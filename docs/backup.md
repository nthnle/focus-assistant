# Backing up state.json

Everything the assistant knows is in one file on the Pi:
`~/.local/state/focus-assistant/state.json` (the list, today, and
14 days of history). A microSD card can fail, so copy it off the Pi once a
day. This guide sets up a desktop PC to copy it at 18:00 over SSH, with a
key that can do nothing but read that one file.

## 1. Make a key on the PC

```bash
ssh-keygen -t ed25519 -N "" -C focus-backup -f ~/.ssh/focus_backup
```

`-N ""` leaves it without a passphrase so the daily copy can run unattended.
That's acceptable because the Pi only lets this key read the state file
(next step). Keep it for this job only.

## 2. Let that key read the file, and nothing else

On the Pi, add one line to `~/.ssh/authorized_keys`, with the public key
from the PC's `~/.ssh/focus_backup.pub` in place of `ssh-ed25519 AAAA...`:

```
restrict,command="cat /home/<user>/.local/state/focus-assistant/state.json" ssh-ed25519 AAAA... focus-backup
```

- `command=` runs `cat` on the state file whatever the PC asks for, so the
  key can't open a shell or run anything else.
- `restrict` turns off port forwarding, agent forwarding and terminals.
- Use the full path, with your user name: `~` isn't expanded there.

The services never see this: they run sandboxed with no access to `~/.ssh`.
The file is written by rename, so a copy taken mid-write is always a whole
file.

Test it from the PC. You should get the JSON, and nothing else should work:

```bash
ssh -i ~/.ssh/focus_backup <user>@focus.local | head -c 200; echo
ssh -i ~/.ssh/focus_backup <user>@focus.local ls     # still prints the JSON, not a listing
```

## 3. Copy it every day at 18:00

**Linux or macOS.** Run `crontab -e` and add:

```
0 18 * * * mkdir -p ~/focus-backups && ssh -i ~/.ssh/focus_backup -o BatchMode=yes <user>@focus.local > ~/focus-backups/state-$(date +\%F).json && find ~/focus-backups -name 'state-*.json' -mtime +30 -delete
```

That keeps one copy a day for 30 days. (`%` must be written `\%` in a
crontab.)

**Windows** (OpenSSH comes with Windows 10 and 11). Save this as
`%USERPROFILE%\focus-backup.ps1`:

```powershell
$dir = "$HOME\focus-backups"
New-Item -ItemType Directory -Force $dir | Out-Null
$out = "$dir\state-$(Get-Date -Format yyyy-MM-dd).json"
cmd /c "ssh -i `"$HOME\.ssh\focus_backup`" -o BatchMode=yes <user>@focus.local > `"$out`""
Get-ChildItem $dir -Filter 'state-*.json' | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item
```

and schedule it:

```
schtasks /create /tn "Focus backup" /sc daily /st 18:00 /tr "powershell -NoProfile -ExecutionPolicy Bypass -File %USERPROFILE%\focus-backup.ps1"
```

The copy goes through `cmd` so the file keeps its exact bytes; PowerShell's
own redirection can change the encoding.

If the PC is off at 18:00, that day is skipped; the next copy still has the
full 14-day history.

The coach's reply log (`replies.jsonl`, next to the state file) isn't part
of this copy: it's for improving the coach, not for restoring.

## Restoring

With your normal login, not the backup key:

```bash
scp ~/focus-backups/state-2026-10-23.json <user>@focus.local:/tmp/state.json
ssh <user>@focus.local
sudo systemctl stop focus-page focus-chat focus-voice focus-camera
cp /tmp/state.json ~/.local/state/focus-assistant/state.json
sudo systemctl start focus-page focus-chat focus-voice focus-camera   # the ones you use
focus show
```

An older copy rolls over to today the first time it's read, like any
other morning.
