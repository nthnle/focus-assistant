# Desk camera

A USB webcam on the desk watches for two things during focus blocks:

- **phone use:** when you pick up your phone and use it, wherever it was
  charging (or not). Each time counts as a pickup, the coach sees them at
  the next check-in, and after **5 minutes** on it the coach says so out
  loud, then again every 5 minutes while you stay on it
- **an empty desk:** so a check-in waits for you instead of talking to an
  empty room, and stretch reminders skip you when you're already up

It does this with a small image model trained on **your own desk**: your
camera's angle, your light, you. That makes it accurate where a general
phone detector isn't (a phone held side-on, in a case, in a dim room), but
it also means you record a few minutes of examples and train it once, on a
PC, before it works.

## Privacy

- **Frames never leave the Pi and are never saved.** Each one is classified
  in memory as phone, work or away, then thrown away. What's kept is the
  time you picked up and put down the phone, and what the camera sees now
  (`camera.json`, for presence and the status page). Nothing goes to the
  model host but those times, as part of a check-in.
- **It only runs in focus blocks** (`FOCUS_BLOCKS`, weekdays) and not while
  you're paused. The rest of the time the camera is closed, and its light is
  off.
- The service gets the camera and nothing else: no network, no API key.
- **The frames you record for training, and the model trained from them,
  are pictures of you.** Keep them on your own machines: the frames in your
  home folder, the model in the state folder. Never add either to a git
  repository.

## What you need

- A USB webcam that works with Linux without a driver (most do: "UVC").
  Plug it into the powered hub if you use one.
- `ffmpeg` on the Pi, which reads the camera.
- Once, a PC to train on: Windows, macOS or Linux, with Python. An NVIDIA
  graphics card makes training take a minute or two instead of ten, but
  isn't needed.
- The [voice service](voice.md#2-voice-commands), for the spoken phone
  nudge. Without it, phone use is still counted and the coach still sees it.

## 1. Place the camera

It needs to see your hands and the desk in front of the keyboard, not only
your face: a phone in your lap or held low is only seen if the camera can
see there. Clip it to the monitor tilted down, or put it on a small stand to
one side. Sit as you normally do and check the view:

```bash
sudo apt install -y ffmpeg v4l-utils
v4l2-ctl --list-devices                  # which /dev/video* is the webcam
ffmpeg -f v4l2 -video_size 640x480 -i /dev/video0 -frames:v 1 ~/view.jpg
```

Once you've trained it, **leave the camera where it is.** Moving it, even a
little, means recording again.

## 2. Install the camera service

```bash
cd ~/focus-assistant
git pull
sudo /opt/focus-assistant/venv/bin/pip install ".[camera]"
sudo deploy/install.sh
```

## 3. Record examples

Stop the service first if it's running (only one program can use the camera
at a time), then start recording:

```bash
sudo systemctl stop focus-camera
/opt/focus-assistant/venv/bin/python -m focus.camera --collect
```

It saves a frame every 2 seconds to `~/frames`. Whenever what you're doing
changes, type a letter and press Enter:

| Key | Meaning |
|---|---|
| `p` | on the phone |
| `w` | at the desk, not on the phone |
| `a` | away: nobody in the chair |
| `s` | skip: frames are thrown away (adjusting the camera, sitting down) |
| `q` | quit |

The first 3 seconds after each change are thrown away, so a sloppy switch
does no harm. You don't type anything per frame: a few minutes of `p` while
you use your phone normally gives you a hundred frames.

**What to record.** About 10 minutes each of `p` and `w`, and 5 of `a`,
spread over a few short sessions rather than one long one:

- `p`: the phone held up, held low, in your lap, one hand, both hands,
  leaning back in the chair
- `w`: typing, mouse work, reading the screen, leaning back, stretching,
  drinking, your hand at your face or holding something phone-sized, and
  the phone lying on the desk untouched. These teach it what *isn't* phone
  use; without them, it may learn that leaning back means phone.
- `a`: the empty chair, pushed in and pulled out
- **every light you work in:** daylight, the lamp, evening

Frames from later sessions add to the same folders.

## 4. Train the model on a PC

Copy the frames over (from the PC):

```bash
scp -r <user>@focus.local:frames .
```

Then, in a copy of this repository on the PC:

```bash
python -m venv .venv-train
.venv-train/bin/pip install torch torchvision onnx onnxscript pillow   # Windows: .venv-train\Scripts\pip
# With an NVIDIA card, install torch's CUDA build instead (pytorch.org has the command).

.venv-train/bin/python tools/train_camera.py frames --check     # how well it does
.venv-train/bin/python tools/train_camera.py frames model.onnx  # the model for the Pi
```

`--check` trains it several times, each time holding back two minutes of
your recording, and scores it on those minutes it never saw. Look at what
it gets wrong: if one pose or one light is mostly wrong, it's because there
are too few examples of it. Record a few more minutes of exactly that, copy
the frames again and retrain. Expect over 95% right on each label, and
"smoothed" (deciding over five frames, as the service does) higher still.

## 5. Install the model and turn it on

```bash
ssh <user>@focus.local mkdir -p .local/state/focus-assistant/camera
scp model.onnx <user>@focus.local:.local/state/focus-assistant/camera/
```

On the Pi, look at what it sees for a few frames, then enable it:

```bash
/opt/focus-assistant/venv/bin/python -m focus.camera --check   # label and time per frame
sudo systemctl enable --now focus-camera
journalctl -u focus-camera -f        # "Camera on" in a focus block, "Phone in use since ..."
```

`focus status` shows what the camera sees and since when, and the status
page shows the phone as in use or not.

## How it decides

- One frame every 2 seconds (`FOCUS_CAMERA_EVERY`). A label counts once it
  is the majority of the last five frames, so a misread frame or two
  changes nothing.
- On the phone for **30 seconds** counts as a pickup, timed from when you
  picked it up; a glance doesn't count. It's over once the camera hasn't
  seen the phone for 30 seconds, so putting it down for a moment is still
  one pickup.
- After **5 minutes** on the phone (`FOCUS_PHONE_NUDGE_MIN`; 0 turns it
  off) the voice service has the coach say so, in a sentence that points
  you back at your list, then again every 5 minutes. Not while you're
  paused, and not in the break after a stretch reminder.
- The desk empty for **5 minutes** (`FOCUS_AWAY_CAMERA_MIN`) counts as
  [away](phone.md#presence).

## When it gets things wrong

It knows the conditions it was trained on. A new lamp, a hoodie, the
camera bumped, or a pose you never recorded can bring mistakes back. Record
a few minutes of that situation with `--collect`, copy all the frames to
the PC again, retrain, and copy the new model over:

```bash
scp model.onnx <user>@focus.local:.local/state/focus-assistant/camera/
ssh <user>@focus.local sudo systemctl restart focus-camera
```

**Its blind spots:**
- a phone kept out of the camera's view, under the desk
- a phone call held to your ear may count as phone use
- someone else in your chair looks like you
- in a dark room it sees little; work in the light you trained in

## Troubleshooting

- **`No model at ...`:** the service stops until there's one (step 5).
- **`The camera stopped`:** another program has the camera (`--collect` or
  `--check` while the service runs), or it's unplugged. Check
  `v4l2-ctl --list-devices` and set `FOCUS_CAMERA_DEVICE` if it isn't
  `/dev/video0`.
- **ffmpeg says the format isn't supported:** set `FOCUS_CAMERA_MJPEG=0`
  (more USB bandwidth, same result).
- **Wrong labels in one situation:** see above, record more of it.
