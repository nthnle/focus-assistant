"""Desk camera: phone use and presence during focus blocks, from a USB webcam.

A small image classifier trained on your own desk (tools/train_camera.py,
docs/camera.md) looks at one frame every FOCUS_CAMERA_EVERY seconds (2)
and says what it sees: phone (you're using it), work (you're there, not on
it) or away (the desk is empty). Frames are analysed in memory and thrown
away: none is saved, sent or logged. What is kept:

- PHONE_OUT once you've been on the phone for 30 s (backdated to when it
  started), and PHONE_IN with the minutes when you put it down. A label
  counts once it is the majority of the last five frames, so a misread
  frame or two changes nothing, and a phone session only ends after 30 s
  without the phone.
- camera.json: what it sees now and since when, for presence and the status
  page; rewritten when that changes, and every 30 s. While the camera is
  off, the service still writes "off" every 30 s, so a camera that is off
  on schedule can be told from a service that isn't running at all.

It runs from 5 minutes before each focus block (FOCUS_BLOCKS, weekdays) to
5 minutes after it, so the check-ins at the edges of a block get its answer
too; phone use counts only inside the block itself. When a spoken check-in
is held because you're away, it stays on (or comes on) until it sees you
back, for up to 2 hours; after that the check-in stays on the screen,
unsaid. Never while paused. The rest of the time the camera is closed and
its light is off.

    python -m focus.camera              the service (what focus-camera runs)
    python -m focus.camera --check      what the model sees in a few frames, and how fast
    python -m focus.camera --collect    record labelled frames to train on
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import actions, blocks, config, pause, presence, state as st
from .config import Config

log = logging.getLogger(__name__)

PHONE, WORK = "phone", "work"
AWAY = presence.AWAY
SIZE = 224               # the model's input: 224x224 RGB, squashed from the camera's 4:3
WINDOW = 5               # frames a label needs a majority of
SETTLE = timedelta(seconds=30)
END = timedelta(seconds=30)
WRITE_EVERY = timedelta(seconds=30)
MARGIN = timedelta(minutes=5)    # on this long before and after each focus block
WAIT = timedelta(hours=2)        # how long a held check-in keeps it on
OFF_POLL_S = 15.0        # how often it checks whether it should be on (or off)
FRAME_TIMEOUT_S = 30.0   # no frame for this long: the camera has hung
EXIT_NO_MODEL = 2        # the unit doesn't restart for this one

Change = tuple[str, datetime, "int | None"]   # (event kind, when, minutes for PHONE_IN)


def model_path(cfg: Config) -> Path:
    env = os.environ.get("FOCUS_CAMERA_MODEL")
    return Path(env) if env else cfg.state_dir / "camera" / "model.onnx"


def put_down(start: datetime, last: datetime) -> Change:
    """The PHONE_IN closing a session, with its minutes."""
    return (st.PHONE_IN, last, round((last - start).total_seconds() / 60))


class Tracker:
    """Per-frame labels in; what you're doing, and phone sessions as events, out."""

    def __init__(self) -> None:
        self.recent: deque[str] = deque(maxlen=WINDOW)
        self.seen: str | None = None
        self.since: datetime | None = None
        self._phone_since: datetime | None = None
        self._last_phone: datetime | None = None
        self._logged = False

    def feed(self, label: str, at: datetime, counts: bool = True) -> list[Change]:
        """`counts`: whether phone use counts now (inside a focus block)."""
        self.recent.append(label)
        best, n = Counter(self.recent).most_common(1)[0]
        if self.seen is None or 2 * n > len(self.recent):   # no majority: no change
            if best != self.seen:
                self.seen, self.since = best, at
        if not counts:
            return self._close() if self._phone_since is not None else []
        if self.seen == PHONE:
            if self._phone_since is None:
                self._phone_since = at
            self._last_phone = at
            if not self._logged and at - self._phone_since >= SETTLE:
                self._logged = True
                return [(st.PHONE_OUT, self._phone_since, None)]
        elif self._phone_since is not None and at - self._last_phone >= END:
            return self._close()
        return []

    def stop(self) -> list[Change]:
        """The camera is closing: end any phone session and start afresh next time."""
        changes = self._close() if self._phone_since is not None else []
        self.recent.clear()
        self.seen = self.since = None
        return changes

    def _close(self) -> list[Change]:
        start, last, logged = self._phone_since, self._last_phone, self._logged
        self._phone_since = self._last_phone = None
        self._logged = False
        if not logged:
            return []  # a glance: never logged, nothing to close
        return [put_down(start, last)]


