# Install

This gets the core running: the to-do list and three check-ins a day. Every
optional feature builds on it; see the other guides when you're ready.

## What you need

- A **Raspberry Pi 4** (2 GB or more) with a microSD card and power supply.
  Any Debian-based Linux machine should also work, but only the Pi is tested.
- An account with a **host for open models** that offers an
  OpenAI-compatible API (Groq, Together, OpenRouter, Fireworks, DeepInfra
  and others): its base URL, an API key, and the name of a model that
  supports tool calling. Make a separate key for the Pi, so you can revoke
  it on its own.

## 1. Prepare the Pi

Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In
Imager's settings, set:

- a **hostname** (the examples use `focus`) and a **user**
- **Wi-Fi** and **SSH**
- **your time zone.** The check-ins run on the Pi's clock.

> On Windows, after flashing, you'll see only the small boot partition. The
> Linux partition is hidden; cancel any offer to format it.

Boot the Pi, then:

```bash
ssh <user>@focus.local
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv fonts-dejavu-core
timedatectl                       # check the time zone
```

## 2. Install

```bash
git clone https://github.com/nthnle/focus-assistant.git
cd focus-assistant
sudo deploy/install.sh
```

`install.sh` is safe to re-run. It:

- creates a virtual environment in `/opt/focus-assistant/venv` and links the
  `focus` command into `/usr/local/bin`
- creates your state directory, `~/.local/state/focus-assistant`
- creates `/etc/focus-assistant/focus.env` (readable by root only) from
  [`deploy/focus.env.example`](../deploy/focus.env.example), and adds a random
  token for each network service
- installs the systemd units, enables the check-in timer and the nightly
  voice quiet hours, and restarts any optional services that are running

The optional services (status page, chat, voice, desk camera, e-ink
display, stretch reminders) are installed but **not enabled**: you turn
each one on when you set it up.

## 3. Set the model

```bash
sudo nano /etc/focus-assistant/focus.env
```

