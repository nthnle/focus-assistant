# Configuration

All settings live in **`/etc/focus-assistant/focus.env`**, one `NAME=value`
per line. The file is readable by root only, because it holds your API key;
edit it with `sudo nano /etc/focus-assistant/focus.env`. After a change,
restart whatever uses the setting (for example
`sudo systemctl restart focus-voice`). Check-ins and stretch reminders pick
up changes on their next run.

[`deploy/focus.env.example`](../deploy/focus.env.example) is the starting
point that `install.sh` copies.

## Core

| Setting | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Anthropic API key. Use one just for this device. |
| `FOCUS_MODEL` | `claude-sonnet-5-5` | Model for the scheduled check-ins. `claude-opus-5-5` coaches slightly better at about twice the price. |
| `FOCUS_CHAT_MODEL` | `claude-haiku-5-5` | Model for chat, voice commands and stretch reminders: quicker and about twenty times cheaper than Sonnet. |
| `FOCUS_EFFORT` | `low` | How much the model thinks before replying (`low`, `medium`, `high`). Replies are short, so `low` is plenty. |
| `FOCUS_OUTPUTS` | `console` | Where check-ins go, comma-separated, in order: `console`, `speech`, `inky`. |
| `FOCUS_HISTORY_DAYS` | `14` | Days of daily summaries kept for the coach's context. |
| `FOCUS_STATE_DIR` | `~/.local/state/focus-assistant` | Where `state.json` lives. The systemd units set this; you normally don't. |
| `FOCUS_BLOCKS` | `09:00-12:00,13:00-17:30` | Weekday focus blocks. Used by the phone dock, stretch reminders, presence and the status page. |
| `FOCUS_LOG_WORDS` | (off) | `1` writes what you say, type or mark done into the system journal. Off, the journal gets only the length (`<16 chars>`). Turn it on while tuning voice. |

The **check-in times** are set in the systemd timer, not here; see
[install.md](install.md#schedule).

## Speech

| Setting | Default | |
|---|---|---|
| `FOCUS_SPEECH_DEVICE` | `default` | ALSA device for the speaker (and the microphone, for voice), e.g. `plughw:CARD=PowerConf,DEV=0`. **Required for voice:** `default` is the Pi's headphone jack, which has no microphone. |
| `FOCUS_SPEECH_VOICE` | `/opt/focus-assistant/voices/en_US-lessac-medium.onnx` | The Piper voice. |
| `FOCUS_SPEECH_WAV` | (unset) | Save speech to this WAV file instead of playing it, for testing without a speaker. |

## Voice commands

| Setting | Default | |
|---|---|---|
| `FOCUS_VOICE_MODEL` | `base.en` | Speech recognition: `moonshine/base` (recommended on a Pi), `moonshine/tiny`, or a Whisper model such as `tiny.en` or `base.en`. See [voice.md](voice.md#choosing-a-speech-model). |
| `FOCUS_VOICE_MODELS` | `/opt/focus-assistant/models` | Where speech models are downloaded. |
| `FOCUS_VOICE_FOLLOWUP` | `5` | Seconds to keep listening after a reply, with no wake word needed. `0` turns it off. |
| `FOCUS_VOICE_LEVEL` | (automatic) | A fixed loudness threshold for "you're speaking", if the automatic one cuts you off or waits too long. Try `800`. |
| `FOCUS_VOICE_BUTTON` | `PowerConf Consumer Control` | Part of the name of the input device with the talk button (`sudo evtest` lists them). |
| `FOCUS_VOICE_KEY` | `164` | The talk button's key code (`164` is `KEY_PLAYPAUSE`). |
| `FOCUS_VOICE_WAKE` | (unset) | Wake word model: `hey_jarvis`, `hey_mycroft`, `hey_rhasspy` or `alexa`. Unset means button only. |
| `FOCUS_VOICE_WAKE_THRESHOLD` | `0.5` | Wake word score needed (0 to 1). Higher means fewer false starts. |
| `FOCUS_VOICE_WAKE_FRAMES` | `3` | Consecutive 80 ms frames above the threshold needed to fire. |
| `FOCUS_VOICE_WAKE_VAD` | `0.5` | Speech-detector threshold the wake word also needs. `0` turns it off. |

## Phone dock and status page

| Setting | Default | |
|---|---|---|
| `FOCUS_DOCK_TOKEN` | (generated) | Token the phone's dock shortcuts send. |
| `FOCUS_ALLOWED_CLIENTS` | (any home-network address) | Comma-separated addresses or networks allowed to reach the dock, status page and chat, e.g. your phone's reserved IP `192.168.1.50`. Public addresses are never allowed. A bad entry stops the services from starting. |
| `FOCUS_DISPLAY_TOKEN` | (generated) | Token for the status page URL. Set it empty (`FOCUS_DISPLAY_TOKEN=`) to stop serving the page; a missing line gets a new token on the next install. |
| `FOCUS_DOCK_PORT` | `8765` | Port for the dock and the status page. |
| `FOCUS_DOCK_HOST` | `0.0.0.0` | Address to listen on. |
| `FOCUS_DOCK_SETTLE` | `30` | Seconds the phone must stay off the charger before it counts. |

## Chat

| Setting | Default | |
|---|---|---|
| `FOCUS_CHAT_TOKEN` | (generated) | Token the phone's Coach shortcut sends. |
| `FOCUS_CHAT_DAILY_LIMIT` | `50` | Chat and voice messages allowed per day, answered or failed: a cost cap. |
| `FOCUS_CHAT_PORT` | `8766` | Port for chat. |
| `FOCUS_CHAT_HOST` | `0.0.0.0` | Address to listen on. |

## Presence

| Setting | Default | |
|---|---|---|
| `FOCUS_AWAY_PHONE_MIN` | `10` | Minutes off the dock before you can count as away. |
| `FOCUS_AWAY_QUIET_MIN` | `15` | ...and minutes of desk silence. |

## E-ink display

| Setting | Default | |
|---|---|---|
| `FOCUS_INKY_PNG` | (unset) | Save the display image to this PNG instead of drawing on the panel. |
| `FOCUS_INKY_FONT` | (DejaVu Sans) | A TrueType font file to use instead. |
