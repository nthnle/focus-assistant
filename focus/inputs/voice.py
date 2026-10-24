"""Talk to the coach at the desk: say "Hey Jarvis" or press the play button, then speak.

    wake word or play button -> chime -> record until you stop talking
    (or press again) -> faster-whisper on the Pi -> the same coach as phone
    chat -> Piper speaks the reply through the speaker

The microphone stream is always open while the service runs, but its audio
is thrown away: it only goes through the wake-word model (if one is set).
A turn's audio is kept only after the wake word or a press, until you stop
talking. Speech never leaves the Pi (only the
transcribed text goes to the coach). Without a wake word, only the button
starts a turn. Between turns it also says a check-in that was held while
you were away, once you're back, and a nudge when the desk camera has seen
you on the phone for a while (focus.nudge). The Anker PowerConf's play button
arrives as KEY_PLAYPAUSE on its "Consumer Control" input device, with the
release right after the press, so it is press-to-talk, not hold-to-talk.
(Its call button sends nothing Linux turns into a key.)

    FOCUS_SPEECH_DEVICE   ALSA device for the mic and the speaker (required)
    FOCUS_VOICE_BUTTON    input device name to look for (default "PowerConf Consumer Control")
    FOCUS_VOICE_KEY       key code to listen for (default 164, KEY_PLAYPAUSE; see evtest)
    FOCUS_VOICE_MODEL     speech model: Whisper base.en (default), tiny.en (faster, less
                          accurate), or moonshine/tiny, moonshine/base (fast on short clips;
                          no vocabulary hint). Whisper is given your priorities as a hint.
    FOCUS_VOICE_LEVEL     fixed speech level if the automatic one misjudges your room
    FOCUS_VOICE_WAKE      openWakeWord model, e.g. hey_jarvis (unset: button only)
    FOCUS_VOICE_WAKE_THRESHOLD  0 to 1, default 0.5; higher means fewer false starts
    FOCUS_VOICE_WAKE_FRAMES     80 ms frames in a row above it to fire, default 3
    FOCUS_VOICE_WAKE_VAD        speech-detector threshold, default 0.5; 0 turns it off
    FOCUS_VOICE_FOLLOWUP  seconds to keep listening after a reply, no wake word needed
    FOCUS_VOICE_TALK_FOLLOWUP  the same in talk mode ("let's talk"), default 10
                          (default 5; 0 = one trigger per turn)

    python -m focus.inputs.voice    the service (what focus-voice runs)
"""

from __future__ import annotations

import array
import contextlib
import functools
import io
import logging
import math
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from .. import actions, blocks, chat, checkin, config, nudge, onnx, pause, presence, state as st
from ..coach import CoachError
from ..config import Config

log = logging.getLogger(__name__)

RATE = 16000                 # what Whisper and openWakeWord want
CHUNK_SAMPLES = 1280         # 80 ms, the frame openWakeWord expects
CHUNK_SECONDS = CHUNK_SAMPLES / RATE
CHUNK_BYTES = CHUNK_SAMPLES * 2   # 16-bit mono
# arecord sends a chunk every 80 ms; this long with none means the device is gone
# (a speakerphone pulled off USB can leave arecord hanging instead of exiting).
MIC_STALL_S = 5.0
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


# The call button never becomes a Linux key: it arrives only as a raw HID
# report on the PowerConf's hidraw device, 02 01 once per press (no release).
POWERCONF_HID = "0000291A:00003301"  # USB 291a:3301, as it appears in the hidraw uevent
CALL_REPORT = b"\x02\x01"


def find_hidraw(hid_id: str, sys_root: Path = Path("/sys/class/hidraw")) -> Path | None:
    for node in sorted(sys_root.glob("hidraw*")):
        try:
            uevent = (node / "device" / "uevent").read_text(errors="replace")
        except OSError:
            continue
        if hid_id.upper() in uevent.upper():
            return Path("/dev") / node.name
    return None