def record(cfg: Config, changes: list[Change]) -> None:
    for kind, at, minutes in changes:
        with st.locked_state(cfg, today=at.date()) as s:
            actions.log_event(s, kind, f"{minutes} min" if minutes is not None else None, at=at)
        if kind == st.PHONE_OUT:
            log.info("Phone in use since %s", at.strftime("%H:%M"))
        else:
            log.info("Phone put down after %d min", minutes)


# --- the camera and the model ---------------------------------------------

def webcam_input(device: str, mjpeg: bool | None = None) -> list[str]:
    """ffmpeg reading the webcam, the same way for training frames and live ones."""
    if mjpeg is None:
        mjpeg = os.environ.get("FOCUS_CAMERA_MJPEG", "1") != "0"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2"]
    if mjpeg:
        cmd += ["-input_format", "mjpeg"]  # far less USB bandwidth than raw video
    return cmd + ["-video_size", "640x480", "-i", device]


def ffmpeg_command(device: str, every: float, size: int = SIZE, mjpeg: bool | None = None) -> list[str]:
    """One raw RGB frame every `every` seconds on stdout, scaled for the model."""
    return webcam_input(device, mjpeg) + [
        "-vf", f"fps=1/{every},scale={size}:{size}:flags=bilinear",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


class CameraError(RuntimeError):
    pass


class Camera:
    """The webcam through ffmpeg. read() waits for the next frame, but not
    forever: a webcam can hang without ffmpeg exiting."""

    def __init__(self, device: str, every: float, size: int = SIZE, popen=subprocess.Popen,
                 timeout: float = FRAME_TIMEOUT_S):
        self.frame_bytes = size * size * 3
        self.timeout = max(timeout, 5 * every)
        self.proc = popen(ffmpeg_command(device, every, size),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        self._frames: queue.Queue[bytes | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            data = self.proc.stdout.read(self.frame_bytes)
            if not data or len(data) < self.frame_bytes:
                self._frames.put(None)  # ffmpeg stopped
                return
            self._frames.put(data)

    def read(self) -> bytes:
        try:
            data = self._frames.get(timeout=self.timeout)
        except queue.Empty:
            raise CameraError(f"No frame from the camera for {self.timeout:.0f} s; it may have hung "
                              "(unplug it and plug it back in)") from None
        if data is None:
            raise CameraError(f"The camera stopped (ffmpeg exit {self.proc.poll()}); is it plugged in, "
                              "or in use by something else?")
        return data

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Model:
    """The classifier from tools/train_camera.py, on onnxruntime."""

    def __init__(self, path: Path):
        import numpy as np
        import onnxruntime as ort

        from . import onnx
        onnx.no_telemetry()

        opts = ort.SessionOptions()
        # One frame every 2 s needs no more than one core; leave the rest to speech.
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        meta = self.session.get_modelmeta().custom_metadata_map
        self.labels: list[str] = json.loads(meta["labels"])
        self.size = int(meta.get("size", SIZE))
        self.trained = meta.get("trained", "?")
        self._np = np

    def __call__(self, frame: bytes) -> str:
        x = self._np.frombuffer(frame, self._np.uint8).reshape(1, self.size, self.size, 3)
        probs = self.session.run(None, {"frames": x})[0][0]
        return self.labels[int(probs.argmax())]


# --- the service ----------------------------------------------------------

def in_window(now: datetime, focus_blocks: list[blocks.Block], margin: timedelta = MARGIN) -> bool:
    """A weekday, from `margin` before a focus block to `margin` after it."""
    if now.weekday() >= 5:
        return False
    return any(datetime.combine(now.date(), start, now.tzinfo) - margin <= now
               < datetime.combine(now.date(), end, now.tzinfo) + margin
               for start, end in focus_blocks)


def why_off(cfg: Config, now: datetime, focus_blocks: list[blocks.Block]) -> str | None:
    """Why the camera should be off at `now` (timezone-aware), or None to run it.

    Outside its window, a check-in held more than WAIT is given up on here:
    nothing else would ever turn the camera on for it again."""
    s = st.peek(cfg, today=now.date())
    if pause.paused(s, now):
        return "paused"
    if in_window(now, focus_blocks):
        return None
    waiting, stale = s.held(now, WAIT)
    if stale:
        with st.locked_state(cfg, today=now.date()) as s:
            given_up = actions.expire_held_checkins(s, now, WAIT)
        log.info("Gave up waiting to say %d held check-in(s); they stay on the screen", given_up)
    if waiting:
        return None
    return "outside focus blocks"


def close_stale(cfg: Config, now: datetime) -> None:
    """A phone session left open by a run that died without closing it (a
    crash, a power cut): close it when camera.json last saw the phone, or
    when it started seeing something else; failing both, when it began."""
    since = st.phone_in_use_since(st.peek(cfg, today=now.date()).events)
    if since is None:
        return
    seen = presence.read_camera(cfg)
    last = since if seen is None else (seen.at if seen.label == PHONE else seen.since)
    if not since <= last <= now:
        last = since
    log.info("Closing the phone session left open since %s", since.strftime("%H:%M"))
    record(cfg, [put_down(since, last)])
    presence.clear_camera(cfg)


def watch(cfg: Config, model: Callable[[bytes], str], sees_away: bool,
          open_camera: Callable[[], Camera], focus_blocks: list[blocks.Block],
          clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
          sleep: Callable[[float], None] = time.sleep) -> None:
    """Run the camera whenever it should be on, until something fails."""
    tracker = Tracker()
    camera: Camera | None = None
    written: tuple[str | None, datetime] | None = None
    checked: datetime | None = None   # when it last asked why_off (it reads state.json)
    off_since: datetime | None = None

    def write(label: str, since: datetime, now: datetime) -> None:
        nonlocal written
        if written is None or written[0] != label or now - written[1] >= WRITE_EVERY:
            presence.record_camera(cfg, label, since, now, sees_away)
            written = (label, now)

    def close(reason: str) -> None:
        nonlocal camera
        camera.close()
        camera = None
        record(cfg, tracker.stop())
        log.info("Camera off: %s", reason)

    close_stale(cfg, clock())
    try:
        while True:
            now = clock()
            if camera is None or (now - checked).total_seconds() >= OFF_POLL_S:
                off = why_off(cfg, now, focus_blocks)
                checked = now
                if off:
                    if camera is not None:
                        close(off)
                    off_since = off_since or now
                    write(presence.OFF, off_since, now)
                    sleep(OFF_POLL_S)
                    continue
            if camera is None:
                camera = open_camera()
                off_since = None
                log.info("Camera on")
            label = model(camera.read())
            now = clock()
            record(cfg, tracker.feed(label, now, counts=blocks.in_block(now, focus_blocks)))
            write(tracker.seen, tracker.since, now)
    finally:
        if camera is not None:
            close("stopping")
        presence.clear_camera(cfg)   # nobody should think it still runs


def check(model: Model, camera: Camera, frames: int) -> int:
    """Print what the model sees in a few frames, and how long each took."""
    try:
        for _ in range(frames):
            frame = camera.read()
            start = time.perf_counter()
            label = model(frame)
            print(f"{datetime.now():%H:%M:%S}  {label:<6} {(time.perf_counter() - start) * 1000:5.0f} ms",
                  flush=True)
    finally:
        camera.close()
    return 0


# --- collecting frames to train on ---------------------------------------

COLLECT_KEYS = {"p": PHONE, "w": WORK, "a": AWAY}
COLLECT_HELP = """\
Type a letter and press Enter whenever what you're doing changes:

    p   on the phone (held up, in your lap, any way you use it)
    w   at the desk, not on the phone: typing, reading, leaning back, drinking,
        the phone lying on the desk untouched
    a   away: nobody in the chair (walk out of view, then type it from elsewhere
        or just leave it running)
    s   skip: frames are thrown away (adjusting the camera, sitting down)
    q   quit

The first 3 seconds after each change are thrown away, so the moment you
switch isn't mislabelled."""
COLLECT_SETTLE = timedelta(seconds=3)


def collect(device: str, out: Path, every: float, keys=None, popen=subprocess.Popen) -> int:
    """Save labelled 640x480 frames to out/<label>/, for tools/train_camera.py."""
    keys = sys.stdin if keys is None else keys
    for name in COLLECT_KEYS.values():
        (out / name).mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="focus-frames-"))
    cmd = webcam_input(device) + ["-vf", f"fps=1/{every}", "-q:v", "3", str(staging / "%06d.jpg")]
    camera = popen(cmd, stdin=subprocess.DEVNULL)
    changes = [(datetime.now(), "s")]
    lock = threading.Lock()
    stop = threading.Event()

    def label_at(t: datetime) -> str | None:
        with lock:
            key, since = "s", datetime.min
            for when, k in changes:
                if when <= t:
                    key, since = k, when
        return COLLECT_KEYS.get(key) if t - since >= COLLECT_SETTLE else None

    def sort(final: bool = False) -> None:
        files = sorted(staging.glob("*.jpg"))
        for f in files if final else files[:-1]:   # the newest may still be being written
            t = datetime.fromtimestamp(f.stat().st_mtime)
            label = label_at(t)
            if label:
                shutil.move(str(f), out / label / f"{t:%Y%m%d-%H%M%S}-{f.stem}.jpg")
            else:
                f.unlink()

    def sorter() -> None:
        while not stop.wait(1):
            sort()
            if camera.poll() is not None:
                print(f"\nThe camera stopped (ffmpeg exit {camera.returncode}). If focus-camera is "
                      "running, stop it first: sudo systemctl stop focus-camera. Press Enter.")
                return

    def counts() -> str:
        return ", ".join(f"{name} {len(list((out / name).glob('*.jpg')))}" for name in COLLECT_KEYS.values())

    worker = threading.Thread(target=sorter, daemon=True)
    worker.start()
    print(COLLECT_HELP)
    print(f"\nSaving to {out} (so far: {counts()}). Skipping until you type p, w or a.")
    try:
        for line in keys:
            key = line.strip().lower()[:1]
            if key == "q" or camera.poll() is not None:
                break
            if key in COLLECT_KEYS or key == "s":
                with lock:
                    changes.append((datetime.now(), key))
                print(f"  now: {COLLECT_KEYS.get(key, 'skip')}   (so far: {counts()})")
            else:
                print("  p, w, a, s or q")
    except KeyboardInterrupt:
        pass
    finally:
        with lock:
            changes.append((datetime.now(), "s"))
        camera.terminate()
        camera.wait()
        stop.set()
        worker.join()
        sort(final=True)
        shutil.rmtree(staging, ignore_errors=True)
        print(f"\nDone. {counts()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m focus.camera", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", type=int, nargs="?", const=10, metavar="FRAMES",
                    help="print what the model sees in a few frames (10), then stop")
    ap.add_argument("--collect", action="store_true", help="record labelled frames to train on")
    ap.add_argument("--out", default="~/frames", help="where --collect saves frames (~/frames)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    device = os.environ.get("FOCUS_CAMERA_DEVICE", "/dev/video0")
    every = float(os.environ.get("FOCUS_CAMERA_EVERY", "2"))
    if shutil.which("ffmpeg") is None:
        print("The camera needs ffmpeg: sudo apt install -y ffmpeg", file=sys.stderr)
        return 1
    if args.collect:
        return collect(device, Path(args.out).expanduser(), every)

    # systemd stops the service with SIGTERM: exit through the finally blocks,
    # so a phone session in progress is closed instead of left open.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    cfg = config.load()
    path = model_path(cfg)
    if not path.exists():
        log.error("No model at %s: train one on your own frames first (docs/camera.md)", path)
        return EXIT_NO_MODEL
    model = Model(path)
    log.info("Model %s: %s, trained %s", path, ", ".join(model.labels), model.trained)
    try:
        if args.check:
            return check(model, Camera(device, every, model.size), args.check)
        watch(cfg, model, AWAY in model.labels, lambda: Camera(device, every, model.size), blocks.from_env())
    except CameraError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
