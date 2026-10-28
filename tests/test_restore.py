import json
from datetime import date

import pytest

from focus import actions, chat, cli, coach, config, display, state as st
from focus.inputs import page


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def test_a_removed_item_comes_back_as_it_was():
    s = st.State(date="2026-10-27")
    a = actions.add_priority(s, "Book the dentist", date(2026, 10, 30))
    a.carried_days = 2
    b = actions.add_priority(s, "Email Sam")
    actions.drop_priority(s, a.id)
    actions.drop_priority(s, b.id)
    assert s.priorities == [] and [d["text"] for d in s.dropped] == ["Book the dentist", "Email Sam"]
    back = actions.restore_priority(s, a.id)
    assert (back.id, back.text, back.on, back.carried_days) == (a.id, "Book the dentist", "2026-10-30", 2)
    assert s.priorities == [back] and [d["text"] for d in s.dropped] == ["Email Sam"]
    with pytest.raises(st.StateError):
        actions.restore_priority(s, a.id)                  # only once


def test_a_restored_item_never_shares_an_id():
    s = st.State(date="2026-10-27")
    p = actions.add_priority(s, "Email Sam")
    actions.drop_priority(s, p.id)
    s.priorities.append(st.Priority(id=p.id, text="Something else"))   # an id reused somehow
    back = actions.restore_priority(s, p.id)
    assert back.id != p.id and len({q.id for q in s.priorities}) == 2


def test_removed_items_are_kept_a_week(cfg):
    with st.locked_state(cfg, today=date(2026, 10, 20)) as s:
        actions.drop_priority(s, actions.add_priority(s, "Old").id)
        s.dropped[-1]["dropped_at"] = "2026-10-20T10:00:00-05:00"
    with st.locked_state(cfg, today=date(2026, 10, 26)) as s:
        actions.drop_priority(s, actions.add_priority(s, "Newer").id)
        s.dropped[-1]["dropped_at"] = "2026-10-26T10:00:00-05:00"
        assert [d["text"] for d in s.dropped] == ["Old", "Newer"]
    assert [d["text"] for d in st.peek(cfg, today=date(2026, 10, 28)).dropped] == ["Newer"]


def test_a_state_file_from_before_still_loads(cfg):
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Email Sam")
    data = json.loads(cfg.state_path.read_text())
    del data["dropped"]
    cfg.state_path.write_text(json.dumps(data))
    assert st.peek(cfg).dropped == []


def test_the_coach_can_bring_one_back(cfg):
    with st.locked_state(cfg) as s:
        p = actions.add_priority(s, "Book the dentist")
        actions.drop_priority(s, p.id)
        payload = coach.build_payload(s, "chat", st.now_iso())
        assert "recently_removed" not in coach.build_payload(s, "morning", st.now_iso())
    assert [r["text"] for r in payload["recently_removed"]] == ["Book the dentist"]
    assert any(t["function"]["name"] == "restore_priority" for t in chat.TOOLS)
    text, is_error = chat._run_tool(cfg, "restore_priority", json.dumps({"id": p.id}))
    assert not is_error
    assert [q.text for q in st.peek(cfg).priorities] == ["Book the dentist"]


def test_the_status_page_offers_to_restore(cfg):
    with st.locked_state(cfg) as s:
        p = actions.add_priority(s, "Fix <the> lamp")
        actions.drop_priority(s, p.id)
    html = display.editor(st.peek(cfg))
    assert "Removed this week" in html
    assert f'<li class="removed" data-id="{p.id}"><span class="t">Fix &lt;the&gt; lamp</span>' in html
    assert '<button data-op="restore">Restore</button>' in html
    assert page.edit_list(cfg, {"op": "restore", "id": p.id}).startswith("restore ")
    assert [q.text for q in st.peek(cfg).priorities] == ["Fix <the> lamp"]
    assert "Removed this week" not in display.editor(st.peek(cfg))


def test_cli_removed_and_restore(cfg, capsys):
    for text in ("Write the spec", "Email Sam", "Book dentist"):
        cli.main(["add", text])
    cli.main(["drop", "1"])
    cli.main(["drop", "1"])
    capsys.readouterr()
    cli.main(["removed"])
    out = capsys.readouterr().out
    assert out.index("1. Email Sam") < out.index("2. Write the spec")      # newest first
    cli.main(["restore", "2"])
    assert "Restored Write the spec" in capsys.readouterr().out
    cli.main(["restore"])                                                  # the latest
    assert [p.text for p in st.peek(cfg).priorities] == ["Book dentist", "Write the spec", "Email Sam"]
    assert cli.main(["restore"]) == 1                                      # nothing left