def call_presses(stream: io.BufferedReader, report: bytes = CALL_REPORT) -> Iterator[None]:
    while chunk := stream.read(64):  # hidraw hands over one report per read
        if chunk.startswith(report):
            yield None


def mark_suggested_done(cfg: Config) -> str:
    """The call button: mark done the item the latest check-in suggested
    starting with; returns what to say."""
    with st.locked_state(cfg) as s:
        if not s.open_priorities:
            return "Nothing left to mark done."
        item = s.suggested
        if item is None:
            return "Tell me which one is done."
        actions.complete_priority(s, item.id)
        left = len(s.open_priorities)
    log.info("Call button: marked %s done", config.words(item.text))
    return f"Done: {item.text}. " + (f"{left} left on your list." if left else "That's everything on your list.")


def undo_last_done(cfg: Config) -> str:
    """A double press on the call button: reopen whatever was marked done most recently."""
    with st.locked_state(cfg) as s:
        done = [p for p in s.priorities if p.done]
        if not done:
            return "Nothing to undo."
        last = max(done, key=lambda p: p.done_at)
        actions.reopen_priority(s, last.id)
    log.info("Call button double press: reopened %s", config.words(last.text))
    return f"Undone: {last.text} is back on your list."


DOUBLE_PRESS_S = 0.4  # a second call press this soon after the first means "undo"


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

    def __init__(self, device: str, keep_s: float = 3.0, command: list[str] | None = None,
                 stall_s: float = MIC_STALL_S):
        self.device = device
        self.stall_s = stall_s
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
        try:
            chunk = self._chunks.get(timeout=self.stall_s)
        except queue.Empty:
            # Exit rather than wait forever, so systemd restarts the service
            # and it finds the device again once it is back.
            self.proc.kill()
            self._chunks.put(None)
            raise RuntimeError(f"No audio from {self.device} for {self.stall_s:g} s; "
                               "is the speakerphone connected?") from None
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
    """openWakeWord, made harder to set off by conversation and noise.

    It fires only when the score stays at or above `threshold` for `frames`
    80 ms frames in a row (a real "Hey Jarvis" holds for 6-8; a stray burst
    doesn't), and only while Silero's speech detector hears a human voice
    (`vad`, 0 turns it off). `last_score` is the peak of the run that fired.
    """

    def __init__(self, name: str, threshold: float = 0.5, frames: int = 3, vad: float = 0.5):
        import openwakeword
        from openwakeword.model import Model

        info = openwakeword.MODELS.get(name)
        if info is None:
            raise ValueError(f"Unknown wake word {name!r}; built in: {', '.join(openwakeword.MODELS)}")
        path = Path(info["model_path"]).with_suffix(".onnx")
        if not path.is_file():
            raise FileNotFoundError(f"Wake word model {path} is missing; see the README's Voice section")
        # ONNX, not tflite: tflite-runtime has no build for the Pi's Python.
        self.model = Model(wakeword_models=[name], inference_framework="onnx", vad_threshold=vad)
        self.name = name
        self.threshold = threshold
        self.frames = frames
        self.last_score = 0.0
        self._run: list[float] = []

    def heard(self, chunk: bytes) -> bool:
        import numpy as np

        return self.score(max(self.model.predict(np.frombuffer(chunk, dtype=np.int16)).values(), default=0.0))

    def score(self, value: float) -> bool:
        """Count one frame's score; True once enough frames in a row are high."""
        if value < self.threshold:
            self._run.clear()
            return False
        self._run.append(value)
        if len(self._run) < self.frames:
            return False
        self.last_score = max(self._run)
        self._run.clear()
        return True

    def reset(self) -> None:
        self.model.reset()
        self._run.clear()


# --- speech to text -----------------------------------------------------

