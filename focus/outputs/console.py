"""Print the check-in. Under systemd this lands in the journal."""

from __future__ import annotations

from ..state import State


class ConsoleOutput:
    def checkin(self, state: State, slot: str, reply: str) -> None:
        print(f"[{slot} check-in]")
        print(reply)
        open_ = state.open_priorities
        if open_:
            print()
            number = {p.id: i for i, p in enumerate(state.ordered, 1)}  # as `focus show` numbers them
            for p in open_:
                carried = f" (carried {p.carried_days}d)" if p.carried_days else ""
                print(f"  {number[p.id]}. {p.text}{carried}")


def create() -> ConsoleOutput:
    return ConsoleOutput()
