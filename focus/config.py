"""Runtime settings, all from environment variables so systemd can set them.

The model provider's key is deliberately not read here: only
`coach.make_client()` reads FOCUS_LLM_API_KEY, which systemd injects from a
root-only EnvironmentFile. Nothing in this package ever writes or logs it.
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
        # The provider's model name; there is no default, as every host names models differently.
        model=os.environ.get("FOCUS_MODEL", ""),
        # Chat and voice may use a quicker, cheaper model; the same one if unset.
        chat_model=os.environ.get("FOCUS_CHAT_MODEL") or os.environ.get("FOCUS_MODEL", ""),
        # Sent as reasoning_effort, which only reasoning models accept.
        effort=os.environ.get("FOCUS_EFFORT", ""),
        outputs=tuple(o.strip() for o in outputs.split(",") if o.strip()),
        history_days=int(os.environ.get("FOCUS_HISTORY_DAYS", "14")),
    )


def words(text: str) -> str:
    """What the logs may show of something the user said, typed or listed.

    The journal outlives the day's state and is readable by admins, so by
    default it gets only the length; FOCUS_LOG_WORDS=1 logs the words, which
    helps when tuning voice.
    """
    if os.environ.get("FOCUS_LOG_WORDS") == "1":
        return repr(text)
    return f"<{len(text)} chars>"