class Transcriber:
    def __init__(self, model: str, models_dir: str):
        from faster_whisper import WhisperModel

        # local_files_only: the service has no business downloading models.
        self.model = WhisperModel(model, device="cpu", compute_type="int8",
                                  download_root=models_dir, local_files_only=True)

    def __call__(self, audio, hint: str | None = None) -> str:
        """`audio` is raw 16-bit mono bytes at 16 kHz, or anything faster-whisper accepts.

        `hint` is text Whisper treats as what came just before (your
        priorities), which makes it likelier to spell those words right.
        """
        if isinstance(audio, (bytes, bytearray)):
            import numpy as np
            audio = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1,
                                            condition_on_previous_text=False, initial_prompt=hint)
        return " ".join(s.text.strip() for s in segments).strip()


class MoonshineTranscriber:
    """Moonshine (moonshine/tiny or moonshine/base): built for short clips on small devices.

    Unlike Whisper it doesn't pad every clip to 30 s, so a two-second command
    costs about two seconds' worth of work. It takes no vocabulary hint.
    Weights are downloaded once at setup into <models>/moonshine (README, Voice).
    """

    def __init__(self, name: str, models_dir: str):
        from moonshine_onnx import MoonshineOnnxModel, load_tokenizer

        size = name.split("/")[-1]
        path = Path(models_dir) / "moonshine" / "onnx" / "merged" / size / "float"
        if not (path / "encoder_model.onnx").is_file():
            raise FileNotFoundError(f"Moonshine {size} isn't downloaded to {path}; see the README's Voice section")
        self.model = MoonshineOnnxModel(models_dir=str(path), model_name=size)
        self.tokenizer = load_tokenizer()

    def __call__(self, audio, hint: str | None = None) -> str:
        import numpy as np

        if isinstance(audio, (bytes, bytearray)):
            audio = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        audio = np.asarray(audio, dtype=np.float32)[: 60 * RATE]  # it accepts 0.1-64 s
        if len(audio) < RATE // 10:
            return ""
        return self.tokenizer.decode_batch(self.model.generate(audio[None, :]))[0].strip()


def make_transcriber(name: str, models_dir: str):
    """FOCUS_VOICE_MODEL: a Whisper model (base.en, tiny.en, ...) or moonshine/tiny, moonshine/base."""
    if name.startswith("moonshine/"):
        return MoonshineTranscriber(name, models_dir)
    return Transcriber(name, models_dir)


def vocabulary(cfg: Config) -> str | None:
    """Your open priorities, as a hint for the speech recogniser."""
    with st.locked_state(cfg) as s:
        texts = [p.text for p in s.open_priorities]
    return ("My priorities: " + "; ".join(texts) + ".") if texts else None


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
FOLLOW_UP = tone([(880, 0.06)], volume=0.15)  # soft: "still listening, no wake word needed"

# What Whisper tends to "hear" in near-silence (it was trained on subtitles).
PHANTOMS = {"you", "thank you", "thanks", "bye", "thank you for watching", "thanks for watching"}


# Talk mode: "let's talk" allows longer replies and a longer follow-up window,
# until "that's all" or a quiet window ends it.
TALK_START = re.compile(r"\b(let'?s|let us|can we|could we) (talk|chat)\b|\btalk mode\b")
TALK_END = re.compile(r"\b(that'?s all|that is all|stop talking|end (the )?(talk|chat)|"
                      r"back to work|goodbye|that'?s it for now)\b")
# "Let's talk" alone is just the switch; with more words it's also the first message.
TALK_SWITCH_WORDS = 4


# --- the loop -----------------------------------------------------------

