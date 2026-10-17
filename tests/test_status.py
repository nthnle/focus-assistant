from datetime import datetime, timedelta, timezone

import pytest

from focus import actions, config, state as st, status, stretch

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 12, 14, 5, tzinfo=TZ)  # Monday afternoon, in a block


@pytest.fixture(autouse=True)
def priced(monkeypatch):
    monkeypatch.setenv("FOCUS_MODEL_PRICE", "2,10")
    monkeypatch.setenv("FOCUS_CHAT_MODEL_PRICE", "0.10,0.50")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def at(h, m):
    return NOW.replace(hour=h, minute=m).isoformat(timespec="seconds")


def busy_day():
    s = st.State(date=NOW.date().isoformat())
    a = actions.add_priority(s, "Write spec")
    actions.add_priority(s, "Email Sam")
    actions.complete_priority(s, a.id)
    s.checkins = [
        {"slot": "morning", "at": at(9, 0), "reply": "x", "model": "test-model",
         "tokens": {"in": 1000, "out": 100}},
        {"slot": "midday", "at": at(13, 0), "reply": "y", "model": "test-model",
         "tokens": {"in": 1000, "out": 100}, "unheard": True},
    ]
    s.chat = [{"at": at(10, 0), "user": "hi", "reply": "hey", "voice": True,
               "model": "test-chat-model", "tokens": {"in": 2000, "out": 50}}]
    s.events = [
        {"at": at(10, 50), "kind": stretch.STRETCH, "detail": "coach",
         "usage": {"model": "test-chat-model", "in": 300, "out": 20}},
        {"at": at(11, 50), "kind": stretch.STRETCH, "detail": "fixed"},
        {"at": at(13, 42), "kind": st.PHONE_OUT},
    ]
    return s


def test_cost():
    assert status.cost("test-model", 1_000_000, 100_000) == pytest.approx(3.0)
    assert status.cost("test-chat-model", 1_000_000, 1_000_000) == pytest.approx(0.6)
    assert status.cost("some-other-model", 1, 1) is None


def test_unset_or_bad_price_is_unknown(monkeypatch):
    monkeypatch.delenv("FOCUS_MODEL_PRICE")
    monkeypatch.setenv("FOCUS_CHAT_MODEL_PRICE", "cheap")
    assert status.cost("test-model", 1, 1) is None
    assert status.cost("test-chat-model", 1, 1) is None


def test_usage_today_groups_and_prices(cfg):
    rows = {name: rest for name, *rest in status.usage_today(cfg, busy_day())}
    assert rows["check-ins"] == [2, 2000, 200, pytest.approx(0.006)]
    assert rows["chat"] == [1, 2000, 50, pytest.approx(0.000225)]
    assert rows["stretch"] == [1, 300, 20, pytest.approx(0.00004)]


def test_report_shows_the_day(cfg):
    fake = {("is-active", "focus-voice"): "inactive"}

    def systemctl(*args):
        if args[0] == "show":
            return "Mon 2026-10-12 14:50:00 CDT"
        return fake.get(args, "active")

    text = status.report(cfg, busy_day(), NOW, systemctl=systemctl)
    assert "focus-stretch.timer  active   next Mon 2026-10-12 14:50:00 CDT" in text
    assert "focus-voice          inactive" in text
    assert "1 open, 1 done" in text
    assert "midday 13:00 (held, away)" in text
    assert "Focus block  yes, 13:00-17:30" in text
    assert "off the dock since 13:42" in text
    assert "Presence     away" in text           # phone out 23 min, no sound reading
    assert "2 reminders (1 by the coach, 1 fixed)" in text
    assert "1 messages (1 by voice)" in text
    assert "total      $0.0063" in text


def test_report_without_systemd(cfg):
    text = status.report(cfg, st.State(date=NOW.date().isoformat()), NOW, systemctl=lambda *a: None)
    assert "systemctl not available" in text
    assert "total      $0.0000" in text


def test_saved_costs_count_even_without_prices_here(cfg, monkeypatch):
    """`focus status` runs from a shell that can't read focus.env: it must use
    what the services saved, and skip offline check-ins (no call was made)."""
    monkeypatch.delenv("FOCUS_MODEL_PRICE")
    monkeypatch.delenv("FOCUS_CHAT_MODEL_PRICE")
    s = st.State(date=NOW.date().isoformat())
    s.checkins = [
        {"slot": "morning", "at": at(9, 0), "reply": "x", "model": "test-model",
         "tokens": {"in": 1000, "out": 100}, "usd": 0.0003},
        {"slot": "midday", "at": at(13, 0), "reply": "The coach is offline.", "model": "offline"},
    ]
    s.chat = [{"at": at(10, 0), "user": "hi", "reply": "hey", "voice": False,
               "model": "test-chat-model", "tokens": {"in": 500, "out": 20}, "usd": 0.0001}]
    rows = {name: rest for name, *rest in status.usage_today(cfg, s)}
    assert rows["check-ins"] == [1, 1000, 100, pytest.approx(0.0003)]
    assert rows["chat"] == [1, 500, 20, pytest.approx(0.0001)]