Set `FOCUS_LLM_BASE_URL`, `FOCUS_LLM_API_KEY` and `FOCUS_MODEL` (see
[configuration.md](configuration.md#core)) and save. Then try a check-in:

```bash
focus add Write the project proposal
sudo systemctl start focus-checkin
journalctl -u focus-checkin -n 20      # or: focus last
```

The core is running: check-ins come at 09:00, 13:00 and 17:30 on work days
(with the desk camera, once you sit down after those times). For how to use it day to day, see [routine.md](routine.md).

## 4. What next

Every feature below is optional, and each guide stands on its own. In
this order, each one builds on the ones before it:

| Feature | You need | Guide |
|---|---|---|
| Spoken check-ins and stretch reminders | a USB speaker or speakerphone | [voice.md, 1](voice.md#1-speaker-and-spoken-check-ins) and [5](voice.md#5-stretch-reminders) |
| Voice commands, wake word, Call button | a USB speakerphone (Anker PowerConf tested) | [voice.md, 2 to 4](voice.md#2-voice-commands) |
| The e-ink panel | a Pimoroni Inky Impression 4.0" | [architecture.md, E-ink display](architecture.md#e-ink-display) |
| The status page: the panel's view in any browser, and a list editor | nothing extra | [phone.md, Status page](phone.md#status-page) |
| Chat from your phone, typed or through Siri | an iPhone (one Shortcut) | [phone.md, Chat](phone.md#chat) |
| Phone use, the phone nudge, and presence | a USB webcam, and a PC to train its model once | [camera.md](camera.md) |
| A daily copy of your list on another computer | a PC or Mac that's on at 18:00 | [backup.md](backup.md) |

The phone nudge and check-ins are spoken, so the camera is most
useful with a speaker set up first. Every setting is in
[configuration.md](configuration.md).

## Schedule

There are three check-ins a day: **morning from 09:00, midday from 13:00,
evening from 17:30**. On the week's last work day the evening one is the
weekly wrap-up.

**With the [desk camera](camera.md)**, each one waits for you: it comes
once the camera has seen you at the desk for 2 minutes, and is written
then, so it's about the day as it is. Every day works this way.

- Each comes at most once a day, at least an hour after the one before.
  Sit down at 15:00 and you get the midday one, not a backlog.
- Not after 20:00, and not while you're paused: one that comes due in a
  pause comes once it's over.
- The camera stays on while a check-in is waiting for you, so on a day off
  it's on only until you've sat down for one, or 20:00. The rest of the
  day off it's off.

**Without the camera**, nothing can tell that you sat down: on work days
the check-ins come at their times, and on days off there are none.

**Work days** are Monday to Friday unless you set `FOCUS_WORKDAYS` (e.g.
`sun-thu`, or `mon-thu,sat`). Only work days have focus blocks, stretch
reminders, phone counting and nudges, and count toward "days carried". On a
day off a check-in is two sentences about what you planned for that day:
items scheduled for it (by voice, chat, `focus add --on sat`, or **Pick a
day** on the status page) or added on it. Work left over isn't mentioned.
With nothing planned it looks ahead to the next work day.

To change the times, set them in `/etc/focus-assistant/focus.env`:

```bash
FOCUS_CHECKINS=08:30,12:30,17:00   # when morning, midday and evening open
FOCUS_CHECKIN_UNTIL=19:00          # none after this
```

then `sudo systemctl restart focus-camera`. `focus-checkin.timer` checks
every 5 minutes from 07:00 to 21:55, so keep the times inside that.
`sudo systemctl start focus-checkin` runs a check-in straight away,
whatever the time.

Focus blocks, used by the desk camera, stretch reminders and the status page,
are set separately with `FOCUS_BLOCKS` (default `09:00-12:00,13:00-17:30`).
See [configuration.md](configuration.md).

## Updating

```bash
cd ~/focus-assistant
git pull && sudo deploy/install.sh
```

Your settings in `/etc/focus-assistant/focus.env` and your state are kept.
To keep a copy of the state off the Pi as well, see [backup.md](backup.md).

## Checking on it

```bash
focus status                                   # the overview
systemctl list-timers 'focus-*' --no-pager     # what runs when
journalctl -u focus-checkin -u focus-checkin-tick -n 30   # recent check-ins
```

## Troubleshooting

| Problem | Try |
|---|---|
| Check-in says "The coach is offline" | The journal says why: `journalctl -u focus-checkin -u focus-checkin-tick -n 20`. |
| No check-in today | `focus status` says what the next one waits for. With the camera, it waits for you to sit down; see [Schedule](#schedule). |
| `FOCUS_LLM_BASE_URL and FOCUS_LLM_API_KEY must be set` | Check-ins get them from systemd. Run them with `sudo systemctl start focus-checkin`, not `focus checkin`. |
| `API key rejected` | Check `FOCUS_LLM_API_KEY` in `/etc/focus-assistant/focus.env`, with no quotes or spaces. |
| `Model provider error 404` or `400` | Usually a wrong `FOCUS_MODEL` name (copy it exactly from the host's model list), a model without tool calling, or `FOCUS_EFFORT` set for a model that isn't a reasoning model. |
| `can't reach the model provider` | Check the Pi's network and `FOCUS_LLM_BASE_URL`. The check-in retries a few times before giving up. |
| Check-ins at the wrong time | `timedatectl` shows the Pi's time zone; set it with `sudo raspi-config` under Localisation. |
| `focus: ... is not valid state JSON` | The state file was edited by hand or damaged. Move `~/.local/state/focus-assistant/state.json` aside and start fresh. |

## Uninstall

```bash
sudo systemctl disable --now focus-checkin.timer focus-stretch.timer \
     focus-voice-sleep.timer focus-voice-wake.timer focus-page focus-chat focus-voice \
     focus-camera focus-display
sudo rm /etc/systemd/system/focus-* /etc/udev/rules.d/70-focus-powerconf.rules
sudo rm -r /opt/focus-assistant /etc/focus-assistant /usr/local/bin/focus
sudo systemctl daemon-reload
rm -r ~/.local/state/focus-assistant           # your list and history
```