class Voice:
    def __init__(self, cfg: Config, speak, stream, play: Callable[[bytes], None],
                 listen: Callable[..., bytes | None],
                 transcribe: Callable[..., str], ask=chat.run,
                 hint: Callable[[], str | None] = lambda: None, followup_s: float = 0.0,
                 talk_followup_s: float = 10.0):
        self.cfg = cfg
        self.speak = speak          # text -> spoken (focus.outputs.speech.Speech.say)
        self.stream = stream        # () -> SentenceStream, for speaking as the reply arrives
        self.play = play            # wav bytes -> played
        self.listen = listen        # (stop, start_timeout_s=None) -> audio or None
        self.transcribe = transcribe  # (audio, hint) -> text
        self.ask = ask
        self.hint = hint            # () -> vocabulary for the recogniser, or None
        self.followup_s = followup_s  # after a reply, listen this long without a wake word
        self.talk_followup_s = talk_followup_s  # the same in talk mode
        self.talking = False          # talk mode: longer replies and windows, until it ends

    def turn(self, pressed_again: Callable[[], bool]) -> None:
        """One turn: listen, understand, answer out loud, then stay open for a follow-up.

        After each reply a soft chime opens a FOCUS_VOICE_FOLLOWUP-second window:
        speak and it's another turn, no wake word needed; stay quiet and it ends.
        In talk mode the window is FOCUS_VOICE_TALK_FOLLOWUP seconds, and when
        the turn ends, talk mode ends with it.
        """
        try:
            self.play(LISTENING)
            audio = self.listen(pressed_again)
            if audio is None:
                self.play(NOTHING)
                return
            while self.answer(audio, pressed_again) and (window := self._window()) > 0:
                self.play(FOLLOW_UP)
                audio = self.listen(pressed_again, start_timeout_s=window)
                if audio is None:
                    log.info("No follow-up")
                    return
        finally:
            if self.talking:
                log.info("Talk mode off")
            self.talking = False

    def _window(self) -> float:
        return self.talk_followup_s if self.talking else self.followup_s

    def _talk_switch(self, text: str) -> bool | None:
        """Handle "let's talk" / "that's all". True or False: handled, and
        whether to stay open for a follow-up; None: an ordinary message."""
        said = text.lower()
        if self.talking and TALK_END.search(said):
            self.talking = False
            log.info("Talk mode off")
            self.speak("Okay, back to it.")
            return False
        if not self.talking and TALK_START.search(said):
            self.talking = True
            log.info("Talk mode on")
            if len(said.split()) <= TALK_SWITCH_WORDS:
                self.speak("Sure. What's on your mind?")
                return True
        return None

    def answer(self, audio: bytes, pressed_again: Callable[[], bool] = lambda: False) -> bool:
        """Understand one utterance and speak the coach's reply.

        True if the coach replied and was heard out; a play press while it
        speaks cuts the reply off (and skips the follow-up window).
        """
        self.play(GOT_IT)
        stopped = time.monotonic()
        hint = self.hint()
        text = self.transcribe(audio, hint=hint)
        heard = time.monotonic()
        if not text:
            self.speak("Sorry, I didn't catch that.")
            return False
        if text.strip(" .,!?").lower() in PHANTOMS or (hint and text.strip() in hint):
            # Whisper's usual output for near-silence, or the hint echoed back:
            # nobody was talking to us.
            log.info("Ignored a likely mis-hearing: %s", config.words(text))
            self.play(NOTHING)
            return False
        log.info("Heard: %s", config.words(text))
        switched = self._talk_switch(text)
        if switched is not None:
            return switched
        out = self.stream()
        replied = True
        try:
            # talk= only when on, so an `ask` without it still works for short turns.
            self.ask(self.cfg, text, on_text=out.feed, voice=True, **({"talk": True} if self.talking else {}))
        except CoachError as exc:
            log.warning("Voice chat failed: %s", exc)
            replied = False
            if not out.spoke:
                out.feed(f"Sorry, that didn't work. {exc}")
        out.finish()
        while not out.wait(0.05):
            if pressed_again():
                out.stop()
                out.wait()
                log.info("Reply cut short with the play button")
                replied = False
                break
        log.info("Timing: %s", timing(len(audio) / 2 / RATE, stopped, heard, out, time.monotonic()))
        return replied


