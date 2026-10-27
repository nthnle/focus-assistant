# Configuration

All settings live in **`/etc/focus-assistant/focus.env`**, one `NAME=value`
per line. The file is readable by root only, because it holds your model key;
edit it with `sudo nano /etc/focus-assistant/focus.env`. After a change,
restart whatever uses the setting (for example
`sudo systemctl restart focus-voice`). Check-ins and stretch reminders pick
up changes on their next run.

[`deploy/focus.env.example`](../deploy/focus.env.example) is the starting
point that `install.sh` copies.

## Core

| Setting | Default | |
|---|---|---|
| `FOCUS_LLM_BASE_URL` | (required) | The host's OpenAI-compatible API address, ending in `/v1`, e.g. `https://api.groq.com/openai/v1`, `https://api.together.xyz/v1`, `https://openrouter.ai/api/v1`. |
| `FOCUS_LLM_API_KEY` | (required) | The host's API key. Use one just for this device, and set a spend limit at the host if it offers one. |
| `FOCUS_MODEL` | (required) | The host's name for the model used for check-ins. Pick an instruction-tuned model that supports tool calling (needed for chat and voice), roughly 8B parameters or larger. |
| `FOCUS_CHAT_MODEL` | `FOCUS_MODEL` | Model for chat, voice commands and stretch reminders. A smaller, faster model makes voice feel quicker. |
| `FOCUS_MODEL_PRICE`, `FOCUS_CHAT_MODEL_PRICE` | (unset) | US$ per million tokens in and out, e.g. `0.59,0.79`, from the host's price list. Each call's cost is saved with it, so `focus status` can show it. Calls made before a price was set show `$?`. |
| `FOCUS_EFFORT` | (unset) | Sent as `reasoning_effort` (`low`, `medium`, `high`). Only reasoning models accept it; leave it unset for the rest, or requests fail. |
| `FOCUS_OUTPUTS` | `console` | Where check-ins go, comma-separated, in order: `console`, `speech`, `inky`. With the `focus-display` service running, leave out `inky`: the service redraws the panel on every change. |
| `FOCUS_HISTORY_DAYS` | `14` | Days of daily summaries kept for the coach's context. |
| `FOCUS_STATE_DIR` | `~/.local/state/focus-assistant` | Where `state.json` lives. The systemd units set this; you normally don't. |
| `FOCUS_WORKDAYS` | `mon-fri` | Work days, e.g. `sun-thu` or `mon-thu,sat`. Only these have focus blocks, stretch reminders, phone counting and nudges, and count toward "days carried"; the last one of the week has the wrap-up. On the others a check-in comes only if you sit down at the desk (with the camera), and is about what you planned for that day. |
| `FOCUS_CHECKINS` | `09:00,13:00,17:30` | When the morning, midday and evening check-ins open. Each comes once you sit down at the desk after its time (with the desk camera), or at its time (without). Keep them between 07:00 and 21:55, when `focus-checkin.timer` checks. |
| `FOCUS_CHECKIN_UNTIL` | `20:00` | No check-in from this time on; the camera stops waiting for you then. |
| `FOCUS_BLOCKS` | `09:00-12:00,13:00-17:30` | Focus blocks on work days. Phone use counts only in them, and the desk camera runs from 5 minutes before each to 5 after; also used by stretch reminders, phone nudges and the status page. |
| `FOCUS_LOG_WORDS` | (off) | `1` writes what you say, type or mark done into the system journal. Off, the journal gets only the length (`<16 chars>`). Turn it on while tuning voice. |

**When the host can't be reached** (or a setting is missing), check-ins
use a fixed nudge from your list, stretch reminders use a fixed
phrase, and chat and voice answer that the coach is offline. Nothing else
calls out, so there is no other fallback.

**Reasoning models** that write `<think>...</think>` before answering work:
that text is removed before anything is shown or spoken. They are slower,
though, and voice notices.

