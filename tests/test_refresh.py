import threading
from datetime import date

import pytest
from conftest import completion, scripted, tool_call

from focus import actions, chat, checkin, coach, config, display, state as st, status


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def _checkin_at(cfg, at, slot="evening", reply="Nothing done yet; start with the inspection."):
    with st.locked_state(cfg) as s:
        s.checkins.append({"slot": slot, "at": at, "reply": reply, "model": "test-model"})


def _done_at(cfg, text, at):
    with st.locked_state(cfg) as s:
        p = actions.add_priority(s, text)
        p.done_at = at


def test_since_note_lists_what_was_done_after_the_check_in(cfg):
    _done_at(cfg, "Email Sam", "2026-10-18T15:00:00-05:00")
    _checkin_at(cfg, "2026-10-18T16:20:00-05:00")
    assert display.since_note(st.peek(cfg)) == ""

    _done_at(cfg, "Mow the lawn", "2026-10-18T21:41:00-05:00")
    assert display.since_note(st.peek(cfg)) == "Since 16:20: done Mow the lawn"
    assert "Since 16:20: done Mow the lawn" in display.render_html(st.peek(cfg), "2026-10-18T21:42:00-05:00")

    # Once the coach has rewritten the check-in, the note has nothing left to add.
    with st.locked_state(cfg) as s:
        s.checkins[-1]["updated_at"] = "2026-10-18T21:42:00-05:00"
    assert display.since_note(st.peek(cfg)) == ""


def test_refresh_rewrites_the_latest_check_in(cfg, monkeypatch):
    _checkin_at(cfg, st.now_iso())
    seen = {}

    def fake_ask(_cfg, payload, client=None):
        seen.update(payload)
        return coach.CoachReply("The lawn is done; the inspection is first tomorrow.", "test-model", 900, 30)

    monkeypatch.setattr(coach, "ask", fake_ask)
    monkeypatch.setenv("FOCUS_MODEL_PRICE", "0.15,0.60")
    assert checkin.refresh(cfg) == "The lawn is done; the inspection is first tomorrow."

    assert seen["checkin"] == "evening"
    assert set(seen["rewrite_of"]) == {"written_at", "done_since"}
    assert seen["earlier_checkins_today"] == []   # the stale reply isn't there to copy
    s = st.peek(cfg)
    last = s.checkins[-1]
    assert len(s.checkins) == 1
    assert last["reply"] == "The lawn is done; the inspection is first tomorrow."
    assert last["first_reply"] == "Nothing done yet; start with the inspection."
    assert last["updated_at"] >= last["at"]
    assert last["rewrites"][0]["tokens"] == {"in": 900, "out": 30}
    # Its cost shows up with the check-ins in `focus status`.
    checkins_row = status.usage_today(cfg, s)[0]
    assert checkins_row[:4] == ("check-ins", 1, 900, 30)
    assert checkins_row[4] == pytest.approx((900 * 0.15 + 30 * 0.60) / 1e6)


def test_a_rewrite_that_repeats_the_old_check_in_is_not_kept(cfg, monkeypatch):
    _checkin_at(cfg, "2026-10-18T16:20:00-05:00")
    _done_at(cfg, "Mow the lawn", "2026-10-18T21:41:00-05:00")
    seen = {}

    def copying_ask(_cfg, payload, client=None):
        seen.update(payload)
        return coach.CoachReply("Nothing done yet;  start with the inspection.", "test-model", 1, 1)

    monkeypatch.setattr(coach, "ask", copying_ask)
    assert checkin.refresh(cfg) is None
    assert seen["rewrite_of"]["done_since"] == ["Mow the lawn"]
    s = st.peek(cfg)
    assert "updated_at" not in s.checkins[-1]
    assert display.since_note(s) == "Since 16:20: done Mow the lawn"   # the note stays up


def test_refresh_needs_a_check_in(cfg, monkeypatch):
    monkeypatch.setattr(coach, "ask", lambda *a, **k: pytest.fail("no call without a check-in"))
    assert checkin.refresh(cfg) is None


def test_refresh_leaves_fridays_weekly_review_alone(cfg, monkeypatch):
    friday = date(2026, 10, 16)
    monkeypatch.setattr(coach, "ask", lambda *a, **k: pytest.fail("the weekly review stays"))
    with st.locked_state(cfg, today=friday) as s:
        s.checkins.append({"slot": "evening", "at": st.now_iso(), "reply": "A good week."})
    assert checkin.refresh(cfg, today=friday) is None
    with st.locked_state(cfg, today=friday) as s:
        assert s.checkins[-1]["reply"] == "A good week."


def test_refresh_gives_way_to_a_newer_check_in(cfg, monkeypatch):
    _checkin_at(cfg, "2026-10-18T13:00:00-05:00", slot="midday", reply="Midday.")

    def slow_ask(_cfg, payload, client=None):
        # The evening check-in lands while the rewrite is still with the model.
        _checkin_at(cfg, "2026-10-18T17:30:00-05:00", reply="Evening.")
        return coach.CoachReply("Rewritten midday.", "test-model", 1, 1)

    monkeypatch.setattr(coach, "ask", slow_ask)
    assert checkin.refresh(cfg) is None
    assert [c["reply"] for c in st.peek(cfg).checkins] == ["Midday.", "Evening."]


def test_refresher_makes_one_call_for_a_burst_of_changes(cfg):
    calls = []
    ran = threading.Event()

    def rewrite(_cfg):
        calls.append(1)
        ran.set()
        return "x"

    refresh = checkin.Refresher(cfg, delay_s=0.05, rewrite=rewrite)
    refresh()
    refresh()   # done, then undone with a double press
    assert ran.wait(2)
    refresh._timer.join(2)
    assert calls == [1]


def test_refresher_survives_the_coach_being_offline(cfg):
    ran = threading.Event()

    def rewrite(_cfg):
        ran.set()
        raise coach.CoachError("The coach is offline")

    refresh = checkin.Refresher(cfg, delay_s=0.01, rewrite=rewrite)
    refresh()
    assert ran.wait(2)
    refresh._timer.join(2)


def test_chat_asks_for_a_rewrite_only_after_done_or_reopen(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Car inspection")
    asked = []
    client, _ = scripted(
        completion(tool_calls=[tool_call("add_priority", {"text": "Lawn"})]),
        completion("Added the lawn."),
        completion(tool_calls=[tool_call("complete_priority", {"id": 2})]),
        completion("Marked the lawn done."),
    )
    chat.run(cfg, "add the lawn", client=client, on_done_change=lambda: asked.append(1))
    assert asked == []
    chat.run(cfg, "the lawn is done", client=client, on_done_change=lambda: asked.append(1))
    assert asked == [1]
