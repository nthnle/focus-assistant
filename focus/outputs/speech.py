"""Speak each check-in reply through the speaker, with Piper running on the Pi.

The audio never leaves the Pi: Piper turns the reply into a WAV in memory
and `aplay` plays it on FOCUS_SPEECH_DEVICE (an ALSA device; for a USB
speakerphone use its plughw name from `aplay -L`, so ALSA converts Piper's
22.05 kHz mono to whatever the device wants).

    FOCUS_SPEECH_VOICE   the .onnx voice (default: the one install puts in
                         /opt/focus-assistant/voices)
    FOCUS_SPEECH_DEVICE  ALSA device, default "default"
    FOCUS_SPEECH_LEAD_MS silence played first after the speaker has been
                         quiet, so a USB speaker's amp is awake before the
                         first word (default 300; 0 turns it off)
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
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .. import config, pause, presence, state as st
from ..state import State

log = logging.getLogger(__name__)

DEFAULT_VOICE = "/opt/focus-assistant/voices/en_US-lessac-medium.onnx"
DEFAULT_LEAD_MS = 300
# Quiet for longer than this, and the speaker may have gone to sleep again.
WAKE_AFTER_S = 2.0


def synthesize(voice, text: str) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        voice.synthesize_wav(text, wav)
    return buf.getvalue()


def with_lead_in(wav: bytes, seconds: float) -> bytes:
    """The same WAV with `seconds` of silence in front (unchanged if it isn't a WAV)."""
    try:
        with wave.open(io.BytesIO(wav)) as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
    except (wave.Error, EOFError):
        return wav
    quiet = b"\x80" if params.sampwidth == 1 else b"\0"  # 8-bit WAV is unsigned
    silence = quiet * (int(params.framerate * seconds) * params.nchannels * params.sampwidth)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(silence + frames)
    return out.getvalue()


class Aplay:
    """Plays a WAV through aplay; stop() cuts off whatever is playing (a play press).

    A USB speaker powers its amp down when idle and swallows the first part
    of a second while it wakes, so after a quiet spell each WAV starts with
    `lead_s` of silence. Sentences played back to back get none.
    """

    def __init__(self, device: str, lead_s: float = DEFAULT_LEAD_MS / 1000,
                 clock: Callable[[], float] = time.monotonic):
        self.device = device
        self.lead_s = lead_s
        self.clock = clock
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        self._ended_at: float | None = None

    def __call__(self, wav: bytes) -> None:
        self._stopped = False
        idle = self._ended_at is None or self.clock() - self._ended_at > WAKE_AFTER_S
        if idle and self.lead_s > 0:
            wav = with_lead_in(wav, self.lead_s)
        self._proc = subprocess.Popen(["aplay", "-q", "-D", self.device, "-"], stdin=subprocess.PIPE)
        try:
            self._proc.communicate(wav, timeout=120)
        except BrokenPipeError:
            pass  # stopped while writing
        finally:
            self._ended_at = self.clock()
        if self._proc.returncode and not self._stopped:
            raise subprocess.CalledProcessError(self._proc.returncode, "aplay")

    def stop(self) -> None:
        self._stopped = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()


def aplay(device: str) -> Aplay:
    return Aplay(device, int(os.environ.get("FOCUS_SPEECH_LEAD_MS", DEFAULT_LEAD_MS)) / 1000)


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
        # For the voice service's timing log (time.monotonic() values, or None).
        self.first_text_at: float | None = None      # first piece of the reply arrived
        self.first_sentence_at: float | None = None  # first whole sentence ready to speak
        self.first_audio_at: float | None = None     # Piper done, first sentence starts playing
        self.spoke = False
        self.stopped = False
        self._buffer = ""
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def feed(self, text: str) -> None:
        if self.first_text_at is None:
            self.first_text_at = time.monotonic()
        self._buffer += text
        *done, self._buffer = _SENTENCE_END.split(self._buffer)
        for sentence in done:
            self._put(sentence)

    def finish(self) -> None:
        """No more text is coming: queue what's left; playback carries on."""
        self._put(self._buffer)
        self._buffer = ""
        self._queue.put(None)

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for playback to end; True once everything has been played (or stopped)."""
        self._worker.join(timeout)
        return not self._worker.is_alive()

    def close(self) -> None:
        """Speak whatever is left and wait until everything has been played."""
        self.finish()
        self.wait()

    def stop(self) -> None:
        """Cut the reply off now: drop unspoken sentences, stop the one playing."""
        self.stopped = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(None)
        self.speech.stop()

    def _put(self, sentence: str) -> None:
        if sentence.strip() and not self.stopped:
            self.spoke = True
            if self.first_sentence_at is None:
                self.first_sentence_at = time.monotonic()
            self._queue.put(sentence.strip())

    def _run(self) -> None:
        while (sentence := self._queue.get()) is not None:
            if self.stopped:
                break
            try:
                wav = synthesize(self.speech.voice, sentence)
                if self.stopped:
                    break
                if self.first_audio_at is None:
                    self.first_audio_at = time.monotonic()
                self.speech.play(wav)
            except Exception:
                log.exception("Could not speak %r", sentence)


class Speech:
    def __init__(self, voice, play: Callable[[bytes], None], cfg: config.Config | None = None):
        self.voice = voice
        self.play = play
        self.cfg = cfg  # set: hold check-ins while you are away (focus.presence)

    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self.play(synthesize(self.voice, text))

    def stream(self) -> SentenceStream:
        return SentenceStream(self)

    def stop(self) -> None:
        """Stop whatever is playing, if the player can (aplay can; saving a WAV can't)."""
        stop = getattr(self.play, "stop", None)
        if stop is not None:
            stop()

    def checkin(self, state: State, slot: str, reply: str) -> None:
        now = datetime.now().astimezone()
        if pause.paused(state, now):
            # Quiet for a meeting: the voice service says it once the pause is over.
            state.checkins[-1]["unheard"] = True
            log.info("Paused %s; holding the %s check-in", pause.describe(state, now), slot)
            return
        if self.cfg and presence.away(self.cfg, now):
            # Nobody to hear it: the voice service says it when you're back.
            state.checkins[-1]["unheard"] = True
            log.info("Away from the desk; holding the %s check-in until you're back", slot)
            return
        self.say(reply)


def create() -> Speech:
    from piper import PiperVoice

    voice_path = os.environ.get("FOCUS_SPEECH_VOICE", DEFAULT_VOICE)
    if not Path(voice_path).is_file():
        raise FileNotFoundError(f"No Piper voice at {voice_path}; see the README's Speech section")
    wav_path = os.environ.get("FOCUS_SPEECH_WAV")
    play = save_to(wav_path) if wav_path else aplay(os.environ.get("FOCUS_SPEECH_DEVICE", "default"))
    return Speech(PiperVoice.load(voice_path), play, config.load())


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
