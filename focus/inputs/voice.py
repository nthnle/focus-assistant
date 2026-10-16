"""Talk to the coach at the desk: press the speakerphone's play button and speak.

    play button -> chime -> record until you stop talking (or press again)
    -> faster-whisper on the Pi -> the same coach as phone chat
    -> Piper speaks the reply through the speaker

The microphone is only open between a press and the end of what you say;
nothing is recorded otherwise, and speech never leaves the Pi (only the
transcribed text goes to the coach). The Anker PowerConf's play button
arrives as KEY_PLAYPAUSE on its "Consumer Control" input device, with the
release right after the press, so it is press-to-talk, not hold-to-talk.
(Its call button sends nothing Linux turns into a key.)

    FOCUS_SPEECH_DEVICE   ALSA device for the mic and the speaker (required)
    FOCUS_VOICE_BUTTON    input device name to look for (default "PowerConf Consumer Control")
    FOCUS_VOICE_KEY       key code to listen for (default 164, KEY_PLAYPAUSE; see evtest)
    FOCUS_VOICE_MODEL     Whisper model (default base.en; tiny.en is faster, less accurate)
    FOCUS_VOICE_LEVEL     fixed speech level if the automatic one misjudges your room

    python -m focus.inputs.voice    the service (what focus-voice runs)
"""

from __future__ import annotations

import array
import io
import logging
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Callable, Iterator

from .. import chat, config
from ..coach import CoachError
from ..config import Config

log = logging.getLogger(__name__)

RATE = 16000                 # what Whisper wants
CHUNK_SECONDS = 0.1
CHUNK_BYTES = int(RATE * CHUNK_SECONDS) * 2   # 16-bit mono
EV_KEY = 1
KEY_PLAYPAUSE = 164
_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux
DEFAULT_MODELS = "/opt/focus-assistant/models"


# --- the button ---------------------------------------------------------

def find_input(name_part: str, sys_root: Path = Path("/sys/class/input")) -> Path:
    for node in sorted(sys_root.glob("event*")):
        name = (node / "device" / "name").read_text(errors="replace").strip()
        if name_part.lower() in name.lower():
            return Path("/dev/input") / node.name
    raise FileNotFoundError(f"No input device matching {name_part!r}; is the speakerphone plugged in?")


def key_presses(stream: io.BufferedReader, code: int = KEY_PLAYPAUSE) -> Iterator[None]:
    while chunk := stream.read(_EVENT.size):
        _, _, type_, key, value = _EVENT.unpack(chunk)
        if type_ == EV_KEY and key == code and value == 1:
            yield None


# --- knowing when you have stopped talking ------------------------------

def level(chunk: bytes) -> float:
    samples = array.array("h", chunk)
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0


class Endpointer:
    """Decides when an utterance is over from per-chunk loudness."""

    def __init__(self, fixed_level: float | None = None, floor: float = 300.0,
                 silence_s: float = 1.2, start_timeout_s: float = 6.0, max_s: float = 20.0):
        self.fixed_level = fixed_level
        self.floor = floor
        self.silence_chunks = round(silence_s / CHUNK_SECONDS)
        self.start_chunks = round(start_timeout_s / CHUNK_SECONDS)
        self.max_chunks = round(max_s / CHUNK_SECONDS)
        self._noise: list[float] = []
        self.threshold = fixed_level
        self.heard = False
        self._quiet = 0
        self._count = 0

    def feed(self, lvl: float) -> str | None:
        """Returns None to keep listening, "done" (speech then a pause) or "nothing"."""
        self._count += 1
        if self.threshold is None:
            # The first 0.3 s, right after the chime, sets the room's noise level.
            self._noise.append(lvl)
            if len(self._noise) >= 3:
                self.threshold = max(self.floor, 3 * sorted(self._noise)[1])
            return None
        if lvl >= self.threshold:
            self.heard = True
            self._quiet = 0
        elif self.heard:
            self._quiet += 1
        if self.heard and self._quiet >= self.silence_chunks:
            return "done"
        if not self.heard and self._count >= self.start_chunks:
            return "nothing"
        if self._count >= self.max_chunks:
            return "done" if self.heard else "nothing"
        return None


def record(device: str, endpointer: Endpointer, stop: Callable[[], bool]) -> bytes | None:
    """Raw 16 kHz mono audio of one utterance, or None if nobody spoke."""
    proc = subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    audio = bytearray()
    try:
        while chunk := proc.stdout.read(CHUNK_BYTES):
            audio += chunk
            verdict = endpointer.feed(level(chunk))
            if stop():
                verdict = "done" if endpointer.heard else "nothing"
            if verdict == "nothing":
                return None
            if verdict == "done":
                return bytes(audio)
        proc.wait(timeout=5)
        err = proc.stderr.read().decode(errors="replace").strip()
        raise RuntimeError(f"arecord failed on {device}: {err or 'no output'}; is the speakerphone connected?")
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


