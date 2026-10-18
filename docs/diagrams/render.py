"""Render every diagram in this folder to PNG, light and dark.

    pip install playwright && playwright install chromium
    python docs/diagrams/render.py              # all diagrams
    python docs/diagrams/render.py workflow     # just workflow.html

Each NAME.html becomes NAME.png and NAME-dark.png (1600x1000), which
docs/architecture.md and README.md show. Set FOCUS_CHROMIUM to a Chromium
binary to use that instead of Playwright's own download.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1600, 1000


def main(names: list[str]) -> int:
    pages = [HERE / f"{n}.html" for n in names] if names else sorted(HERE.glob("*.html"))
    missing = [p.name for p in pages if not p.exists()]
    if missing:
        print(f"render: no such diagram: {', '.join(missing)}", file=sys.stderr)
        return 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("FOCUS_CHROMIUM") or None)
        for page_path in pages:
            for scheme, suffix in (("light", ""), ("dark", "-dark")):
                page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                        color_scheme=scheme, device_scale_factor=1)
                page.goto(page_path.as_uri(), wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                out = HERE / f"{page_path.stem}{suffix}.png"
                page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
                page.close()
                print(f"wrote {out.relative_to(HERE.parent.parent)}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
