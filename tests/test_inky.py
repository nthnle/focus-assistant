import pytest

pytest.importorskip("PIL")

from focus.outputs import inky  # noqa: E402
from focus.state import Priority, State  # noqa: E402

REPLY = "Start with the parser. The email to Sam can wait until after lunch."


def make_state(*texts, carried=0):
    return State(
        date="2026-10-12",
        priorities=[Priority(i, t, carried_days=carried) for i, t in enumerate(texts, 1)],
    )


def test_render_is_panel_sized():
    img = inky.render(make_state("Write spec", "Email Sam"), "morning", REPLY)
    assert img.size == inky.SIZE
    assert img.mode == "RGB"


def test_render_handles_no_priorities_and_long_text():
    inky.render(make_state(), "evening", "")
    inky.render(make_state("x" * 300, carried=5), "midday", "word " * 500)


def test_short_reply_uses_largest_font():
    font, lines, _, truncated = inky.fit_reply(REPLY, 592, 170)
    assert font.size == inky.REPLY_MAX_SIZE
    assert not truncated


def test_overlong_reply_is_truncated_at_smallest_font():
    font, lines, line_height, truncated = inky.fit_reply("word " * 500, 592, 170)
    assert truncated
    assert font.size == inky.REPLY_MIN_SIZE
    assert len(lines) * line_height <= 170
    assert lines[-1].endswith("…") and not lines[-1].endswith("……")


def test_redraws_only_when_the_items_shown_or_reply_change(tmp_path, monkeypatch):
    shown = []
    out = inky.InkyOutput(tmp_path / "inky.last")
    monkeypatch.setattr(out, "_show", shown.append)
    s = make_state("Write spec", "Email Sam", "Book dentist", "Fourth", "Fifth", "Sixth", "Seventh")

    out.checkin(s, "morning", REPLY)
    out.checkin(s, "morning", REPLY)
    assert len(shown) == 1

    s.priorities[6].text = "Seventh, renamed"  # only counted in "+2 more"
    out.checkin(s, "midday", REPLY)
    assert len(shown) == 1

    out.checkin(s, "midday", "A new nudge.")
    s.priorities[0].done_at = "2026-10-12T10:00:00"
    out.checkin(s, "midday", "A new nudge.")
    assert len(shown) == 3


def test_png_mode_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FOCUS_INKY_PNG", str(tmp_path / "out.png"))
    inky.create().checkin(make_state("Write spec"), "morning", REPLY)
    assert (tmp_path / "out.png").stat().st_size > 0
