import json
from datetime import datetime, timedelta, timezone

import pytest

from focus import blocks, config, presence, state as st, stretch
from focus.inputs import voice
from focus.outputs import speech

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 12, 13, 50, tzinfo=TZ)  # a Monday, in the afternoon block


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def ago(minutes):
    return (NOW - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def state_with(*events):
    s = st.State(date=NOW.date().isoformat())
    s.events = [{"at": at, "kind": kind} for kind, at in events]
    return s


def heard_sound(cfg, minutes_ago):
    presence.sound_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    presence.sound_path(cfg).write_text(json.dumps({"sound_at": ago(minutes_ago)}))


def test_phone_out_since_follows_the_last_dock_event():
    assert presence.phone_out_since(state_with()) is None
    assert presence.phone_out_since(state_with((st.PHONE_OUT, ago(20)))) == NOW - timedelta(minutes=20)
    assert presence.phone_out_since(state_with((st.PHONE_OUT, ago(20)), (st.PHONE_IN, ago(5)))) is None


def test_away_needs_the_phone_gone_and_the_desk_quiet(cfg):
    gone = state_with((st.PHONE_OUT, ago(20)))
    assert presence.away(cfg, gone, NOW)                      # no sound reading: the dock decides
    heard_sound(cfg, 30)
    assert presence.away(cfg, gone, NOW)                      # quiet for 30 min
    heard_sound(cfg, 3)
    assert not presence.away(cfg, gone, NOW)                  # typing 3 min ago: still here


def test_not_away_when_the_phone_is_docked_or_only_just_left(cfg):
    assert not presence.away(cfg, state_with(), NOW)
    assert not presence.away(cfg, state_with((st.PHONE_OUT, ago(4))), NOW)
    assert not presence.away(cfg, state_with((st.PHONE_OUT, ago(30)), (st.PHONE_IN, ago(1))), NOW)


def test_thresholds_come_from_the_environment(cfg, monkeypatch):
    monkeypatch.setenv("FOCUS_AWAY_PHONE_MIN", "3")
    assert presence.away(cfg, state_with((st.PHONE_OUT, ago(4))), NOW)


def test_sound_tracker_ignores_steady_noise_and_records_sounds(cfg):
    clock = [NOW]
    t = presence.SoundTracker(cfg, clock=lambda: clock[0])
    for _ in range(50):
        t.feed(150)                    # fan hum
    assert presence.last_sound(cfg) is None
    t.feed(3000)                       # a chair, a keyboard
    assert presence.last_sound(cfg) == NOW
    clock[0] = NOW + timedelta(seconds=10)
    t.feed(3000)                       # written at most every 30 s
    assert presence.last_sound(cfg) == NOW
    clock[0] = NOW + timedelta(seconds=40)
    t.feed(3000)
    assert presence.last_sound(cfg) == NOW + timedelta(seconds=40)


class FakeVoice:
    def synthesize_wav(self, text, wav):
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00")


def checkin_state(cfg, *events):
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.events = [{"at": at, "kind": kind} for kind, at in events]
        s.checkins.append({"slot": "midday", "at": ago(50), "reply": "Back to the report."})
        return s


def test_spoken_checkin_is_held_while_away(cfg, monkeypatch):
    monkeypatch.setattr(speech, "datetime", type("D", (), {"now": staticmethod(lambda: NOW)}))
    s = checkin_state(cfg, (st.PHONE_OUT, ago(30)))
    played = []
    speech.Speech(FakeVoice(), played.append, cfg).checkin(s, "midday", "Back to the report.")
    assert played == []
    assert s.checkins[-1]["unheard"] is True


def test_spoken_checkin_plays_when_present(cfg, monkeypatch):
    monkeypatch.setattr(speech, "datetime", type("D", (), {"now": staticmethod(lambda: NOW)}))
    s = checkin_state(cfg)
    played = []
    speech.Speech(FakeVoice(), played.append, cfg).checkin(s, "midday", "Back to the report.")
    assert len(played) == 1 and "unheard" not in s.checkins[-1]


def test_held_checkin_is_said_once_when_back(cfg):
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.events = [{"at": ago(60), "kind": st.PHONE_OUT}]
        s.checkins.append({"slot": "midday", "at": ago(50), "reply": "Back to the report.", "unheard": True})
    said = []
    assert not voice.replay_missed(cfg, said.append, NOW)    # still away
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.events.append({"at": ago(0), "kind": st.PHONE_IN})  # phone back on the dock
    assert voice.replay_missed(cfg, said.append, NOW)
    assert said == ["You missed the midday check-in. It said: Back to the report."]
    assert not voice.replay_missed(cfg, said.append, NOW)    # only once


def test_stretch_reminder_skipped_while_away(cfg):
    local = datetime(2026, 10, 12, 13, 50)  # naive, like the timer's datetime.now()
    out = (local.astimezone() - timedelta(minutes=30)).isoformat(timespec="seconds")
    with st.locked_state(cfg, today=local.date()) as s:
        s.events = [{"at": out, "kind": st.PHONE_OUT}]
    said = []
    focus = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)
    assert not stretch.run(cfg, local, said.append, focus, write=lambda c, n: None)
    assert said == []


def test_run_feeds_chunks_and_runs_idle_between_turns():
    class Mic:
        def __init__(self):
            self.left, self.cleared = 5, 0

        def read(self):
            if not self.left:
                raise StopIteration
            self.left -= 1
            return b"\x00\x00" * voice.CHUNK_SAMPLES

        def clear(self):
            self.cleared += 1

    seen, idles, mic = [], [], Mic()
    with pytest.raises(StopIteration):
        voice.run(object(), mic, None, None, on_chunk=seen.append,
                  idle=lambda: idles.append(1), idle_every_s=0)
    assert len(seen) == 5 and len(idles) == 5 and mic.cleared == 5
