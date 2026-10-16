"""Speak each check-in reply through the speaker, with Piper running on the Pi.

The audio never leaves the Pi: Piper turns the reply into a WAV in memory
and `aplay` plays it on FOCUS_SPEECH_DEVICE (an ALSA device; for a USB
speakerphone use its plughw name from `aplay -L`, so ALSA converts Piper's
22.05 kHz mono to whatever the device wants).

    FOCUS_SPEECH_VOICE   the .onnx voice (default: the one install puts in
                         /opt/focus-assistant/voices)
    FOCUS_SPEECH_DEVICE  ALSA device, default "default"
    FOCUS_SPEECH_WAV     save to this WAV instead of playing (no speaker needed)

    python -m focus.outputs.speech            say the last check-in again
    python -m focus.outputs.speech "Hello."   say something
"""

from __future__ import annotations

import io
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from datetime import date
from pathlib import Path
from typing import Callable

from .. import config, state as st
from ..state import State

log = logging.getLogger(__name__)

DEFAULT_VOICE = "/opt/focus-assistant/voices/en_US-lessac-medium.onnx"


def synthesize(voice, text: str) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        voice.synthesize_wav(text, wav)
    return buf.getvalue()


def aplay(device: str) -> Callable[[bytes], None]:
    def play(wav: bytes) -> None:
        subprocess.run(["aplay", "-q", "-D", device, "-"], input=wav, check=True, timeout=120)
    return play


def save_to(path: str) -> Callable[[bytes], None]:
    def save(wav: bytes) -> None:
        Path(path).write_bytes(wav)
        log.info("Saved speech to %s", path)
    return save


# A sentence ends at . ! or ? followed by whitespace, so "3.5" stays whole.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SentenceStream:
    """Speak text that arrives in pieces, one sentence at a time.

    feed() takes streamed text and queues each finished sentence; a worker
    thread synthesizes and plays them in order, so the first sentence is
    heard while the rest of the reply is still being written.
    """

    def __init__(self, speech: Speech):
        self.speech = speech
        self.first_audio_at: float | None = None
        self.spoke = False
        self._buffer = ""
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def feed(self, text: str) -> None:
        self._buffer += text
        *done, self._buffer = _SENTENCE_END.split(self._buffer)
        for sentence in done:
            self._put(sentence)

    def close(self) -> None:
        """Speak whatever is left and wait until everything has been played."""
        self._put(self._buffer)
        self._buffer = ""
        self._queue.put(None)
        self._worker.join()

    def _put(self, sentence: str) -> None:
        if sentence.strip():
            self.spoke = True
            self._queue.put(sentence.strip())

    def _run(self) -> None:
        while (sentence := self._queue.get()) is not None:
            try:
                wav = synthesize(self.speech.voice, sentence)
                if self.first_audio_at is None:
                    self.first_audio_at = time.monotonic()
                self.speech.play(wav)
            except Exception:
                log.exception("Could not speak %r", sentence)


class Speech:
    def __init__(self, voice, play: Callable[[bytes], None]):
        self.voice = voice
        self.play = play

    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self.play(synthesize(self.voice, text))

    def stream(self) -> SentenceStream:
        return SentenceStream(self)

    def checkin(self, state: State, slot: str, reply: str) -> None:
        self.say(reply)


def create() -> Speech:
    from piper import PiperVoice

    voice_path = os.environ.get("FOCUS_SPEECH_VOICE", DEFAULT_VOICE)
    if not Path(voice_path).is_file():
        raise FileNotFoundError(f"No Piper voice at {voice_path}; see the README's Speech section")
    wav_path = os.environ.get("FOCUS_SPEECH_WAV")
    play = save_to(wav_path) if wav_path else aplay(os.environ.get("FOCUS_SPEECH_DEVICE", "default"))
    return Speech(PiperVoice.load(voice_path), play)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if argv:
        text = " ".join(argv)
    else:
        with st.locked_state(config.load(), today=date.today()) as s:
            if not s.checkins:
                print("No check-in yet today; pass some text to say instead.")
                return 1
            text = s.checkins[-1].get("reply") or ""
    create().say(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
