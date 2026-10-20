# Voice

Everything that uses a speaker or a microphone. Do the sections in order:
each one builds on the one before.

1. [Speaker and spoken check-ins](#1-speaker-and-spoken-check-ins)
2. [Voice commands](#2-voice-commands)
3. [Wake word](#3-wake-word-hey-jarvis)
4. [Speakerphone buttons](#4-speakerphone-buttons)
5. [Stretch reminders](#5-stretch-reminders)
6. [Quiet hours](#quiet-hours), [speed](#speed) and [troubleshooting](#troubleshooting)

All speech processing runs on the Pi: Piper for text-to-speech,
faster-whisper or Moonshine for speech recognition, openWakeWord for the
wake word. Only the transcribed text is sent to the coach.

## Hardware

Any USB speaker works for spoken check-ins. For voice commands you also need
a microphone; a **USB speakerphone** gives you both, plus a mute button.
This guide uses an **Anker PowerConf**, the tested device.

> **Tip:** on a Pi 4, plug the speakerphone into a **black USB 2.0 port**.
> The PowerConf isn't detected at all on the blue USB 3.0 ports, and some
> other USB audio devices have the same problem.

Check that the Pi sees it:

```bash
sudo apt install -y alsa-utils
aplay -L | grep -i plughw      # e.g. plughw:CARD=PowerConf,DEV=0
arecord -l                     # the same card should list a capture device
speaker-test -D plughw:CARD=PowerConf,DEV=0 -c 2 -t wav -l 1
```

The `plughw:...` name is your **speech device**. The examples below use the
PowerConf's; substitute yours.

## 1. Speaker and spoken check-ins

```bash
sudo /opt/focus-assistant/venv/bin/pip install "$HOME/focus-assistant[speech]"
sudo /opt/focus-assistant/venv/bin/python -m piper.download_voices \
     --download-dir /opt/focus-assistant/voices en_US-lessac-medium
sudo chmod -R a+rX /opt/focus-assistant/voices
```

Add to `/etc/focus-assistant/focus.env`:

```
FOCUS_OUTPUTS=console,speech
FOCUS_SPEECH_DEVICE=plughw:CARD=PowerConf,DEV=0
```

Try it:

```bash
FOCUS_SPEECH_DEVICE=plughw:CARD=PowerConf,DEV=0 \
  /opt/focus-assistant/venv/bin/python -m focus.outputs.speech "Hello from the coach."
sudo systemctl start focus-checkin           # a real check-in, spoken
```

With no text, `python -m focus.outputs.speech` repeats the last check-in.

**Other voices.** Listen to samples at
[rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/),
download one by name with the command above, and set
`FOCUS_SPEECH_VOICE=/opt/focus-assistant/voices/<name>.onnx`. Prefer
**medium** voices: **high** ones sound a little better but are noticeably
slower on a Pi. Single-speaker voices only.

## 2. Voice commands

Press the speakerphone's **play** button, wait for the rising chime, and
speak. A falling chime means it heard you; the coach starts answering as
soon as its first sentence is written. Voice replies are one short sentence:

> "Done with the report."
> *"Marked the report done."*

> "What should I do now?"
> *"Call the dentist; it's quick and clears a pending appointment."*

The list isn't ranked, so the coach never says what's "next"; ask it and it
suggests one item, with a reason.

After each reply a soft chime means it's still listening for 5 seconds:
speak again and it's another turn, with no button or wake word. Say nothing
and it stops. The coach is the same one as [phone chat](phone.md#chat): it
can add, rename, schedule, complete, reopen and drop items, set
week goals, log how a block is going, and pause ("quiet until 11", "I'm off
until Monday"). It's built for quick commands.

**Talk mode,** for thinking something through out loud: say **"let's talk"**
(or "can we chat", or "let's talk about the exam plan" to start right in).
Replies grow to two or three sentences, the coach may ask you a question
back, and the listening window after each reply is 10 seconds
(`FOCUS_VOICE_TALK_FOLLOWUP`). Say **"that's all"** (or "back to work") to
end it, or just stop talking: a quiet window ends it too, and the next wake
word starts short again. Every turn counts toward the same 50-a-day limit
as chat.

Install the voice extras and a speech model:

```bash
sudo /opt/focus-assistant/venv/bin/pip install "$HOME/focus-assistant[voice]"
sudo /opt/focus-assistant/venv/bin/pip install --no-deps "useful-moonshine-onnx==20251121"
sudo /opt/focus-assistant/venv/bin/python -c "from huggingface_hub import hf_hub_download as d; [d('UsefulSensors/moonshine', f'onnx/merged/{n}/float/{f}.onnx', local_dir='/opt/focus-assistant/models/moonshine') for n in ('tiny', 'base') for f in ('encoder_model', 'decoder_model_merged')]"
sudo chmod -R a+rX /opt/focus-assistant/models
```

(`--no-deps` skips librosa and numba, which Moonshine only needs for reading
audio files.) Then add to `focus.env` and start the service:

```
FOCUS_VOICE_MODEL=moonshine/base
```

```bash
sudo systemctl enable --now focus-voice
journalctl -u focus-voice -f        # "Ready", then "Heard: ..." for each command
```

### Choosing a speech model

Speech recognition is the slowest step on a Pi. Measured on a Pi 4 with a
1.8-second command:

| `FOCUS_VOICE_MODEL` | Time | Notes |
|---|---|---|
| `moonshine/tiny` | 0.5 s | fastest; occasional small slips |
| `moonshine/base` | 1.1 s | **recommended**: about as accurate as Whisper base |
| `tiny.en` | 2.6 s | Whisper; takes a vocabulary hint |
| `base.en` (default) | 5.1 s | Whisper; most accurate, slow on a Pi |

Whisper pads every clip to 30 seconds, so short commands cost almost as much
as long ones; Moonshine's time scales with the clip. Whisper is given your
open priorities as a hint, which helps it spell project names and jargon;
Moonshine takes no hint. To use Whisper instead:

```bash
sudo /opt/focus-assistant/venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('tiny.en', download_root='/opt/focus-assistant/models')"
sudo chmod -R a+rX /opt/focus-assistant/models
```

With Moonshine base, a command's first spoken word typically comes about
3 seconds after you stop talking. See [Speed](#speed) to measure your own.

## 3. Wake word ("Hey Jarvis")

Instead of pressing play, say **"Hey Jarvis"**, wait for the chime, then
speak. The button keeps working.

```bash
sudo /opt/focus-assistant/venv/bin/pip install "$HOME/focus-assistant[wake]"
sudo /opt/focus-assistant/venv/bin/pip install --no-deps "openwakeword==0.6.0"
sudo /opt/focus-assistant/venv/bin/python -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"
echo 'FOCUS_VOICE_WAKE=hey_jarvis' | sudo tee -a /etc/focus-assistant/focus.env
sudo systemctl restart focus-voice
```

openWakeWord requires `tflite-runtime` on Linux, which has no build for the
Pi's Python, so it's installed without dependencies; the `wake` extra
supplies the ones it really uses.

Built-in phrases: `hey_jarvis`, `hey_mycroft`, `hey_rhasspy`, `alexa`. To
keep conversation and noise from setting it off, it fires only when the
score stays high for several frames in a row and a speech detector hears a
human voice. Each trigger logs its score, e.g.
`Listening (hey_jarvis, score 0.97)`. If it starts on its own, raise
`FOCUS_VOICE_WAKE_THRESHOLD` (0.5) toward 0.7 or `FOCUS_VOICE_WAKE_FRAMES`
(3) to 4; if it misses you, lower them.

**Pause after the wake word.** What you say before the chime isn't heard.

## 4. Speakerphone buttons

On the Anker PowerConf:

| Button | Does |
|---|---|
| **Play** | Talk: starts a voice command. Pressed while you speak, it ends the recording; pressed while the coach speaks, it cuts the reply off. |
| **Call** | Marks done the item the latest check-in suggested starting with (or the only open one), instantly, with no API call. If no check-in suggested one, it asks you to say which. **Double press** (within 0.4 s) undoes it. |
| **Mute** | Mutes the microphones inside the device. The Pi gets silence: a true privacy switch. |
| **Volume** | Speaker volume, handled by the speakerphone itself. |

The play button arrives as a normal key (`KEY_PLAYPAUSE`). The call button
never becomes a key; it arrives as a raw HID report on `/dev/hidraw*`, which
`install.sh` makes readable with a udev rule
([`deploy/70-focus-powerconf.rules`](../deploy/70-focus-powerconf.rules)).

**Other speakerphones.** Find your talk button's input device and key code
with `sudo evtest`, then set `FOCUS_VOICE_BUTTON` (part of the device's
name) and `FOCUS_VOICE_KEY` (the code). The call-button support is specific
to the PowerConf's USB ID and report; supporting another device's extra
buttons means adapting `POWERCONF_HID` and `CALL_REPORT` in
[`focus/inputs/voice.py`](../focus/inputs/voice.py) and the udev rule. With
no usable button at all, the wake word alone works.

## 5. Stretch reminders

At **:50 past each hour of a focus block** (9:50, 10:50, 11:50, 13:50 to
16:50 with the default blocks), the speaker tells you to stand up and move:
50 minutes of work, 10 of break. The coach writes the line from your day
("You've been on the report since nine, stand up before the conclusion").
If it doesn't answer within 5 seconds, or the network is down, a fixed
phrase plays instead, so a reminder is never late. They're skipped when
you're [away](phone.md#presence).

At **:00**, when the break is over, it says so: "Break's over. Back to it.
3 things on your list." Only after a reminder that played at :50, and not
when you're away or paused, or when the hour starts outside a focus block
(12:00 is lunch). It's a fixed line, so it costs nothing and needs no
network.

Needs the speaker setup (section 1). Then:

```bash
sudo systemctl enable --now focus-stretch.timer focus-back.timer
systemctl list-timers focus-stretch.timer focus-back.timer --no-pager
```

(`install.sh` turns the back-to-work timer on for you whenever the stretch
timer is on. Not wanted? `sudo systemctl disable --now focus-back.timer`.)

To try one immediately with the service's settings:

```bash
sudo systemd-run --pipe --wait -p User=$USER -p EnvironmentFile=/etc/focus-assistant/focus.env \
     -E FOCUS_STATE_DIR=$HOME/.local/state/focus-assistant \
     /opt/focus-assistant/venv/bin/python -m focus.stretch --now
```

## Quiet hours

From **00:00 to 08:00** the voice service is stopped completely: no
microphone, no wake word, no buttons. For a quiet spell during the day (a meeting,
a call), pause instead: `focus pause 11:00`, or say "quiet until 11". That
holds stretch reminders and spoken check-ins but keeps voice commands working. `install.sh` enables the two timers
that do this. To change the times:

```bash
sudo systemctl edit focus-voice-sleep.timer     # and focus-voice-wake.timer
```

To turn quiet hours off:
`sudo systemctl disable --now focus-voice-sleep.timer focus-voice-wake.timer`.
If the Pi restarts during the night, voice starts with it and stops at the
next midnight.

## Speed

Each voice command logs where its wait went, counted from the end of
recording (which is 0.8 s after you stop talking):

```
Timing: 1.8 s of speech, transcribe 1.1 s, coach 1.8 s, sentence 0.1 s,
        voice 0.2 s, first word at 3.2 s, done at 5.0 s
```

`transcribe` is speech recognition, `coach` the wait for the reply's first
words, `sentence` until the first sentence is complete, and `voice` Piper
turning it into audio. `journalctl -u focus-voice | grep Timing` lists them.

To time every part without speaking, including each installed speech model:

```bash
sudo systemd-run --pipe --wait -p User=$USER -p EnvironmentFile=/etc/focus-assistant/focus.env \
     -E FOCUS_STATE_DIR=$HOME/.local/state/focus-assistant -E HF_HUB_OFFLINE=1 \
     /opt/focus-assistant/venv/bin/python -m focus.bench --api
```

`--api` adds one short call to the coach to measure its reply time.

## Troubleshooting

| Problem | Try |
|---|---|
| The speakerphone doesn't show up in `aplay -l` | Use a black USB 2.0 port; check `lsusb`; try another cable (some are charge-only). |
| `set FOCUS_SPEECH_DEVICE in focus.env` | Voice needs the speech device explicitly; the default sound card is the Pi's headphone jack, which has no microphone. |
| `No Piper voice at ...` | Download a voice (section 1). |
| `arecord failed on ...` | The speakerphone was unplugged or another program holds it. `sudo systemctl restart focus-voice`. |
| It cuts you off, or waits long after you stop | Set `FOCUS_VOICE_LEVEL` (try 800; higher for a noisy room). |
| It hears "You" or "Thank you" in silence | Those are speech-recognition artifacts. They're ignored automatically: you get a low tone, and the coach never sees them. |
| `Call button ... Can't read /dev/hidraw0` | Re-run `sudo deploy/install.sh` to install the udev rule. |
| A check-in and a voice reply at once | They can't share the speaker; one fails and is logged. |

To hear exactly what the microphone picks up (for example, to confirm mute):

```bash
sudo systemctl stop focus-voice
arecord -D plughw:CARD=PowerConf,DEV=0 -f S16_LE -r 16000 -c 1 -d 3 /tmp/m.wav && \
  aplay -D plughw:CARD=PowerConf,DEV=0 /tmp/m.wav
sudo systemctl start focus-voice
```
