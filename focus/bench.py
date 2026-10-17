"""Time each part of a voice turn on this machine, to see where the seconds go.

    python -m focus.bench          Piper, Whisper (each downloaded model),
                                   the wake word and the loudness check
    python -m focus.bench --api    also the coach's reply time (one short
                                   Haiku call, a small fraction of a cent)

Needs the voice setup (Piper voice, Whisper model). Run it the way the
services run so it gets their settings:

    sudo systemd-run --pipe --wait -p User=<user> \\
        -p EnvironmentFile=/etc/focus-assistant/focus.env \\
        -E FOCUS_STATE_DIR=/home/<user>/.local/state/focus-assistant -E HF_HUB_OFFLINE=1 \\
        /opt/focus-assistant/venv/bin/python -m focus.bench --api

Nothing is played or recorded: the test speech comes from Piper itself.
"""

from __future__ import annotations

import io
import json
import os
import platform
import statistics
import sys
import time
import wave

SAID = "I finished the spec. Add email Sam the budget, and move the report to the top."
REPLY = "Done. Next is the email to Sam, then the report."


def _pcm16k(wav: bytes):
    import numpy as np

    with wave.open(io.BytesIO(wav)) as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    n = int(len(pcm) * 16000 / rate)
    return np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.int16)


def _timed(fn, runs: int = 3) -> float:
    """Median seconds of `runs` calls, after one warm-up call."""
    fn()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from piper import PiperVoice

    from . import coach, config, state as st
    from .chat import SYSTEM_PROMPT, VOICE_RULES
    from .inputs import voice
    from .outputs import speech

    print(f"Machine: {platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}\n")

    # Piper
    voice_path = os.environ.get("FOCUS_SPEECH_VOICE", speech.DEFAULT_VOICE)
    piper = PiperVoice.load(voice_path)
    reply_wav = speech.synthesize(piper, REPLY)
    with wave.open(io.BytesIO(reply_wav)) as w:
        reply_s = w.getnframes() / w.getframerate()
    t = _timed(lambda: speech.synthesize(piper, REPLY))
    print(f"Piper ({os.path.basename(voice_path)})")
    print(f"  {reply_s:.1f} s of speech generated in {t:.2f} s ({t / reply_s:.2f} x real time)")
    first = "Done."
    print(f"  a short first sentence ({first!r}): {_timed(lambda: speech.synthesize(piper, first)):.2f} s\n")

    # Whisper
    audio = _pcm16k(speech.synthesize(piper, SAID)).tobytes()
    said_s = len(audio) / 2 / 16000
    models_dir = os.environ.get("FOCUS_VOICE_MODELS", voice.DEFAULT_MODELS)
    print(f"Whisper on {said_s:.1f} s of speech")
    for name in dict.fromkeys([os.environ.get("FOCUS_VOICE_MODEL", "base.en"), "tiny.en", "base.en", "small.en"]):
        try:
            transcribe = voice.Transcriber(name, models_dir)
        except Exception:
            print(f"  {name:9} not downloaded")
            continue
        heard = transcribe(audio)
        t = _timed(lambda: transcribe(audio))
        print(f"  {name:9} {t:.2f} s  heard: {heard}")
    print()

    # Wake word and loudness check, per 80 ms frame
    frames = [audio[i:i + voice.CHUNK_BYTES] for i in range(0, len(audio) - voice.CHUNK_BYTES, voice.CHUNK_BYTES)]
    start = time.perf_counter()
    for f in frames:
        voice.level(f)
    per = (time.perf_counter() - start) / len(frames)
    print(f"Loudness check: {per * 1000:.2f} ms per frame ({per / voice.CHUNK_SECONDS:.1%} of one CPU)")
    wake_name = os.environ.get("FOCUS_VOICE_WAKE", "hey_jarvis")
    try:
        wake = voice.WakeWord(wake_name)
        start = time.perf_counter()
        for f in frames:
            wake.heard(f)
        per = (time.perf_counter() - start) / len(frames)
        print(f"Wake word ({wake_name}, speech detector on): {per * 1000:.2f} ms per frame "
              f"({per / voice.CHUNK_SECONDS:.1%} of one CPU)")
    except Exception as exc:
        print(f"Wake word: not available ({exc})")

    # The coach
    if "--api" in argv:
        cfg = config.load()
        with st.locked_state(cfg) as s:
            payload = coach.build_payload(s, "chat", st.now_iso())
        client = coach.make_client("run this with the service's settings (see the top of focus/bench.py)")
        marks: list[float] = []
        start = time.perf_counter()
        response = coach.create(
            cfg, client, model=cfg.chat_model, on_text=lambda _: marks.append(time.perf_counter()),
            system=SYSTEM_PROMPT + VOICE_RULES,
            messages=[{"role": "user", "content": f"<data>\n{json.dumps(payload)}\n</data>\n\nwhat's next?"}],
        )
        total = time.perf_counter() - start
        print(f"\nCoach ({cfg.chat_model}, no tools)")
        print(f"  first words after {marks[0] - start:.2f} s, whole reply after {total:.2f} s, "
              f"{response.usage.input_tokens} tokens in / {response.usage.output_tokens} out")
        print(f"  said: {coach.reply_text(response)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
