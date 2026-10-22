import io
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from focus import actions, blocks, camera, config, presence, state as st

BLOCKS = blocks.parse_blocks(blocks.DEFAULT_BLOCKS)
TZ = timezone(timedelta(hours=-5))
START = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)  # a Monday, in the morning block


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def feed(tracker, labels, start=START, every=2):
    """Feed one label per frame, `every` seconds apart; all the changes, in order."""
    changes = []
    for i, label in enumerate(labels):
        changes += tracker.feed(label, start + timedelta(seconds=every * i))
    return changes


W, P, A = camera.WORK, camera.PHONE, camera.AWAY


def test_a_minute_on_the_phone_is_logged_from_when_it_started():
    t = camera.Tracker()
    changes = feed(t, [W] * 5 + [P] * 30 + [W] * 25)
    # The phone became the majority on frame 7 (14 s), so the session starts there.
    started = START + timedelta(seconds=14)
    assert changes[0] == (st.PHONE_OUT, started, None)
    kind, put_down, minutes = changes[1]
    assert kind == st.PHONE_IN and minutes == 1
    assert put_down == START + timedelta(seconds=2 * 36)   # the last frame it still saw the phone


def test_a_glance_is_not_logged():
    t = camera.Tracker()
    assert feed(t, [W] * 5 + [P] * 12 + [W] * 40) == []    # 24 s on the phone


def test_misread_frames_change_nothing():
    t = camera.Tracker()
    noisy = [W, W, P, W, W, W, P, P, W, W] * 6             # never a majority of phone
    assert feed(t, noisy) == []
    assert t.seen == W


def test_putting_it_down_briefly_is_one_session():
    t = camera.Tracker()
    changes = feed(t, [P] * 30 + [W] * 10 + [P] * 30 + [W] * 25)   # 20 s down in the middle
    assert [c[0] for c in changes] == [st.PHONE_OUT, st.PHONE_IN]
    assert changes[1][2] == 2


def test_what_it_sees_and_since_when():
    t = camera.Tracker()
    feed(t, [W] * 10 + [A] * 10)
    assert t.seen == A and t.since == START + timedelta(seconds=2 * 12)  # third away frame


def test_stop_closes_a_session_and_starts_afresh():
    t = camera.Tracker()
    feed(t, [P] * 40)
    changes = t.stop()
    assert [c[0] for c in changes] == [st.PHONE_IN]
    assert t.seen is None and t.stop() == []


def test_record_writes_events_through_actions(cfg):
    camera.record(cfg, [(st.PHONE_OUT, START, None), (st.PHONE_IN, START + timedelta(minutes=7), 7)])
    events = st.peek(cfg, today=START.date()).events
    assert events == [{"at": "2026-10-12T10:00:00-05:00", "kind": st.PHONE_OUT},
                      {"at": "2026-10-12T10:07:00-05:00", "kind": st.PHONE_IN, "detail": "7 min"}]


def test_ffmpeg_reads_small_raw_frames():
    cmd = camera.ffmpeg_command("/dev/video2", 2.0)
    assert cmd[cmd.index("-i") + 1] == "/dev/video2"
    assert "fps=1/2.0,scale=224:224:flags=bilinear" in cmd
    assert cmd[-5:] == ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    assert "mjpeg" in cmd and "mjpeg" not in camera.ffmpeg_command("/dev/video0", 2.0, mjpeg=False)


