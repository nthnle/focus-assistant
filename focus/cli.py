"""`focus` command: run a check-in, or edit priorities until hardware exists."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from . import actions, checkin, config, days, pause, state as st, status
from .coach import CoachError


def _show(s: st.State) -> None:
    """Numbered by position: the numbers the other commands take."""
    quiet = pause.describe(s, datetime.now().astimezone())
    if quiet:
        print(f"Paused {quiet}. Resume with: focus resume")
    if s.goals:
        print("This week:")
        for i, g in enumerate(s.goals, 1):
            print(f"  [{'x' if g.get('done_at') else ' '}] {i}. {g['text']}")
        print()
    if not s.priorities:
        print("No priorities yet. Add one with: focus add \"...\"")
        return
    top = {p.id for p in s.open_priorities[:3]}  # what the display and the coach treat as the top three
    today = s.today_list
    if not today:
        print("Nothing on today's list.")
    for i, p in enumerate(s.ordered, 1):
        if i == len(today) + 1:
            print("Later:")
        mark = "x" if p.done else " "
        carried = f"  (carried {p.carried_days}d)" if p.carried_days else ""
        when = "" if p.due(s.date) else f"  ({_on(p.on)})"
        star = "*" if p.id in top else " "
        print(f"{star}[{mark}] {i}. {p.text}{carried}{when}")


def _on(on: str) -> str:
    return "someday" if on == st.LATER else f"{date.fromisoformat(on):%a %d %b}"


def _at(s: st.State, number: int) -> st.Priority:
    """The priority shown as `number` by `focus show`."""
    order = s.ordered
    if not 1 <= number <= len(order):
        raise st.StateError(f"No priority {number}; `focus show` lists them")
    return order[number - 1]


def _goal_at(s: st.State, number: int) -> dict:
    if not 1 <= number <= len(s.goals):
        raise st.StateError(f"No goal {number}; `focus goals` lists them")
    return s.goals[number - 1]


def _day(s: st.State, words: list[str] | None) -> date | str:
    """A day for `--on` or `focus later`: none given means "later"."""
    text = " ".join(words or []).strip()
    if not text or text.lower() == st.LATER:
        return st.LATER
    day = days.parse_day(text, date.fromisoformat(s.date))
    if day is None:
        raise st.StateError(f"Can't tell which day {text!r} is; try tomorrow, wed, friday or 2026-10-24")
    return day


def _last(s: st.State) -> None:
    if not s.checkins:
        print("No check-in yet today.")
        return
    c = s.checkins[-1]
    print(f"[{c['slot']} check-in, {c['at'][11:16]}]")
    print(c.get("reply") or "(no reply recorded)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="focus", description="Desk focus coach")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("checkin", help="run a coach check-in (what the timer calls)")
    p.add_argument("--slot", choices=checkin.SLOTS, help="default: from the time of day")

    sub.add_parser("show", help="list this week's goals, today's priorities and later ones")
    sub.add_parser("last", help="print the latest check-in reply")
    sub.add_parser("status", help="services, today, presence and API cost at a glance")
    p = sub.add_parser("add", help="add a priority")
    p.add_argument("--on", metavar="DAY", help="for another day: tomorrow, wed, 2026-10-24, later")
    p.add_argument("text", nargs="+")
    p = sub.add_parser("later", help="move a priority off today's list, to a day or to someday")
    p.add_argument("number", type=int)
    p.add_argument("day", nargs="*", help="tomorrow, wed, 2026-10-24 (default: someday)")
    p = sub.add_parser("now", help="bring a later priority back to today")
    p.add_argument("number", type=int)
    sub.add_parser("goals", help="this week's goals")
    p = sub.add_parser("goal", help="this week's goals: add, done or drop one")
    p.add_argument("action", choices=["add", "done", "drop"])
    p.add_argument("what", nargs="+", help="the goal's text (add) or its number (done, drop)")
    for name, help_ in [("done", "mark done"), ("undo", "mark not done"), ("drop", "remove")]:
        p = sub.add_parser(name, help=f"{help_} a priority (its number in `focus show`)")
        p.add_argument("number", type=int)
    p = sub.add_parser("edit", help="change a priority's wording")
    p.add_argument("number", type=int)
    p.add_argument("text", nargs="+")
    p = sub.add_parser("move", help="move a priority to a position (1 = top)")
    p.add_argument("number", type=int)
    p.add_argument("position", type=int)
    p = sub.add_parser("pause", help="hold check-ins and stretch reminders: 11:00, 45m, tomorrow, monday ...")
    p.add_argument("until", nargs="+")
    sub.add_parser("resume", help="end a pause now")
    p = sub.add_parser("report", help="log how the current block is going")
    p.add_argument("how", choices=["on-track", "drifted"])

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = config.load()

    try:
        if args.cmd == "checkin":
            if checkin.run(cfg, args.slot) is None:
                print("Paused for today; no check-in. `focus resume` ends the pause.")
            return 0

        with st.locked_state(cfg) as s:
            if args.cmd == "last":
                _last(s)
                return 0
            if args.cmd == "status":
                print(status.report(cfg, s, datetime.now().astimezone()))
                return 0
            if args.cmd == "add":
                on = _day(s, [args.on]) if args.on is not None else None
                actions.add_priority(s, " ".join(args.text), on)
            elif args.cmd == "later":
                actions.schedule_priority(s, _at(s, args.number).id, _day(s, args.day))
            elif args.cmd == "now":
                actions.schedule_priority(s, _at(s, args.number).id, None)
            elif args.cmd == "goals":
                pass
            elif args.cmd == "goal":
                if args.action == "add":
                    actions.add_goal(s, " ".join(args.what))
                else:
                    if len(args.what) != 1 or not args.what[0].isdigit():
                        raise st.StateError(f"focus goal {args.action} takes a goal number")
                    goal = _goal_at(s, int(args.what[0]))
                    (actions.complete_goal if args.action == "done" else actions.drop_goal)(s, goal["id"])
            elif args.cmd == "done":
                actions.complete_priority(s, _at(s, args.number).id)
            elif args.cmd == "undo":
                actions.reopen_priority(s, _at(s, args.number).id)
            elif args.cmd == "drop":
                actions.drop_priority(s, _at(s, args.number).id)
            elif args.cmd == "edit":
                actions.rename_priority(s, _at(s, args.number).id, " ".join(args.text))
            elif args.cmd == "move":
                actions.move_priority(s, _at(s, args.number).id, args.position)
            elif args.cmd == "pause":
                try:
                    until = pause.parse_until(" ".join(args.until), datetime.now().astimezone())
                except ValueError as exc:
                    raise st.StateError(str(exc)) from exc
                actions.pause(s, until)
            elif args.cmd == "resume":
                actions.resume(s)
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
