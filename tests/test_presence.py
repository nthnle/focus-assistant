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


def camera_sees(cfg, label, minutes_ago, sees_away=True, written_ago=0):
    presence.record_camera(cfg, label, NOW - timedelta(minutes=minutes_ago),
                           NOW - timedelta(minutes=written_ago), sees_away)


def test_away_once_the_camera_has_seen_the_empty_desk_for_a_while(cfg):
    camera_sees(cfg, "work", 20)
    assert not presence.away(cfg, NOW)
    camera_sees(cfg, "phone", 20)
    assert not presence.away(cfg, NOW)
    camera_sees(cfg, "away", 3)
    assert not presence.away(cfg, NOW)      # empty for 3 min: a quick errand
    camera_sees(cfg, "away", 6)
    assert presence.away(cfg, NOW)


def test_without_a_running_camera_you_count_as_here(cfg):
    assert not presence.away(cfg, NOW)      # no camera at all
    camera_sees(cfg, "away", 30, written_ago=2)           # last wrote 2 min ago: it stopped
    assert presence.camera_seen(cfg, NOW) is None
    assert not presence.away(cfg, NOW)
    presence.clear_camera(cfg)
    assert presence.camera_seen(cfg, NOW) is None


def test_a_model_without_away_never_says_away(cfg):
    camera_sees(cfg, "work", 40, sees_away=False)
    assert not presence.away(cfg, NOW)
    assert presence.back(cfg, NOW)


def test_back_waits_for_the_camera_only_while_its_service_runs(cfg):
    assert presence.back(cfg, NOW)                        # no camera service: nothing to wait for
    camera_sees(cfg, presence.OFF, 10)
    assert presence.latest(cfg, NOW) is not None and presence.camera_seen(cfg, NOW) is None
    assert not presence.back(cfg, NOW)                    # off for now: it comes on for a held check-in
    camera_sees(cfg, "away", 1)
    assert not presence.back(cfg, NOW)
    camera_sees(cfg, "work", 0)
    assert presence.back(cfg, NOW)
    camera_sees(cfg, "away", 30, written_ago=5)           # the service stopped long ago
    assert presence.back(cfg, NOW)                        # don't hold it forever


def test_an_off_camera_is_not_away(cfg):
    camera_sees(cfg, presence.OFF, 60)
    assert not presence.away(cfg, NOW)


def test_thresholds_come_from_the_environment(cfg, monkeypatch):
    camera_sees(cfg, "away", 3)
    monkeypatch.setenv("FOCUS_AWAY_CAMERA_MIN", "2")
    assert presence.away(cfg, NOW)


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
    camera_sees(cfg, "away", 30)
    s = checkin_state(cfg)
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
    camera_sees(cfg, "away", 60)
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.checkins.append({"slot": "midday", "at": ago(50), "reply": "Back to the report.", "unheard": True})
    said = []
    assert not voice.replay_missed(cfg, said.append, NOW)    # still away
    camera_sees(cfg, "work", 0)                              # back in the chair
    assert voice.replay_missed(cfg, said.append, NOW)
    assert said == ["You missed the midday check-in. It said: Back to the report."]
    assert not voice.replay_missed(cfg, said.append, NOW)    # only once


def test_no_held_checkin_means_no_lock_and_no_rewrite(cfg):
    """The voice service asks every 30 s; state.json mustn't be rewritten each time."""
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.checkins.append({"slot": "midday", "at": ago(50), "reply": "Back to the report."})
    before = cfg.state_path.stat().st_mtime_ns
    assert not voice.replay_missed(cfg, [].append, NOW)
    assert cfg.state_path.stat().st_mtime_ns == before


def test_between_turns_says_a_held_checkin_before_any_nudge(cfg, monkeypatch):
    from focus import nudge
    nudged = []
    monkeypatch.setattr(nudge, "run", lambda *a: nudged.append(a) or True)
    with st.locked_state(cfg, today=NOW.date()) as s:
        s.checkins.append({"slot": "midday", "at": ago(50), "reply": "Back to the report.", "unheard": True})
    said = []
    voice.between_turns(cfg, said.append, NOW, blocks.parse_blocks(blocks.DEFAULT_BLOCKS))
    assert said == ["You missed the midday check-in. It said: Back to the report."] and nudged == []
    voice.between_turns(cfg, said.append, NOW, blocks.parse_blocks(blocks.DEFAULT_BLOCKS))
    assert len(nudged) == 1                                # the next tick, nothing held: the nudge


def test_stretch_reminder_skipped_while_away(cfg):
    local = datetime(2026, 10, 12, 13, 50)  # naive, like the timer's datetime.now()
    presence.record_camera(cfg, "away", local.astimezone() - timedelta(minutes=30),
                           local.astimezone(), sees_away=True)
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

    idles, mic = [], Mic()
    with pytest.raises(StopIteration):
        voice.run(object(), mic, None, None, idle=lambda: idles.append(1), idle_every_s=0)
    assert len(idles) == 5 and mic.cleared == 5
