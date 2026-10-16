# focus-assistant

A desk focus coach for a Raspberry Pi 4. Three times each weekday it reads
your priorities and the day's log, asks Claude for a short nudge, prints it
and saves the result. Between check-ins you can chat with the coach from
your phone. The check-in only makes outbound HTTPS calls; the optional
phone services (dock, chat) are the only things that listen, on the home
network only.

## Layout

```
focus/
  config.py         settings from env vars (FOCUS_*); never touches the key
  state.py          state.json: locked read-modify-write, atomic save, day rollover
  actions.py        every state change (add/done/move/report/log_event); all inputs call these
  coach.py          check-in system prompt + the shared Anthropic API call
  chat.py           phone chat: the coach with tools that call actions
  display.py        the Inky layout as an HTML page (served by the dock service)
  checkin.py        snapshot -> coach -> record -> outputs
  cli.py            `focus` command
  outputs/          where a check-in goes   (console, inky, speech; led later)
  inputs/           what feeds actions      (dock, chat; buttons, voice later)
deploy/
  focus-checkin.service / .timer, focus-dock.service, focus-chat.service,
  focus.env.example, install.sh
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
focus last            # the latest check-in reply (or: journalctl -u focus-checkin)
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
| Phone dock (charger) | `inputs/dock.py` (service) | input | iPhone Shortcut -> `log_event(PHONE_OUT/PHONE_IN)` during `FOCUS_BLOCKS` |
| Chat (Shortcut / Siri) | `inputs/chat.py` (service) + `chat.py` | both | coach with tools for add/done/undo/drop/move/report; reply shown or spoken by the phone |
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

### Display page (`display.py`)

Until the panel arrives, the same layout is served as a web page: the latest
check-in slot and date, the top 3 open priorities (red tag = days carried)
and the reply. It is built from `state.json` on every request and refreshes
itself every 30 s, so changes from chat, the CLI or the dock show up within
the half minute. The dock service serves it (it holds no API key), read-only,
to the home network only.

```bash
git pull && sudo deploy/install.sh        # adds FOCUS_DISPLAY_TOKEN, restarts focus-dock
sudo grep FOCUS_DISPLAY_TOKEN /etc/focus-assistant/focus.env
```

Open `http://<hostname>.local:8765/display?k=<token>` in a browser and
bookmark it. On an iPhone, Share > Add to Home Screen makes it an app-like
icon. Anyone with that link on your Wi-Fi can read your priorities, so treat
it like the other tokens.

### Speech (`outputs/speech.py`)

Says each check-in reply out loud. Piper runs on the Pi (a 70-word reply
takes a second or two on a Pi 4), so only the coach call goes out. Set up for
an Anker PowerConf on USB; any ALSA speaker works. Connect the PowerConf's
USB-C port to a black USB 2.0 port on the Pi: on the blue USB 3.0 ports it
is not detected at all.

```bash
sudo apt install -y alsa-utils
sudo /opt/focus-assistant/venv/bin/pip install "/home/<user>/focus-assistant[speech]"
sudo /opt/focus-assistant/venv/bin/python -m piper.download_voices      --download-dir /opt/focus-assistant/voices en_US-lessac-medium
aplay -L | grep -i plughw          # find the PowerConf, e.g. plughw:CARD=PowerConf,DEV=0
```

In `focus.env` set `FOCUS_OUTPUTS=console,speech` and
`FOCUS_SPEECH_DEVICE=<that plughw name>`, then try it:

```bash
sudo -u <user> FOCUS_SPEECH_DEVICE=<plughw name>      /opt/focus-assistant/venv/bin/python -m focus.outputs.speech "Hello from the coach."
sudo systemctl start focus-checkin       # a real check-in, spoken
```

`python -m focus.outputs.speech` with no text repeats the last check-in.
Other voices: https://huggingface.co/rhasspy/piper-voices (set
`FOCUS_SPEECH_VOICE` to the .onnx path). Volume is the PowerConf's own
buttons.

### Phone dock (`inputs/dock.py`)

