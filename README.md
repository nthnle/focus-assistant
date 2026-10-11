# focus-assistant

A desk focus coach for a Raspberry Pi 4. Three times each weekday it reads
your priorities and the day's log, asks Claude for a short nudge, prints it
and saves the result. It only makes outbound HTTPS calls and listens on no
ports.

## Layout

```
focus/
  config.py         settings from env vars (FOCUS_*); never touches the key
  state.py          state.json: locked read-modify-write, atomic save, day rollover
  actions.py        every state change (add/done/move/report/log_event); all inputs call these
  coach.py          system prompt + the one Anthropic API call
  checkin.py        snapshot -> coach -> record -> outputs
  cli.py            `focus` command
  outputs/          where a check-in goes   (console, inky; speech, led later)
  inputs/           what feeds actions      (buttons, dock, voice later)
deploy/
  focus-checkin.service / .timer, focus.env.example, install.sh
tests/
```

The core (`state`, `actions`, `coach`, `checkin`) imports no hardware
library. Hardware attaches at two points only:

- **Outputs** implement `checkin(state, slot, reply)` and are enabled by
  name with `FOCUS_OUTPUTS=console,inky,...`. A failing output is logged and
  skipped; it never stops the others.
- **Inputs** are separate long-running services that call `focus.actions`
  inside `focus.state.locked_state()`. The file lock lets them and the timer
  write safely, and a check-in holds the lock only around its reads and
  writes, never during the API call.

## Install on the Pi

Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager. In its
settings, set the hostname, user, Wi-Fi, SSH and **your time zone**: the
timer runs on the Pi's clock. After flashing, Windows shows only the 512 MB
boot partition; the Linux one is hidden, so cancel any offer to format it.

```bash
ssh <user>@<hostname>.local
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv fonts-dejavu-core
timedatectl                                   # check the time zone
```

The repo is private, so give the Pi a read-only deploy key rather than your
account (GitHub no longer accepts passwords for Git):

```bash
ssh-keygen -t ed25519 -C "focus-pi" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub     # add under repo Settings > Deploy keys, no write access
git clone git@github.com:nthnle/focus-assistant.git && cd focus-assistant
sudo deploy/install.sh
sudo nano /etc/focus-assistant/focus.env      # set ANTHROPIC_API_KEY
sudo systemctl start focus-checkin            # one check-in now
journalctl -u focus-checkin -n 20
```

Use a separate API key for the Pi so it can be revoked on its own. To update
later: `git pull && sudo deploy/install.sh` (safe to re-run; it keeps the key
file).

The key file is `root:root 0600`. systemd reads it as root and starts the
check-in as your user, in a sandbox: read-only system, home read-only except
the state directory, no capabilities, IP and Unix sockets only.

Schedule: Mon to Fri at 09:00, 13:00 and 17:30. Change the times with
`sudo systemctl edit focus-checkin.timer`. The slot comes from the time of day
(before 11:00 is morning, before 16:00 midday, otherwise evening).

## Day-to-day

```bash
focus add Finish the quarterly report
focus show            # * marks the top 3, which is what the display will show
focus move 4 1        # put #4 at the top
focus done 1
focus report drifted  # or on-track
focus drop 3
```

At the first use each new day, unfinished priorities carry over with
`carried_days` + 1, finished ones go into `history` (the last 14 days), and
the day's check-ins and events are cleared. The coach is told to raise
anything carried for 3+ days.

Settings (in `focus.env`): `FOCUS_MODEL` (default `claude-sonnet-5-5`; Opus 5.5 coached slightly better at about 3x the cost),
`FOCUS_EFFORT` (default `low`; it is a short reply), `FOCUS_OUTPUTS`,
`FOCUS_HISTORY_DAYS`. Each check-in records its token use in `state.json`.

## Later phases: where each piece plugs in

| Piece | Module | Kind | Talks to core via |
|---|---|---|---|
| Inky Impression 4" | `outputs/inky.py` | output | renders top 3 open priorities + last reply on each check-in; also redraws after a button action |
| Buttons A-D | `inputs/buttons.py` (service) | input | A `complete_priority(top)`, B `report_on_track`, C `report_drifted`, D `log_event(SNOOZE)` |
| Phone dock switch | `inputs/dock.py` (service) | input | `log_event(PHONE_OUT/PHONE_IN)`; only counts during focus blocks, which need a small `blocks` field in state |
| LED + buzzer | `outputs/led.py` | output | pulse on check-in; a snooze event delays the next nudge |
| Push-to-talk voice | `inputs/voice.py` (service) + `outputs/speech.py` | both | whisper.cpp base.en -> intent -> an action or an ad-hoc check-in; Piper speaks the reply |

The system prompt already keeps replies plain, short and markdown-free, so
the same text works on e-ink and through Piper.

Each later phase needs:

- **GPIO and SPI:** the `inky` output runs inside the check-in service, so
  that unit needs `DeviceAllow=` for `/dev/spidev0.0` and `/dev/gpiochip0`
  (see the comment in the unit). The input services get their own units.
- **Refreshes:** a full refresh on the Impression takes about 30 s, so
  redraw only when the top 3 or the reply changes.
- **Voice:** whisper.cpp and Piper run fully on the Pi. Only the coach
  call goes out.

### Display (`outputs/inky.py`)

Written and tested; waiting on the panel. It draws the slot and date, the
top 3 open priorities (red tag = days carried) and the reply, which shrinks
from 24 px to 16 px to fit and is cut with an ellipsis past that. A typical
70-word reply fits at about 22 px.

Preview the layout on any machine, without the panel or an API call:

```bash
.venv/bin/python -m focus.outputs.inky preview.png    # redraws the last check-in
```

`FOCUS_OUTPUTS=console,inky` with `FOCUS_INKY_PNG=preview.png` sends real
check-ins to a PNG instead of the panel. When the panel arrives:

```bash
sudo raspi-config nonint do_spi 0                     # enable SPI
sudo /opt/focus-assistant/venv/bin/pip install "/home/<user>/focus-assistant[display]"
```

then set `FOCUS_OUTPUTS=console,inky` in `focus.env` and make the unit change
described under GPIO and SPI above.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests mock the API, so they need no key and no network.
