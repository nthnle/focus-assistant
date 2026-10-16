import array
import io
import wave

import pytest

from focus import config
from focus.coach import CoachError
from focus.inputs import voice


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def event(type_, code, value):
    return voice._EVENT.pack(0, 0, type_, code, value)


def test_key_presses_counts_only_the_press():
    raw = b"".join([
        event(4, 4, 0xC00CD),                    # MSC_SCAN
        event(voice.EV_KEY, voice.KEY_PLAYPAUSE, 1),
        event(0, 0, 0),                          # SYN_REPORT
        event(voice.EV_KEY, voice.KEY_PLAYPAUSE, 0),
        event(voice.EV_KEY, 115, 1),             # volume up
        event(voice.EV_KEY, voice.KEY_PLAYPAUSE, 1),
    ])
    assert len(list(voice.key_presses(io.BytesIO(raw)))) == 2


def test_find_input_matches_by_name(tmp_path):
    for n, name in [(4, "Anker Anker PowerConf Consumer Control"), (5, "Anker Anker PowerConf")]:
        (tmp_path / f"event{n}" / "device").mkdir(parents=True)
        (tmp_path / f"event{n}" / "device" / "name").write_text(name + "\n")
    assert voice.find_input("PowerConf Consumer Control", tmp_path).name == "event4"
    with pytest.raises(FileNotFoundError):
        voice.find_input("Jabra", tmp_path)


def test_level():
    assert voice.level(array.array("h", [0] * 1600).tobytes()) == 0
    assert voice.level(array.array("h", [1000, -1000] * 800).tobytes()) == pytest.approx(1000)


def feed_all(ep, levels):
    for lvl in levels:
        verdict = ep.feed(lvl)
        if verdict:
            return verdict
    return None


def test_endpointer_speech_then_pause():
    ep = voice.Endpointer()
    # 0.3 s of room noise sets the threshold, then 1 s of speech, then 0.8 s quiet.
    assert feed_all(ep, [100, 120, 110] + [2000] * 10 + [100] * 7) is None
    assert ep.feed(100) == "done"
    assert ep.threshold == 330  # 3 x the median noise level (110)


def test_endpointer_threshold_has_a_floor():
    ep = voice.Endpointer()
    feed_all(ep, [5, 5, 5])
    assert ep.threshold == ep.floor


def test_endpointer_nobody_speaks():
    ep = voice.Endpointer(start_timeout_s=1.0)
    assert feed_all(ep, [100] * 3 + [100] * 20) == "nothing"


def test_endpointer_max_length():
    ep = voice.Endpointer(fixed_level=500, max_s=2.0)
    assert feed_all(ep, [2000] * 30) == "done"


def test_tone_is_a_wav():
    with wave.open(io.BytesIO(voice.LISTENING)) as w:
        assert w.getframerate() == 22050 and w.getnframes() > 0


class FakeStream:
    def __init__(self, sink):
        self.sink, self.parts, self.spoke, self.first_audio_at = sink, [], False, None

    def feed(self, text):
        self.parts.append(text)
        self.spoke = True

    def close(self):
        self.sink.append("".join(self.parts))


class Recorder:
    def __init__(self, cfg, audio=b"\x00\x00" * 100, heard="add buy coffee filters",
                 reply="Added buy coffee filters.", fail=None):
        self.spoken, self.played, self.asked = [], [], []

        def ask(_cfg, text, on_text):
            self.asked.append(text)
            if fail:
                raise CoachError(fail)
            for i, word in enumerate(reply.split(" ")):  # arrives in pieces, like a stream
                on_text(word if i == 0 else " " + word)
            return reply

        self.voice = voice.Voice(
            cfg, speak=self.spoken.append, stream=lambda: FakeStream(self.spoken),
            play=self.played.append, listen=lambda stop: audio, transcribe=lambda a: heard, ask=ask,
        )


def test_turn_hears_asks_and_speaks(cfg):
    r = Recorder(cfg)
    r.voice.turn(lambda: False)
    assert r.played == [voice.LISTENING, voice.GOT_IT]
    assert r.asked == ["add buy coffee filters"]
    assert r.spoken == ["Added buy coffee filters."]


def test_turn_with_silence_just_chimes(cfg):
    r = Recorder(cfg, audio=None)
    r.voice.turn(lambda: False)
    assert r.played == [voice.LISTENING, voice.NOTHING]
    assert r.asked == [] and r.spoken == []


def test_turn_with_nothing_understood(cfg):
    r = Recorder(cfg, heard="")
    r.voice.turn(lambda: False)
    assert r.asked == []
    assert r.spoken == ["Sorry, I didn't catch that."]


def test_turn_speaks_coach_errors(cfg):
    r = Recorder(cfg, fail="Daily chat limit (50) reached")
    r.voice.turn(lambda: False)
    assert r.spoken == ["Sorry, that didn't work. Daily chat limit (50) reached"]


def test_run_stops_when_the_button_goes_away(cfg):
    r = Recorder(cfg)
    with pytest.raises(RuntimeError, match="Lost"):
        voice.run(r.voice, iter([]))
