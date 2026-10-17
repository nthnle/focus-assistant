# Install

This gets the core running: priorities and three check-ins a day. Every
optional feature builds on it; see the other guides when you're ready.

## What you need

- A **Raspberry Pi 4** (2 GB or more) with a microSD card and power supply.
  Any Debian-based Linux machine should also work, but only the Pi is tested.
- An **Anthropic API key** from the [Console](https://console.anthropic.com/).
  Make a separate key for the Pi, so you can revoke it on its own.

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

The optional services (dock, chat, voice, stretch reminders) are installed
but **not enabled**: you turn each one on when you set it up.

## 3. Add your API key

```bash
sudo nano /etc/focus-assistant/focus.env
```

Set `ANTHROPIC_API_KEY=...` and save. Then try a check-in:

```bash
focus add Write the project proposal
sudo systemctl start focus-checkin
journalctl -u focus-checkin -n 20      # or: focus last
```

## Schedule

Check-ins run Monday to Friday at **09:00, 13:00 and 17:30**. Which kind of
check-in it is depends on the time: before 11:00 is the morning check-in,
before 16:00 midday, otherwise evening. Friday's evening check-in is the
weekly wrap-up.

To change the times:

```bash
sudo systemctl edit focus-checkin.timer
```

```ini
[Timer]
OnCalendar=
OnCalendar=Mon..Fri 08:30
OnCalendar=Mon..Fri 12:30
OnCalendar=Mon..Fri 17:00
```

The empty `OnCalendar=` clears the defaults. Keep each time inside its
window (morning, midday, evening). If the Pi was off at a scheduled time,
the check-in runs once when it comes back.

Focus blocks, used by the phone dock, stretch reminders and the status page,
are set separately with `FOCUS_BLOCKS` (default `09:00-12:00,13:00-17:30`).
See [configuration.md](configuration.md).

## Updating

```bash
cd ~/focus-assistant
git pull && sudo deploy/install.sh
```

Your settings in `/etc/focus-assistant/focus.env` and your state are kept.

## Checking on it

```bash
focus status                                   # the overview
systemctl list-timers 'focus-*' --no-pager     # what runs when
journalctl -u focus-checkin -n 30              # recent check-ins
```

## Troubleshooting

| Problem | Try |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Check-ins get the key from systemd. Run them with `sudo systemctl start focus-checkin`, not `focus checkin`. |
| `API key rejected` | Check the key in `/etc/focus-assistant/focus.env`, with no quotes or spaces. |
| `Could not reach the API` | Check the Pi's network. The check-in retries a few times before giving up. |
| Check-ins at the wrong time | `timedatectl` shows the Pi's time zone; set it with `sudo raspi-config` under Localisation. |
| `focus: ... is not valid state JSON` | The state file was edited by hand or damaged. Move `~/.local/state/focus-assistant/state.json` aside and start fresh. |

## Uninstall

```bash
sudo systemctl disable --now focus-checkin.timer focus-stretch.timer \
     focus-voice-sleep.timer focus-voice-wake.timer focus-dock focus-chat focus-voice
sudo rm /etc/systemd/system/focus-* /etc/udev/rules.d/70-focus-powerconf.rules
sudo rm -r /opt/focus-assistant /etc/focus-assistant /usr/local/bin/focus
sudo systemctl daemon-reload
rm -r ~/.local/state/focus-assistant           # your priorities and history
```