# --- speech to text -----------------------------------------------------

class Transcriber:
    def __init__(self, model: str, models_dir: str):
        from faster_whisper import WhisperModel

        # local_files_only: the service has no business downloading models.
        self.model = WhisperModel(model, device="cpu", compute_type="int8",
                                  download_root=models_dir, local_files_only=True)

    def __call__(self, audio) -> str:
        """`audio` is raw 16-bit mono bytes at 16 kHz, or anything faster-whisper accepts."""
        if isinstance(audio, (bytes, bytearray)):
            import numpy as np
            audio = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1,
                                            condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


# --- chimes -------------------------------------------------------------

def tone(notes: list[tuple[float, float]], rate: int = 22050, volume: float = 0.3) -> bytes:
    """A WAV of (frequency Hz, seconds) notes with short fades, so they don't click."""
    frames = array.array("h")
    for freq, secs in notes:
        n = int(rate * secs)
        fade = min(n // 4, int(rate * 0.01))
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade) if fade else 1.0
            frames.append(int(32767 * volume * env * math.sin(2 * math.pi * freq * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames.tobytes())
    return buf.getvalue()


LISTENING = tone([(660, 0.08), (880, 0.12)])
GOT_IT = tone([(880, 0.08), (660, 0.10)])
NOTHING = tone([(330, 0.25)])


# --- the loop -----------------------------------------------------------

class Voice:
    def __init__(self, cfg: Config, speak, play: Callable[[bytes], None],
                 listen: Callable[[Callable[[], bool]], bytes | None],
                 transcribe: Callable[[bytes], str], ask=chat.run):
        self.cfg = cfg
        self.speak = speak          # text -> spoken (focus.outputs.speech.Speech.say)
        self.play = play            # wav bytes -> played
        self.listen = listen
        self.transcribe = transcribe
        self.ask = ask

    def turn(self, pressed_again: Callable[[], bool]) -> None:
        """One press: listen, understand, answer out loud."""
        self.play(LISTENING)
        audio = self.listen(pressed_again)
        if audio is None:
            self.play(NOTHING)
            return
        self.play(GOT_IT)
        text = self.transcribe(audio)
        if not text:
            self.speak("Sorry, I didn't catch that.")
            return
        log.info("Heard: %s", text)
        try:
            reply = self.ask(self.cfg, text)
        except CoachError as exc:
            log.warning("Voice chat failed: %s", exc)
            reply = f"Sorry, that didn't work. {exc}"
        self.speak(reply)


def run(voice: Voice, presses: Iterator[None]) -> None:
    events: queue.Queue[None] = queue.Queue()

    def pump() -> None:
        for _ in presses:
            events.put(None)
        events.put(None)  # wake the loop so it can notice the device went away

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    def pressed_again() -> bool:
        try:
            events.get_nowait()
            return True
        except queue.Empty:
            return False

    while True:
        events.get()
        if not reader.is_alive():
            raise RuntimeError("Lost the speakerphone's button")
        try:
            voice.turn(pressed_again)
        except Exception:
            log.exception("Voice turn failed")
        # Presses made while it was answering don't queue up another turn.
        while pressed_again():
            pass


def main() -> int:
    from ..outputs import speech

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    device = os.environ.get("FOCUS_SPEECH_DEVICE")
    if not device:
        # "default" is the Pi's headphone jack, which has no microphone.
        print("set FOCUS_SPEECH_DEVICE in focus.env, e.g. plughw:CARD=PowerConf,DEV=0 "
              "(from: aplay -L | grep plughw)", file=sys.stderr)
        return 1
    fixed = os.environ.get("FOCUS_VOICE_LEVEL")
    button = find_input(os.environ.get("FOCUS_VOICE_BUTTON", "PowerConf Consumer Control"))
    log.info("Loading Whisper and Piper")
    transcribe = Transcriber(os.environ.get("FOCUS_VOICE_MODEL", "base.en"),
                             os.environ.get("FOCUS_VOICE_MODELS", DEFAULT_MODELS))
    spoken = speech.create()
    voice = Voice(
        config.load(),
        speak=spoken.say,
        play=spoken.play,
        listen=lambda stop: record(device, Endpointer(float(fixed) if fixed else None), stop),
        transcribe=transcribe,
    )
    key = int(os.environ.get("FOCUS_VOICE_KEY", KEY_PLAYPAUSE))
    log.info("Ready: press the button (key %d on %s) and speak", key, button)
    with open(button, "rb", buffering=0) as stream:
        run(voice, key_presses(stream, key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
