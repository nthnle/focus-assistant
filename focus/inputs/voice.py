"""Talk to the coach at the desk: say "Hey Jarvis" or press the play button, then speak.

    wake word or play button -> chime -> record until you stop talking
    (or press again) -> faster-whisper on the Pi -> the same coach as phone
    chat -> Piper speaks the reply through the speaker

With a wake word set, the microphone is always on, but that audio only goes
through the wake-word model on the Pi and is thrown away; a turn's audio is
kept only after the wake word or a press, until you stop talking. Speech
never leaves the Pi (only the transcribed text goes to the coach). Without a
wake word, only the button starts a turn. The Anker PowerConf's play button
arrives as KEY_PLAYPAUSE on its "Consumer Control" input device, with the
release right after the press, so it is press-to-talk, not hold-to-talk.
(Its call button sends nothing Linux turns into a key.)

    FOCUS_SPEECH_DEVICE   ALSA device for the mic and the speaker (required)
    FOCUS_VOICE_BUTTON    input device name to look for (default "PowerConf Consumer Control")
    FOCUS_VOICE_KEY       key code to listen for (default 164, KEY_PLAYPAUSE; see evtest)
    FOCUS_VOICE_MODEL     Whisper model (default base.en; tiny.en is faster, less accurate)
    FOCUS_VOICE_LEVEL     fixed speech level if the automatic one misjudges your room
    FOCUS_VOICE_WAKE      openWakeWord model, e.g. hey_jarvis (unset: button only)
    FOCUS_VOICE_WAKE_THRESHOLD  0 to 1, default 0.5; higher means fewer false starts

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
import time
import wave
from pathlib import Path
from typing import Callable, Iterator

from .. import chat, config
from ..coach import CoachError
from ..config import Config

log = logging.getLogger(__name__)

RATE = 16000                 # what Whisper and openWakeWord want
CHUNK_SAMPLES = 1280         # 80 ms, the frame openWakeWord expects
CHUNK_SECONDS = CHUNK_SAMPLES / RATE
CHUNK_BYTES = CHUNK_SAMPLES * 2   # 16-bit mono
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


# --- the microphone -----------------------------------------------------

class Mic:
    """One arecord stream for the whole service, keeping the newest few seconds.

    A reader thread drains arecord continuously, so it never overruns while a
    reply is being spoken; old audio is dropped, and clear() throws away what
    was heard before a turn starts (the chime, the wake word, the reply).
    """

    def __init__(self, device: str, keep_s: float = 3.0, command: list[str] | None = None):
        self.device = device
        self.proc = subprocess.Popen(
            command or ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(RATE),
                        "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=max(1, round(keep_s / CHUNK_SECONDS)))
        threading.Thread(target=self._pump, daemon=True).start()

    def _put(self, item: bytes | None) -> None:
        while True:
            try:
                self._chunks.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._chunks.get_nowait()  # drop the oldest
                except queue.Empty:
                    pass

    def _pump(self) -> None:
        while len(chunk := self.proc.stdout.read(CHUNK_BYTES)) == CHUNK_BYTES:
            self._put(chunk)
        self._put(None)

    def read(self) -> bytes:
        chunk = self._chunks.get()
        if chunk is None:
            self._chunks.put(None)  # keep failing for later reads
            self.proc.wait(timeout=5)
            err = self.proc.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(f"arecord failed on {self.device}: {err or 'no output'}; "
                               "is the speakerphone connected?")
        return chunk

    def clear(self) -> None:
        while True:
            try:
                if self._chunks.get_nowait() is None:
                    self._chunks.put(None)
                    return
            except queue.Empty:
                return


# --- knowing when you have stopped talking ------------------------------

def level(chunk: bytes) -> float:
    samples = array.array("h", chunk)
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0


class Endpointer:
    """Decides when an utterance is over from per-chunk loudness."""

    def __init__(self, fixed_level: float | None = None, floor: float = 300.0,
                 silence_s: float = 0.8, start_timeout_s: float = 6.0, max_s: float = 20.0):
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
            # The first three chunks after the chime set the room's noise level.
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


def record(read: Callable[[], bytes], endpointer: Endpointer, stop: Callable[[], bool]) -> bytes | None:
    """Raw 16 kHz mono audio of one utterance, or None if nobody spoke."""
    audio = bytearray()
    while True:
        chunk = read()
        audio += chunk
        verdict = endpointer.feed(level(chunk))
        if stop():
            verdict = "done" if endpointer.heard else "nothing"
        if verdict == "nothing":
            return None
        if verdict == "done":
            return bytes(audio)


# --- the wake word ------------------------------------------------------

class WakeWord:
    def __init__(self, name: str, threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model

        info = openwakeword.MODELS.get(name)
        if info is None:
            raise ValueError(f"Unknown wake word {name!r}; built in: {', '.join(openwakeword.MODELS)}")
        path = Path(info["model_path"]).with_suffix(".onnx")
        if not path.is_file():
            raise FileNotFoundError(f"Wake word model {path} is missing; see the README's Voice section")
        # ONNX, not tflite: tflite-runtime has no build for the Pi's Python.
        self.model = Model(wakeword_models=[name], inference_framework="onnx")
        self.name = name
        self.threshold = threshold

    def heard(self, chunk: bytes) -> bool:
        import numpy as np

        scores = self.model.predict(np.frombuffer(chunk, dtype=np.int16))
        return max(scores.values(), default=0.0) >= self.threshold

    def reset(self) -> None:
        self.model.reset()


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
    def __init__(self, cfg: Config, speak, stream, play: Callable[[bytes], None],
                 listen: Callable[[Callable[[], bool]], bytes | None],
                 transcribe: Callable[[bytes], str], ask=chat.run):
        self.cfg = cfg
        self.speak = speak          # text -> spoken (focus.outputs.speech.Speech.say)
        self.stream = stream        # () -> SentenceStream, for speaking as the reply arrives
        self.play = play            # wav bytes -> played
        self.listen = listen
        self.transcribe = transcribe
        self.ask = ask

    def turn(self, pressed_again: Callable[[], bool]) -> None:
        """One turn: listen, understand, answer out loud."""
        self.play(LISTENING)
        audio = self.listen(pressed_again)
        if audio is None:
            self.play(NOTHING)
            return
        self.play(GOT_IT)
        stopped = time.monotonic()
        text = self.transcribe(audio)
        heard = time.monotonic()
        if not text:
            self.speak("Sorry, I didn't catch that.")
            return
        log.info("Heard: %s", text)
        out = self.stream()
        try:
            self.ask(self.cfg, text, on_text=out.feed)
        except CoachError as exc:
            log.warning("Voice chat failed: %s", exc)
            if not out.spoke:
                out.feed(f"Sorry, that didn't work. {exc}")
        out.close()
        first = out.first_audio_at
        log.info("Timing: %.1f s of speech, transcribed in %.1f s, first word %s after you stopped, done after %.1f s",
                 len(audio) / 2 / RATE, heard - stopped,
                 f"{first - stopped:.1f} s" if first else "never", time.monotonic() - stopped)


_LOST = object()


def run(voice: Voice, mic, presses: Iterator[None] | None, wake=None) -> None:
    """Start a turn on each button press or wake word, until the hardware goes away."""
    events: queue.Queue = queue.Queue()

    if presses is not None:
        def pump() -> None:
            for _ in presses:
                events.put(None)
            events.put(_LOST)

        threading.Thread(target=pump, daemon=True).start()

    def pressed_again() -> bool:
        try:
            item = events.get_nowait()
        except queue.Empty:
            return False
        if item is _LOST:
            raise RuntimeError("Lost the speakerphone's button")
        return True

    while True:
        chunk = mic.read()
        if pressed_again():
            trigger = "button"
        elif wake is not None and wake.heard(chunk):
            trigger = wake.name
        else:
            continue
        log.info("Listening (%s)", trigger)
        try:
            voice.turn(pressed_again)
        except RuntimeError:
            raise  # lost hardware: let systemd restart the service
        except Exception:
            log.exception("Voice turn failed")
        # Nothing pressed or said while it was answering starts another turn,
        # and the reply it just spoke can't trigger the wake word.
        mic.clear()
        while pressed_again():
            pass
        if wake is not None:
            wake.reset()


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
    wake_name = os.environ.get("FOCUS_VOICE_WAKE")
    try:
        button = find_input(os.environ.get("FOCUS_VOICE_BUTTON", "PowerConf Consumer Control"))
    except FileNotFoundError:
        if not wake_name:
            raise
        button = None
        log.warning("No button found; only the wake word starts a turn")

    log.info("Loading Whisper, Piper%s", " and the wake word" if wake_name else "")
    wake = WakeWord(wake_name, float(os.environ.get("FOCUS_VOICE_WAKE_THRESHOLD", "0.5"))) if wake_name else None
    transcribe = Transcriber(os.environ.get("FOCUS_VOICE_MODEL", "base.en"),
                             os.environ.get("FOCUS_VOICE_MODELS", DEFAULT_MODELS))
    spoken = speech.create()
    mic = Mic(device)

    def listen(stop: Callable[[], bool]) -> bytes | None:
        mic.clear()  # start after the chime, not with it
        return record(mic.read, Endpointer(float(fixed) if fixed else None), stop)

    voice = Voice(config.load(), speak=spoken.say, stream=spoken.stream, play=spoken.play,
                  listen=listen, transcribe=transcribe)
    key = int(os.environ.get("FOCUS_VOICE_KEY", KEY_PLAYPAUSE))
    ways = ([f'say "{wake_name.replace("_", " ")}"'] if wake else []) + ([f"press key {key}"] if button else [])
    log.info("Ready: %s, then speak", " or ".join(ways))
    if button is None:
        run(voice, mic, None, wake)
        return 0
    with open(button, "rb", buffering=0) as stream:
        run(voice, mic, key_presses(stream, key), wake)
    return 0


if __name__ == "__main__":
    sys.exit(main())
