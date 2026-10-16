from focus import display
from focus.state import Priority, State


def test_top_three_open_with_carried_tag():
    s = State(date="2026-10-16", priorities=[
        Priority(1, "Done one", done_at="x"),
        Priority(2, "Spec", carried_days=3),
        Priority(3, "Email"), Priority(4, "Report"), Priority(5, "Fourth open"),
    ])
    page = display.render_html(s, "2026-10-16T09:05:00-05:00")
    assert "Spec" in page and '<span class="c">3d</span>' in page
    assert "Report" in page and "Fourth open" not in page and "Done one" not in page
    assert "NO CHECK-IN YET" in page
    assert "Fri 16 Oct" in page and "Updated 09:05" in page


def test_empty_state():
    page = display.render_html(State(date="2026-10-16"), "2026-10-16T09:05:00-05:00")
    assert "No open priorities" in page