The **check-in times** are set in the systemd timer, not here; see
[install.md](install.md#schedule).

## Speech

| Setting | Default | |
|---|---|---|
| `FOCUS_SPEECH_DEVICE` | `default` | ALSA device for the speaker (and the microphone, for voice), e.g. `plughw:CARD=PowerConf,DEV=0`. **Required for voice:** `default` is the Pi's headphone jack, which has no microphone. |
| `FOCUS_SPEECH_LEAD_MS` | `300` | Silence played before speech when the speaker has been quiet for over 2 s, so a USB speaker's amplifier is awake before the first word. Raise it if the start of a sentence still sounds cut off; `0` turns it off. |
| `FOCUS_SPEECH_VOICE` | `/opt/focus-assistant/voices/en_US-lessac-medium.onnx` | The Piper voice. |
| `FOCUS_SPEECH_WAV` | (unset) | Save speech to this WAV file instead of playing it, for testing without a speaker. |

## Voice commands

| Setting | Default | |
|---|---|---|
| `FOCUS_VOICE_MODEL` | `base.en` | Speech recognition: `moonshine/base` (recommended on a Pi), `moonshine/tiny`, or a Whisper model such as `tiny.en` or `base.en`. See [voice.md](voice.md#choosing-a-speech-model). |
| `FOCUS_VOICE_MODELS` | `/opt/focus-assistant/models` | Where speech models are downloaded. |
| `FOCUS_VOICE_FOLLOWUP` | `5` | Seconds to keep listening after a reply, with no wake word needed. `0` turns it off. |
| `FOCUS_VOICE_TALK_FOLLOWUP` | `10` | The same in talk mode ("let's talk"). |
| `FOCUS_VOICE_LEVEL` | (automatic) | A fixed loudness threshold for "you're speaking", if the automatic one cuts you off or waits too long. Try `800`. |
| `FOCUS_VOICE_BUTTON` | `PowerConf Consumer Control` | Part of the name of the input device with the talk button (`sudo evtest` lists them). |
| `FOCUS_VOICE_KEY` | `164` | The talk button's key code (`164` is `KEY_PLAYPAUSE`). |
| `FOCUS_VOICE_WAKE` | (unset) | Wake word model: `hey_jarvis`, `hey_mycroft`, `hey_rhasspy` or `alexa`. Unset means button only. |
| `FOCUS_VOICE_WAKE_THRESHOLD` | `0.5` | Wake word score needed (0 to 1). Higher means fewer false starts. |
| `FOCUS_VOICE_WAKE_FRAMES` | `3` | Consecutive 80 ms frames above the threshold needed to fire. |
| `FOCUS_VOICE_WAKE_VAD` | `0.5` | Speech-detector threshold the wake word also needs. `0` turns it off. |

## Desk camera

See [camera.md](camera.md).

| Setting | Default | |
|---|---|---|
| `FOCUS_CAMERA_DEVICE` | `/dev/video0` | The webcam, from `v4l2-ctl --list-devices`. |
| `FOCUS_CAMERA_EVERY` | `2` | Seconds between frames. |
| `FOCUS_CAMERA_MJPEG` | `1` | Ask the camera for MJPEG (less USB bandwidth). `0` if it refuses. |
| `FOCUS_CAMERA_MODEL` | `<state folder>/camera/model.onnx` | The model from `tools/train_camera.py`. |
| `FOCUS_PHONE_NUDGE_MIN` | `5` | Minutes on the phone in a focus block before the coach says so, and again every this many minutes. `0` turns nudges off. |

## Status page

| Setting | Default | |
|---|---|---|
| `FOCUS_DISPLAY_TOKEN` | (generated) | Token for the status page URL. `focus-page` won't start without one; a missing line gets a new token on the next install. |
| `FOCUS_PAGE_PORT` | `8765` | Port for `focus-page`. |
| `FOCUS_PAGE_HOST` | `0.0.0.0` | Address to listen on. |
| `FOCUS_ALLOWED_CLIENTS` | (any home-network address) | Comma-separated addresses or networks allowed to reach the status page and chat, e.g. your phone's reserved IP `192.168.1.50`. The Pi itself (localhost) is always allowed, for testing with curl; public addresses never are. A bad entry stops the services from starting. |

## Chat

| Setting | Default | |
|---|---|---|
| `FOCUS_CHAT_TOKEN` | (generated) | Token the phone's Coach shortcut sends. |
| `FOCUS_CHAT_PORT` | `8766` | Port for chat. |
| `FOCUS_CHAT_HOST` | `0.0.0.0` | Address to listen on. |

## Presence

| Setting | Default | |
|---|---|---|
| `FOCUS_AWAY_CAMERA_MIN` | `5` | Minutes the desk camera must see the empty desk before you count as away. |

## E-ink display

| Setting | Default | |
|---|---|---|
| `FOCUS_INKY_PNG` | (unset) | Save the display image to this PNG instead of drawing on the panel (600x400). |
| `FOCUS_INKY_FONT` | (DejaVu Sans) | A TrueType font file to use instead. |
| `FOCUS_INKY_BUTTONS` | `5,6,16,24` | GPIO pins of the panel's buttons A to D ([what they do](architecture.md#e-ink-display)). Empty turns them off. |
