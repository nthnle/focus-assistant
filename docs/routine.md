# Day to day

What to do, and when. The same on one page, to print or keep on your
phone: [diagrams/cheatsheet.png](diagrams/cheatsheet.png).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/cheatsheet-dark.png">
  <img alt="Using Focus Assistant: a desk card with what to do every workday, every week, and now and then" src="diagrams/cheatsheet.png">
</picture>

Commands run on the Pi (`ssh` in). Most things can also be said to the
coach, from your phone ("Hey Siri, Coach") or at the desk ("Hey Jarvis").

## Every workday

| When | Do |
|---|---|
| Before 09:00 | Put up to three priorities on the list: `focus add Write the spec`, or "add email Sam". Phone on its charger. |
| 09:00 | Listen to the morning check-in and start with what it names. |
| Focus time | Leave the phone on the charger. Stand up when the :50 reminder says so. Finished something? Press the speakerphone's **Call** button (marks the top priority done), or say "done with the report". |
| A meeting or call | "Quiet until 11", or `focus pause 11:00`. Check-ins wait until it ends; stretch reminders are skipped. |
| 13:00 | Midday check-in. If you drifted, take the one reset it gives you. |
| 17:30 | Evening check-in. Add tomorrow's first thing now (it won't count as slipping). Phone away for the night. |

## Every week

| When | Do |
|---|---|
| Monday | Set one to three goals for the week: `focus goal add Ship the beta`, or tell the coach. Goals set on a Saturday or Sunday count for the week ahead. |
| Something not for today | `focus add --on wed Book the dentist`, or `focus later 3` (to "someday"). `focus now 3` brings it back. |
| A day off | "Off until Monday", or `focus pause monday`. No check-ins, no cost, nothing counts as slipping. |
| Friday 17:30 | The wrap-up: which goals were met, what slipped, and one routine tip. Try the tip the next week; to move focus blocks, edit `FOCUS_BLOCKS` in `/etc/focus-assistant/focus.env`. Ask for more any time: "how could I improve my routine?" |

## Now and then

| When | Do |
|---|---|
| A reply misses | `focus rate bad "it's Sunday"`, or tell the coach "that was wrong". A good one: `focus rate good`. |
| Every two weeks | `focus feedback`, then bring the output to a Claude Code session to improve the coach. |
| Monthly | Update the Pi ([install.md, Updating](install.md#updating)). Check that new copies are arriving in `~/focus-backups` on the PC ([backup.md](backup.md)). `focus status` shows the day's API cost. |
| Something's off | `focus status`, then [Troubleshooting](install.md#troubleshooting). |

## Quick reference

```bash
focus show            # the list, numbered; * marks the top three
focus last            # the latest check-in
focus done 2          # by its number in `focus show`
focus move 4 1        # to the top
focus edit 2 Email Sam the budget
focus resume          # end a pause
focus status          # services, today, goals, pauses, replies, cost
```
