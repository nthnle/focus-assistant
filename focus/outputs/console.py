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
            for p in open_[:3]:
                carried = f" (carried {p.carried_days}d)" if p.carried_days else ""
                print(f"  {p.id}. {p.text}{carried}")
            if len(open_) > 3:
                print(f"  ...and {len(open_) - 3} more")


def create() -> ConsoleOutput:
    return ConsoleOutput()
