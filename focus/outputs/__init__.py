"""Output adapters: where a check-in goes after the coach replies.

Each adapter is a module in this package exposing `create() -> Output`.
FOCUS_OUTPUTS picks which ones run (comma-separated, in order), so the
Inky, Piper and LED adapters can be added without touching the core:

    FOCUS_OUTPUTS=console,inky,led,speech
"""

from __future__ import annotations

import importlib
import logging
from typing import Protocol

from ..state import State

log = logging.getLogger(__name__)


class Output(Protocol):
    def checkin(self, state: State, slot: str, reply: str) -> None:
        """Present a finished check-in. `state` already includes it."""


def load(names: tuple[str, ...]) -> list[Output]:
    outputs = []
    for name in names:
        module = importlib.import_module(f"{__name__}.{name}")
        outputs.append(module.create())
    return outputs


def deliver(outputs: list[Output], state: State, slot: str, reply: str) -> None:
    # One broken display or speaker must not stop the others.
    for out in outputs:
        try:
            out.checkin(state, slot, reply)
        except Exception:
            log.exception("Output %s failed", type(out).__name__)
