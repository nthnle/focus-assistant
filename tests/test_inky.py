import pytest

pytest.importorskip("PIL")

from focus import actions, config, state as st  # noqa: E402
from focus.outputs import inky  # noqa: E402
from focus.state import Priority, State  # noqa: E402

REPLY = "Start with the parser. The email to Sam can wait until after lunch."


def make_state(*texts, carried=0):
    return State(
        date="2026-10-12",
        priorities=[Priority(i, t, carried_days=carried) for i, t in enumerate(texts, 1)],
    )


class FakePanel:
    def __init__(self, size=inky.SIZE):
        self.size = size
        self.drawn = []

    def show(self, img):
        assert img.size == self.size
        self.drawn.append(img)


def test_render_is_panel_sized():
    img = inky.render(make_state("Write spec", "Email Sam"), "morning", REPLY)
    assert img.size == inky.SIZE == (600, 400)
    assert img.mode == "RGB"
    assert inky.render(make_state("Write spec"), "morning", REPLY, size=(640, 400)).size == (640, 400)


def test_render_handles_no_items_no_check_in_and_long_text():
    inky.render(make_state(), "evening", "")
    inky.render(make_state(), None, "The first check-in of the day will appear here.")
    inky.render(make_state("x" * 300, carried=5), "midday", "word " * 500, since="Since 09:00: done " + "y" * 200)


def test_short_reply_uses_largest_font():
    font, lines, _, truncated = inky.fit_reply(REPLY, 552, 170)
    assert font.size == inky.REPLY_MAX_SIZE
    assert not truncated


def test_overlong_reply_is_truncated_at_smallest_font():
    font, lines, line_height, truncated = inky.fit_reply("word " * 500, 552, 170)
    assert truncated
    assert font.size == inky.REPLY_MIN_SIZE
    assert len(lines) * line_height <= 170
    assert lines[-1].endswith("…") and not lines[-1].endswith("……")


def test_redraws_only_when_the_picture_changes(tmp_path):
    panel = FakePanel()
    out = inky.InkyOutput(tmp_path / "inky.last", panel)
    s = make_state("Write spec", "Email Sam", "Book dentist", "Fourth", "Fifth", "Sixth", "Seventh")

    out.checkin(s, "morning", REPLY)
    out.checkin(s, "morning", REPLY)
    assert len(panel.drawn) == 1

    s.priorities[6].text = "Seventh, renamed"  # only counted in "+2 more"
    out.checkin(s, "morning", REPLY)
    assert len(panel.drawn) == 1

    out.checkin(s, "midday", REPLY)           # the header changes
    s.priorities[0].done_at = "2026-10-12T10:00:00"
    out.checkin(s, "midday", REPLY)           # an item leaves the list
    assert len(panel.drawn) == 3


def test_draws_at_the_attached_panels_size(tmp_path):
    panel = FakePanel(size=(640, 400))   # the older 4" model
    inky.InkyOutput(tmp_path / "inky.last", panel).checkin(make_state("Write spec"), "morning", REPLY)
    assert panel.drawn[0].size == (640, 400)


def test_png_mode_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FOCUS_INKY_PNG", str(tmp_path / "out.png"))
    inky.create().checkin(make_state("Write spec"), "morning", REPLY)
    assert (tmp_path / "out.png").stat().st_size > 0


# --- the focus-display watcher ---------------------------------------------

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


class Script:
    """Drives `watch` with a fake clock: each poll runs the next step first."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def running(self):
        if not self.steps:
            return False
        step = self.steps.pop(0)
        if step:
            step()
        return True


def test_watch_draws_once_changes_settle(cfg):
    panel = FakePanel()

    def add(text):
        def step():
            with st.locked_state(cfg) as s:
                actions.add_priority(s, text)
        return step

    # Two quick changes 5 s apart, then quiet: one redraw, not two.
    script = Script(add("Write spec"), add("Email Sam"), *[None] * 10)
    inky.watch(cfg, panel, quiet_s=30, poll_s=5, clock=script.clock, sleep=script.sleep, running=script.running)
    assert len(panel.drawn) == 1
    assert (cfg.state_dir / "inky.last").is_file()

    # A restarted watcher draws once even with nothing changed: something
    # else (a test pattern) may be on the panel now. Then it stays quiet.
    again = Script(*[None] * 20)
    inky.watch(cfg, panel, quiet_s=30, poll_s=5, clock=again.clock, sleep=again.sleep, running=again.running)
    assert len(panel.drawn) == 2


def test_watch_waits_out_a_steady_stream_of_changes(cfg):
    panel = FakePanel()
    n = iter(range(100))

    def add():
        with st.locked_state(cfg) as s:
            actions.add_priority(s, f"Item {next(n)}")

    script = Script(*[add] * 10)   # a change every poll, never 30 s of quiet
    inky.watch(cfg, panel, quiet_s=30, poll_s=5, clock=script.clock, sleep=script.sleep, running=script.running)
    assert panel.drawn == []


def test_watch_keeps_going_when_the_panel_fails(cfg):
    class Flaky(FakePanel):
        def show(self, img):
            super().show(img)
            if len(self.drawn) == 1:
                raise OSError("busy wait timed out")

    panel = Flaky()
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Write spec")
    script = Script(*[None] * 30)
    inky.watch(cfg, panel, quiet_s=30, poll_s=5, clock=script.clock, sleep=script.sleep, running=script.running)
    assert len(panel.drawn) == 2   # failed once, drew on the retry
