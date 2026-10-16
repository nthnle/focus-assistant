"""Runtime settings, all from environment variables so systemd can set them.

The API key is deliberately not read here: the Anthropic SDK reads
ANTHROPIC_API_KEY itself, and systemd injects it from a root-only
EnvironmentFile. Nothing in this package ever writes or logs it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "focus-assistant"


@dataclass(frozen=True)
class Config:
    state_dir: Path
    model: str
    chat_model: str
    effort: str
    outputs: tuple[str, ...]
    history_days: int

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "state.lock"


def load() -> Config:
    state_dir = os.environ.get("FOCUS_STATE_DIR")
    outputs = os.environ.get("FOCUS_OUTPUTS", "console")
    return Config(
        state_dir=Path(state_dir) if state_dir else _default_state_dir(),
        model=os.environ.get("FOCUS_MODEL", "claude-sonnet-5-5"),
        # Chat and voice want a quick, cheap reply; check-ins keep the bigger model.
        chat_model=os.environ.get("FOCUS_CHAT_MODEL", "claude-haiku-5-5"),
        effort=os.environ.get("FOCUS_EFFORT", "low"),
        outputs=tuple(o.strip() for o in outputs.split(",") if o.strip()),
        history_days=int(os.environ.get("FOCUS_HISTORY_DAYS", "14")),
    )
