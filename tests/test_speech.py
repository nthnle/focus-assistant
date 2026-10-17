import io
import wave

from focus.outputs import speech
from focus.state import State


class FakeVoice:
    def __init__(self):
        self.texts = []

    def synthesize_wav(self, text, wav):
        self.texts.append(text)
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 100)


def test_checkin_speaks_the_reply_as_wav():
    played = []
    voice = FakeVoice()
    out = speech.Speech(voice, played.append)
    out.checkin(State(date="2026-10-15"), "morning", "Start with the parser.")

    assert voice.texts == ["Start with the parser."]
    with wave.open(io.BytesIO(played[0])) as wav:
        assert wav.getframerate() == 22050
        assert wav.getnframes() == 100


def test_empty_reply_is_silent():
    played = []
    speech.Speech(FakeVoice(), played.append).say("  ")
    assert played == []


def test_stream_speaks_each_sentence_as_it_completes():
    played = []
    voice = FakeVoice()
    out = speech.Speech(voice, played.append).stream()
    for piece in ["Start with ", "the parser. It's 3.5 hours", " of work! Then email", " Sam."]:
        out.feed(piece)
    out.close()
    assert voice.texts == ["Start with the parser.", "It's 3.5 hours of work!", "Then email Sam."]
    assert len(played) == 3 and out.spoke and out.first_audio_at is not None


def test_stream_with_nothing_is_silent():
    played = []
    out = speech.Speech(FakeVoice(), played.append).stream()
    out.close()
    assert played == [] and not out.spoke


class FakeAplay:
    """Stands in for the aplay process."""
    made = []

    def __init__(self, cmd, stdin=None):
        self.cmd, self.returncode, self.fed, self.terminated = cmd, None, None, False
        FakeAplay.made.append(self)

    def communicate(self, data, timeout=None):
        self.fed = data
        self.returncode = -15 if self.terminated else 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


def test_aplay_command(monkeypatch):
    monkeypatch.setattr(speech.subprocess, "Popen", FakeAplay)
    speech.aplay("plughw:CARD=PowerConf,DEV=0")(b"RIFF")
    proc = FakeAplay.made[-1]
    assert proc.cmd == ["aplay", "-q", "-D", "plughw:CARD=PowerConf,DEV=0", "-"]
    assert proc.fed == b"RIFF"


def test_aplay_stop_terminates_and_is_not_an_error(monkeypatch):
    monkeypatch.setattr(speech.subprocess, "Popen", FakeAplay)
    player = speech.aplay("x")
    player._proc = FakeAplay(["aplay"])
    player.stop()
    assert player._proc.terminated


def test_stream_stop_drops_unspoken_sentences():
    played = []
    voice = FakeVoice()
    sp = speech.Speech(voice, played.append)
    out = sp.stream()
    out.stop()
    out.feed("One. Two. Three.")
    out.finish()
    assert out.wait(1)
    assert played == [] and out.stopped


def test_stream_records_when_text_sentence_and_audio_arrive():
    out = speech.Speech(FakeVoice(), lambda wav: None).stream()
    out.feed("Done")
    assert out.first_text_at is not None and out.first_sentence_at is None
    out.feed(". Next")
    out.close()
    assert out.first_text_at <= out.first_sentence_at <= out.first_audio_at