def timing(spoke_s: float, stopped: float, heard: float, out, done: float) -> str:
    """One line splitting a turn's wait into its parts, all counted from the end of recording.

    transcribe: Whisper on the Pi; coach: until the reply's first words arrive;
    sentence: until its first sentence is complete; voice: Piper turning that
    sentence into audio. Where the seconds go says what to speed up.
    """
    def at(t: float | None) -> str:
        return f"{t - stopped:.1f} s" if t is not None else "-"

    first_text = getattr(out, "first_text_at", None)
    sentence = getattr(out, "first_sentence_at", None)
    audio = getattr(out, "first_audio_at", None)
    parts = [
        f"{spoke_s:.1f} s of speech",
        f"transcribe {heard - stopped:.1f} s",
        f"coach {first_text - heard:.1f} s" if first_text is not None else "coach -",
        f"sentence {sentence - first_text:.1f} s" if sentence is not None and first_text is not None else "sentence -",
        f"voice {audio - sentence:.1f} s" if audio is not None and sentence is not None else "voice -",
        f"first word at {at(audio)}",
        f"done at {done - stopped:.1f} s",
    ]
    return ", ".join(parts)


_LOST = object()


def second_call_follows(events: queue.Queue, window_s: float = DOUBLE_PRESS_S) -> bool:
    """After a call press, wait briefly for a second one (a double press).

    Anything else that arrives meanwhile (a play press) goes back in the queue.
    """
    deadline = time.monotonic() + window_s
    while (left := deadline - time.monotonic()) > 0:
        try:
            item = events.get(timeout=left)
        except queue.Empty:
            return False
        if item == "call":
            return True
        events.put(item)
        return False
    return False


def replay_missed(cfg: Config, speak: Callable[[str], None], now: datetime) -> bool:
    """Say the latest check-in that was held while you were away or paused,
    once the camera sees you back and the pause is over."""
    if not any(c.get("unheard") for c in st.peek(cfg, today=now.date()).checkins):
        return False  # the usual case: no lock, and no state.json rewrite every 30 s
    with st.locked_state(cfg, today=now.date()) as s:
        unheard = [c for c in s.checkins if c.get("unheard")]
        if not unheard or not presence.back(cfg, now) or pause.paused(s, now):
            return False
        for c in unheard:
            c["unheard"] = False
        last = unheard[-1]
    log.info("Back at the desk; saying the held %s check-in", last["slot"])
    speak(f"You missed the {last['slot']} check-in. It said: {last.get('reply') or ''}")
    return True


def between_turns(cfg: Config, say: Callable[[str], None], now: datetime,
                  focus_blocks: list[blocks.Block]) -> None:
    """Every 30 s between turns: a held check-in first, else a phone nudge if
    one is due (never both at once; the nudge waits for the next tick)."""
    if not replay_missed(cfg, say, now):
        nudge.run(cfg, now, say, focus_blocks)


