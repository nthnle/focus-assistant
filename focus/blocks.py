"""Focus blocks: the times on work days (FOCUS_WORKDAYS) when phone use counts and stretch reminders play.

Set with FOCUS_BLOCKS, e.g. "09:00-12:00,13:00-17:30" (the default).
"""

from __future__ import annotations

import os
from datetime import datetime, time as dtime

from . import days

DEFAULT_BLOCKS = "09:00-12:00,13:00-17:30"

Block = tuple[dtime, dtime]


def parse_blocks(text: str) -> list[Block]:
    blocks = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            start, end = (dtime.fromisoformat(t.strip()) for t in part.split("-"))
        except ValueError as exc:
            raise ValueError(f"FOCUS_BLOCKS: bad block {part!r}, expected HH:MM-HH:MM") from exc
        if start >= end:
            raise ValueError(f"FOCUS_BLOCKS: block {part!r} ends before it starts")
        blocks.append((start, end))
    return blocks


def from_env() -> list[Block]:
    return parse_blocks(os.environ.get("FOCUS_BLOCKS", DEFAULT_BLOCKS))


def in_block(now: datetime, blocks: list[Block]) -> bool:
    if not days.is_workday(now.date()):
        return False
    t = now.time()
    return any(start <= t < end for start, end in blocks)
