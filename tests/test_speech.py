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


def test_aplay_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(speech.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    speech.aplay("plughw:CARD=PowerConf,DEV=0")(b"RIFF")
    assert seen["cmd"] == ["aplay", "-q", "-D", "plughw:CARD=PowerConf,DEV=0", "-"]
    assert seen["input"] == b"RIFF"