class FakeProc:
    def __init__(self, data=b""):
        self.stdout = io.BytesIO(data)
        self.terminated = False

    def poll(self):
        return 1

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_camera_reads_whole_frames_and_says_when_it_stops():
    frame = bytes(range(256)) * (224 * 224 * 3 // 256)
    cam = camera.Camera("/dev/video0", 2, popen=lambda cmd, **kw: FakeProc(frame))
    assert cam.read() == frame
    with pytest.raises(camera.CameraError, match="camera stopped"):
        cam.read()


class FakeCamera:
    def __init__(self):
        self.closed = False

    def read(self):
        return b"frame"

    def close(self):
        self.closed = True


class Done(Exception):
    pass


class Clock:
    """Times for watch(): each read gives the next one, then stops the loop."""

    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        if not self.times:
            raise Done
        return self.times.pop(0)


def run_watch(cfg, labels, times, sleeps=None):
    cams = []
    script = iter(labels)

    def open_camera():
        cams.append(FakeCamera())
        return cams[-1]

    times = list(times[:1]) + list(times)   # watch() first reads the clock to tidy up
    with pytest.raises(Done):
        camera.watch(cfg, lambda frame: next(script), [P, W, A], open_camera, BLOCKS,
                     clock=Clock(times), sleep=(sleeps.append if sleeps is not None else lambda s: None))
    return cams


def test_watch_logs_phone_use_and_tidies_up_when_it_stops(cfg):
    labels = [W] * 5 + [P] * 30
    # Two clock reads per frame: one to check it should run, one to stamp the frame.
    times = [START + timedelta(seconds=2 * i) for i in range(len(labels)) for _ in (0, 1)]
    cams = run_watch(cfg, labels, times)
    assert len(cams) == 1 and cams[0].closed
    kinds = [e["kind"] for e in st.peek(cfg, today=START.date()).events]
    assert kinds == [st.PHONE_OUT, st.PHONE_IN]       # closed when the service stopped
    assert not presence.camera_path(cfg).exists()     # nobody thinks it still runs


def test_watch_writes_what_it_sees_for_presence(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(presence, "record_camera",
                        lambda c, label, since, now, sees_away: seen.append((label, now, sees_away)))
    labels = [W] * 20
    times = [START + timedelta(seconds=2 * i) for i in range(len(labels)) for _ in (0, 1)]
    run_watch(cfg, labels, times)
    # The first frame, then every 30 s while nothing changes.
    assert [s[1] - START for s in seen] == [timedelta(0), timedelta(seconds=30)]
    assert all(s[0] == W and s[2] for s in seen)


def test_watch_keeps_the_camera_off_outside_blocks_and_while_paused(cfg):
    sleeps = []
    lunch = START.replace(hour=12, minute=30)
    assert run_watch(cfg, [], [lunch, lunch], sleeps) == []
    assert sleeps == [camera.OFF_POLL_S, camera.OFF_POLL_S]
    with st.locked_state(cfg, today=START.date()) as s:
        actions.pause(s, START + timedelta(hours=1))
    assert run_watch(cfg, [], [START], sleeps) == []


def test_the_camera_runs_from_five_minutes_before_a_block_to_five_after():
    assert not camera.in_window(START.replace(hour=8, minute=54), BLOCKS)
    assert camera.in_window(START.replace(hour=8, minute=55), BLOCKS)
    assert camera.in_window(START.replace(hour=12, minute=4), BLOCKS)
    assert not camera.in_window(START.replace(hour=12, minute=5), BLOCKS)
    assert camera.in_window(START.replace(hour=17, minute=34), BLOCKS)
    assert not camera.in_window(START.replace(day=17), BLOCKS)      # Saturday


def test_phone_use_counts_only_inside_the_block():
    def watched(tracker, start, n):
        # As watch() does: phone use counts only while in a focus block.
        times = [start + timedelta(seconds=2 * i) for i in range(n)]
        return [c for at in times for c in tracker.feed(P, at, counts=blocks.in_block(at, BLOCKS))]

    t = camera.Tracker()
    changes = watched(t, START.replace(hour=8, minute=58, second=30), 80)   # 08:58:30-09:01:08
    assert changes == [(st.PHONE_OUT, START.replace(hour=9), None)]          # counted from 09:00
    t = camera.Tracker()
    changes = feed(t, [P] * 30, start=START.replace(hour=11, minute=59))
    changes += [c for i in range(5) for c in t.feed(P, START.replace(hour=12, second=2 * i), counts=False)]
    assert [c[0] for c in changes] == [st.PHONE_OUT, st.PHONE_IN]  # closed when the block ends
    assert t.seen == P                                          # it still sees what it sees


def held_checkin(cfg, at):
    with st.locked_state(cfg, today=at.date()) as s:
        s.checkins.append({"slot": "evening", "at": at.isoformat(timespec="seconds"),
                           "reply": "Good day.", "unheard": True})


def test_a_held_checkin_keeps_the_camera_on_for_two_hours(cfg):
    evening = START.replace(hour=17, minute=30)
    assert camera.why_off(cfg, evening + timedelta(minutes=10), BLOCKS) == "outside focus blocks"
    held_checkin(cfg, evening)
    assert camera.why_off(cfg, evening + timedelta(minutes=10), BLOCKS) is None
    assert camera.why_off(cfg, evening + timedelta(minutes=119), BLOCKS) is None
    later = evening + timedelta(minutes=121)
    assert camera.why_off(cfg, later, BLOCKS) == "outside focus blocks"
    c = st.peek(cfg, today=START.date()).checkins[-1]
    assert c["unheard"] is False and c["expired"] is True          # given up on, still on screen


def test_a_held_checkin_waits_out_a_pause(cfg):
    held_checkin(cfg, START.replace(hour=17, minute=30))
    with st.locked_state(cfg, today=START.date()) as s:
        actions.pause(s, START.replace(hour=18))
    assert camera.why_off(cfg, START.replace(hour=17, minute=45), BLOCKS) == "paused"
    assert camera.why_off(cfg, START.replace(hour=18, minute=1), BLOCKS) is None


def test_inside_its_window_a_held_checkin_is_never_given_up(cfg):
    held_checkin(cfg, START.replace(hour=9))
    assert camera.why_off(cfg, START.replace(hour=11, minute=30), BLOCKS) is None
    assert st.peek(cfg, today=START.date()).checkins[-1]["unheard"] is True


def test_watch_closes_the_camera_when_the_window_ends(cfg):
    end = START.replace(hour=12, minute=4, second=58)
    times = [end, end, end + timedelta(seconds=2)]
    cams = run_watch(cfg, [W], times)
    assert len(cams) == 1 and cams[0].closed


def test_a_session_left_open_is_closed_at_start(cfg):
    camera.record(cfg, [(st.PHONE_OUT, START, None)])
    # The run that died last saw the phone at 10:06 ...
    presence.record_camera(cfg, P, START, START + timedelta(minutes=6), sees_away=True)
    camera.close_stale(cfg, START + timedelta(minutes=30))
    events = st.peek(cfg, today=START.date()).events
    assert events[-1] == {"at": "2026-10-12T10:06:00-05:00", "kind": st.PHONE_IN, "detail": "6 min"}
    assert st.phone_in_use_since(events) is None
    assert not presence.camera_path(cfg).exists()
    camera.close_stale(cfg, START + timedelta(minutes=31))     # nothing left to close
    assert len(st.peek(cfg, today=START.date()).events) == 2


def test_a_stale_session_ends_when_the_camera_started_seeing_something_else(cfg):
    camera.record(cfg, [(st.PHONE_OUT, START, None)])
    presence.record_camera(cfg, W, START + timedelta(minutes=2), START + timedelta(minutes=20), sees_away=True)
    camera.close_stale(cfg, START + timedelta(minutes=21))
    assert st.peek(cfg, today=START.date()).events[-1]["detail"] == "2 min"


def test_a_stale_session_without_a_trace_closes_where_it_began(cfg):
    camera.record(cfg, [(st.PHONE_OUT, START, None)])
    camera.close_stale(cfg, START + timedelta(minutes=21))
    assert st.peek(cfg, today=START.date()).events[-1]["detail"] == "0 min"


def test_sigterm_ends_the_service_through_its_cleanup(cfg, monkeypatch):
    """systemd stops it with SIGTERM: that must become SystemExit, so
    watch()'s finally closes the session instead of the process just dying."""
    import signal
    handlers = {}
    monkeypatch.setattr(camera.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    monkeypatch.setattr(camera.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    camera.main([])                                  # no model: returns early, handler already set
    with pytest.raises(SystemExit):
        handlers[signal.SIGTERM](signal.SIGTERM, None)


def test_main_without_a_model_says_where_to_put_one(cfg, monkeypatch, caplog):
    monkeypatch.setattr(camera.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert camera.main([]) == camera.EXIT_NO_MODEL
    assert "camera" in str(camera.model_path(cfg)) and "No model" in caplog.text


class FakeFfmpeg:
    """Writes a numbered JPEG into the staging folder every 0.1 s, like ffmpeg's image2."""

    def __init__(self, cmd, stdin=None):
        self.pattern = cmd[-1]
        self.returncode = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        n = 1
        while not self._stop.wait(0.1):
            Path(self.pattern.replace("%06d", f"{n:06d}")).write_bytes(b"jpg")
            n += 1

    def poll(self):
        return self.returncode

    def terminate(self):
        self._stop.set()
        self.returncode = 0

    def wait(self):
        return 0


def test_collect_sorts_frames_by_what_you_typed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(camera, "COLLECT_SETTLE", timedelta(seconds=0.2))

    def keys():
        for delay, line in [(0.5, "p\n"), (1.0, "a\n"), (1.0, "s\n"), (0.3, "huh\n"), (0, "q\n")]:
            time.sleep(delay)
            yield line

    out = tmp_path / "frames"
    assert camera.collect("/dev/video0", out, 0.1, keys=keys(), popen=FakeFfmpeg) == 0
    counts = {label: len(list((out / label).glob("*.jpg"))) for label in (P, W, A)}
    assert 3 <= counts[P] <= 11 and 3 <= counts[A] <= 11 and counts[W] == 0
    assert "p, w, a, s or q" in capsys.readouterr().out