The phone sits on a wireless charger, so the iPhone itself reports when it
goes on or off: an iOS Shortcuts automation sends one request to the Pi.
The Apple Watch on the same charger is ignored.

During a focus block (`FOCUS_BLOCKS`, default `09:00-12:00,13:00-17:30`,
weekdays), taking the phone off for more than 30 s logs `phone_out` at the
time it came off, and putting it back logs `phone_in` with the minutes it was
gone. Outside the blocks nothing is logged. The coach sees these events at
the next check-in.

`focus-dock` listens on port 8765, answers only private (home network)
addresses, and needs the token that `install.sh` put in `focus.env`. It does
not get the API key. Requests are plain HTTP, so the token is only as private
as your Wi-Fi.

On the Pi:

```bash
git pull && sudo deploy/install.sh
sudo systemctl enable --now focus-dock
sudo grep FOCUS_DOCK_TOKEN /etc/focus-assistant/focus.env    # for the Shortcut
curl -i -X POST -H "Authorization: Bearer <token>" http://localhost:8765/dock/out   # expect 204
curl -i -X POST -H "Authorization: Bearer <token>" http://localhost:8765/dock/in
```

On the iPhone, in Shortcuts > Automation > New Automation > **Charger**:

1. Choose **Is Disconnected**, **Run Immediately**, and turn off **Notify
   When Run**. Then pick **New Blank Automation**.
2. Add **Get Contents of URL** with the URL
   `http://<hostname>.local:8765/dock/out`. Expand it, then set **Method** to
   `POST` and add a header `Authorization` with the value `Bearer <token>`.
3. Make a second automation for **Is Connected** that posts to `/dock/in`.

Away from home the request just fails. To avoid error banners there, wrap
the action in **If** with **Get Network Details** (Wi-Fi, Network Name) equal
to your home network.

Watch it work with `journalctl -u focus-dock -f`. Every accepted report
logs a `Charger: in/out (...)` line saying whether it counts; a bad token
logs `Rejected ...`, and no line at all means the request never arrived.
During a block it then logs `Phone out since ...` after 30 s off the charger
and `Phone back after N min` on return. `FOCUS_DOCK_SETTLE` changes the 30 s.

### Chat (`chat.py`, `inputs/chat.py`)

Message the coach from the phone, typed or by voice through Siri: "I
finished the spec, what next?", "add email Sam", "move the report to the
top", "I'm drifting". It sees the same data as a check-in and can change
priorities through six tools (add, done, undo, drop, move, report) that
call `focus.actions`. It has no other tools and cannot run commands.
Today's chat is kept in state, so follow-ups work and the next check-in
sees it; it is cleared at rollover.

`focus-chat` listens on port 8766 with its own token (`FOCUS_CHAT_TOKEN`,
added by `install.sh`). Unlike the dock it holds the API key, so a leaked
token could spend money: `FOCUS_CHAT_DAILY_LIMIT` (default 50 messages a
day) caps that, and each message is at most 6 model calls. Each exchange
records its token use in `state.json`.

On the Pi:

```bash
git pull && sudo deploy/install.sh
sudo systemctl enable --now focus-chat
sudo grep FOCUS_CHAT_TOKEN /etc/focus-assistant/focus.env    # for the Shortcut
TOKEN=$(sudo sed -n 's/^FOCUS_CHAT_TOKEN=//p' /etc/focus-assistant/focus.env)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -d '{"text": "what should I do first?"}'      http://localhost:8766/chat
```

On the iPhone, make a shortcut named **Coach** (tap **Edit** to build it by
hand):

1. **Ask for Input**, type Text, prompt `What's up?`
2. **Get Contents of URL**: `http://<hostname>.local:8766/chat`, Method
   `POST`, header `Authorization` = `Bearer <token>`, Request Body **JSON**
   with one Text field `text` set to **Provided Input**.
3. **Show Result** with **Contents of URL**.

"Hey Siri, Coach" then asks what's up, takes your dictation and reads the
reply aloud. Errors (no network, daily limit) come back as a sentence
rather than a Shortcut error. `journalctl -u focus-chat -f` shows each
message.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests mock the API, so they need no key and no network.
