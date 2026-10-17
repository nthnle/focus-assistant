import array
import io
import sys
import time
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
    # Three chunks of room noise set the threshold, then speech, then 0.8 s (10 chunks) quiet.
    assert feed_all(ep, [100, 120, 110] + [2000] * 10 + [100] * 9) is None
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

        def ask(_cfg, text, on_text, voice):
            assert voice is True  # spoken turns ask for short replies
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


SILENCE = b"\x00\x00" * voice.CHUNK_SAMPLES


class Done(Exception):
    pass


class FakeMic:
    """Hands out silent chunks, then stops the endless loop."""

    def __init__(self, chunks=50):
        self.left, self.cleared = chunks, 0

    def read(self):
        if self.left == 0:
            raise Done
        self.left -= 1
        time.sleep(0.001)  # let the button thread run
        return SILENCE

    def clear(self):
        self.cleared += 1


class FakeWake:
    name = "hey_jarvis"
    last_score = 0.93

    def __init__(self, fire_on):
        self.calls, self.resets, self.fire_on = 0, 0, fire_on

    def heard(self, chunk):
        self.calls += 1
        return self.calls in self.fire_on

    def reset(self):
        self.resets += 1


class CountingVoice:
    def __init__(self):
        self.turns = 0

    def turn(self, pressed_again):
        self.turns += 1


def test_run_stops_when_the_button_goes_away():
    with pytest.raises(RuntimeError, match="Lost"):
        voice.run(CountingVoice(), FakeMic(), iter([]))


def test_wake_word_starts_a_turn_and_is_reset_after():
    v, mic, wake = CountingVoice(), FakeMic(), FakeWake(fire_on={3, 10})
    with pytest.raises(Done):
        voice.run(v, mic, None, wake)
    assert v.turns == 2
    assert wake.resets == 2 and mic.cleared == 2


def test_button_starts_a_turn_without_a_wake_word():
    v = CountingVoice()
    presses = iter([None])  # one press; then the button "goes away"
    with pytest.raises((Done, RuntimeError)):
        voice.run(v, FakeMic(), presses)
    assert v.turns == 1


def _mic(script, **kw):
    return voice.Mic("test", command=[sys.executable, "-c", script], **kw)


def test_mic_reads_whole_chunks_then_reports_arecord_failing():
    mic = _mic(f"import sys; sys.stdout.buffer.write(b'\\x01\\x00' * {voice.CHUNK_SAMPLES * 2}); "
               "sys.stderr.write('device gone')")
    assert mic.read() == b"\x01\x00" * voice.CHUNK_SAMPLES
    assert mic.read() == b"\x01\x00" * voice.CHUNK_SAMPLES
    with pytest.raises(RuntimeError, match="device gone"):
        mic.read()
    with pytest.raises(RuntimeError):  # and keeps failing
        mic.read()


def test_mic_keeps_only_the_newest_audio():
    # 10 chunks numbered 0..9 into a queue that holds 4: the oldest are dropped.
    script = ("import sys\n"
              f"for i in range(10): sys.stdout.buffer.write(bytes([i, 0]) * {voice.CHUNK_SAMPLES})\n")
    mic = _mic(script, keep_s=4 * voice.CHUNK_SECONDS)
    mic.proc.wait(timeout=10)
    time.sleep(0.2)  # let the reader thread finish
    first = mic.read()
    assert first[0] >= 5  # at most four chunks plus the end marker survive
    mic.clear()
    with pytest.raises(RuntimeError):  # clear() keeps the end marker
        mic.read()


def bare_wake(threshold=0.5, frames=3):
    """A WakeWord without loading openWakeWord, to test its frame counting."""
    w = object.__new__(voice.WakeWord)
    w.name, w.threshold, w.frames, w.last_score, w._run = "hey_jarvis", threshold, frames, 0.0, []
    return w


def test_wake_needs_enough_high_frames_in_a_row():
    w = bare_wake()
    assert [w.score(s) for s in [0.9, 0.9, 0.2, 0.9, 0.8]] == [False] * 5   # broken by a low frame
    assert w.score(0.95) is True                                            # third high in a row
    assert w.last_score == 0.95
    assert w.score(0.9) is False                                            # starts counting again


def test_one_loud_frame_is_not_enough():
    w = bare_wake()
    assert not any(w.score(s) for s in [0.0, 1.0, 0.0, 1.0, 0.0])


@pytest.mark.parametrize("phantom", ["You", "you.", "Thank you.", "Thanks for watching!"])
def test_whisper_phantoms_are_ignored(cfg, phantom):
    r = Recorder(cfg, heard=phantom)
    r.voice.turn(lambda: False)
    assert r.asked == [] and r.spoken == []
    assert r.played == [voice.LISTENING, voice.GOT_IT, voice.NOTHING]


def test_real_short_commands_still_go_through(cfg):
    r = Recorder(cfg, heard="You there?")
    r.voice.turn(lambda: False)
    assert r.asked == ["You there?"]
