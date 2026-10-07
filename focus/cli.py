"""`focus` command: run a check-in, or edit priorities until hardware exists."""

from __future__ import annotations

import argparse
import logging
import sys

from . import actions, checkin, config, state as st
from .coach import CoachError


def _show(s: st.State) -> None:
    if not s.priorities:
        print("No priorities yet. Add one with: focus add \"...\"")
        return
    for i, p in enumerate(s.priorities, 1):
        mark = "x" if p.done else " "
        carried = f"  (carried {p.carried_days}d)" if p.carried_days else ""
        top = "*" if i <= 3 and not p.done else " "
        print(f"{top}[{mark}] {p.id}. {p.text}{carried}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="focus", description="Desk focus coach")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("checkin", help="run a coach check-in (what the timer calls)")
    p.add_argument("--slot", choices=checkin.SLOTS, help="default: from the time of day")

    sub.add_parser("show", help="list today's priorities")
    p = sub.add_parser("add", help="add a priority")
    p.add_argument("text", nargs="+")
    for name, help_ in [("done", "mark done"), ("undo", "mark not done"), ("drop", "remove")]:
        p = sub.add_parser(name, help=f"{help_} a priority")
        p.add_argument("id", type=int)
    p = sub.add_parser("move", help="move a priority to a position (1 = top)")
    p.add_argument("id", type=int)
    p.add_argument("position", type=int)
    p = sub.add_parser("report", help="log how the current block is going")
    p.add_argument("how", choices=["on-track", "drifted"])

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = config.load()

    try:
        if args.cmd == "checkin":
            checkin.run(cfg, args.slot)
            return 0

        with st.locked_state(cfg) as s:
            if args.cmd == "add":
                pr = actions.add_priority(s, " ".join(args.text))
                print(f"Added {pr.id}. {pr.text}")
            elif args.cmd == "done":
                actions.complete_priority(s, args.id)
            elif args.cmd == "undo":
                actions.reopen_priority(s, args.id)
            elif args.cmd == "drop":
                actions.drop_priority(s, args.id)
            elif args.cmd == "move":
                actions.move_priority(s, args.id, args.position)
            elif args.cmd == "report":
                if args.how == "on-track":
                    actions.report_on_track(s)
                else:
                    actions.report_drifted(s)
            _show(s)
        return 0
    except (st.StateError, CoachError) as exc:
        print(f"focus: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