def run(voice: Voice, mic, presses: Iterator[None] | None, wake=None,
        idle: Callable[[], None] | None = None, idle_every_s: float = 30.0,
        calls: Iterator[None] | None = None, on_call: Callable[[], None] | None = None,
        on_double_call: Callable[[], None] | None = None) -> None:
    """Start a turn on each play press or wake word, until the hardware goes away.

    `idle` runs between turns every `idle_every_s` (held check-ins, phone nudges);
    `on_call` runs on a call-button press between turns, `on_double_call`
    on two presses within DOUBLE_PRESS_S.
    """
    events: queue.Queue = queue.Queue()
    next_idle = time.monotonic() + idle_every_s

    def pump(source: Iterator[None], kind: str) -> None:
        try:
            for _ in source:
                events.put(kind)
        except Exception as exc:  # e.g. EIO when the speakerphone drops off USB
            log.warning("The %s button stopped: %s", kind, exc)
        finally:
            events.put(_LOST)  # always: the main loop exits and systemd restarts us

    for source, kind in ((presses, "play"), (calls, "call")):
        if source is not None:
            threading.Thread(target=pump, args=(source, kind), daemon=True).start()

    def next_event() -> str | None:
        try:
            item = events.get_nowait()
        except queue.Empty:
            return None
        if item is _LOST:
            raise RuntimeError("Lost the speakerphone's buttons")
        return item

    def pressed_again() -> bool:
        """During a turn: only play counts (it ends the recording); call is ignored."""
        item = next_event()
        if item == "call":
            log.info("Call button ignored during a turn")
        return item == "play"

    while True:
        chunk = mic.read()
        if idle is not None and time.monotonic() >= next_idle:
            next_idle = time.monotonic() + idle_every_s
            try:
                idle()
            except Exception:
                log.exception("Idle task failed")
            mic.clear()  # what it just said isn't anyone at the desk
        event = next_event()
        if event == "call":
            action = on_double_call if second_call_follows(events) else on_call
            if action is not None:
                try:
                    action()
                except Exception:
                    log.exception("Call button action failed")
            mic.clear()
            continue
        if event == "play":
            trigger = "button"
        elif wake is not None and wake.heard(chunk):
            trigger = f"{wake.name}, score {wake.last_score:.2f}"
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
        while next_event() is not None:
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
    onnx.no_telemetry()  # the wake word and Piper run on onnxruntime
    wake = WakeWord(
        wake_name,
        threshold=float(os.environ.get("FOCUS_VOICE_WAKE_THRESHOLD", "0.5")),
        frames=int(os.environ.get("FOCUS_VOICE_WAKE_FRAMES", "3")),
        vad=float(os.environ.get("FOCUS_VOICE_WAKE_VAD", "0.5")),
    ) if wake_name else None
    transcribe = make_transcriber(os.environ.get("FOCUS_VOICE_MODEL", "base.en"),
                                  os.environ.get("FOCUS_VOICE_MODELS", DEFAULT_MODELS))
    spoken = speech.create()
    mic = Mic(device)

    def listen(stop: Callable[[], bool], start_timeout_s: float | None = None) -> bytes | None:
        mic.clear()  # start after the chime (and the reply), not with it
        endpointer = Endpointer(float(fixed) if fixed else None,
                                **({"start_timeout_s": start_timeout_s} if start_timeout_s else {}))
        return record(mic.read, endpointer, stop)

    cfg = config.load()
    # Marking something done rewrites the check-in on the screen to match.
    refresh = checkin.Refresher(cfg)
    voice = Voice(cfg, speak=spoken.say, stream=spoken.stream, play=spoken.play,
                  ask=functools.partial(chat.run, on_done_change=refresh),
                  listen=listen, transcribe=transcribe, hint=lambda: vocabulary(cfg),
                  followup_s=float(os.environ.get("FOCUS_VOICE_FOLLOWUP", "5")),
                  talk_followup_s=float(os.environ.get("FOCUS_VOICE_TALK_FOLLOWUP", "10")))
    focus_blocks = blocks.from_env()

    def idle() -> None:
        between_turns(cfg, spoken.say, datetime.now().astimezone(), focus_blocks)

    key = int(os.environ.get("FOCUS_VOICE_KEY", KEY_PLAYPAUSE))
    ways = ([f'say "{wake_name.replace("_", " ")}"'] if wake else []) + ([f"press key {key}"] if button else [])

    with contextlib.ExitStack() as stack:
        presses = key_presses(stack.enter_context(open(button, "rb", buffering=0)), key) if button else None
        calls = None
        hidraw = find_hidraw(POWERCONF_HID)
        if hidraw is None:
            log.info("No PowerConf call button found; it won't mark priorities done")
        else:
            try:
                calls = call_presses(stack.enter_context(open(hidraw, "rb", buffering=0)))
                log.info("Call button on %s marks the suggested item done", hidraw)
            except PermissionError:
                log.warning("Can't read %s; install.sh adds the udev rule that allows it", hidraw)
        log.info("Ready: %s, then speak", " or ".join(ways))
        run(voice, mic, presses, wake, calls=calls,
            on_call=lambda: (spoken.say(mark_suggested_done(cfg)), refresh()),
            on_double_call=lambda: (spoken.say(undo_last_done(cfg)), refresh()), idle=idle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
