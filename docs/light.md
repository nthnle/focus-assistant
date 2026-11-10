# Front light

E-ink has no backlight, so in a dim room the panel is hard to read. A short
warm LED strip along the top of the panel lights it from the front, the
way e-readers do. The Pi switches the strip on when the room gets dim and
off when it's bright, at night, or once the desk camera has seen you leave.

It's optional: without `FOCUS_LIGHT_PIN` nothing about it runs or shows.

## What you need

No soldering:

| Part | Notes |
|---|---|
| A **5 V warm white LED strip**, 10 to 15 cm | 2700 to 3000 K is easiest on the eyes. COB strips give an even line of light. A 10 cm piece draws about 0.1 A; keep it under 0.5 A, since it runs from the Pi's 5 V pin |
| A **MOSFET switch module that works at 3.3 V** | Look for one built on a D4184 or AO3400 and sold as 3.3 V compatible. **Not** the common IRF520 module: it needs about 10 V to switch fully and stays dim or off on a Pi |
| A **BH1750 light sensor** board (often sold as GY-302) | Buy one with the header pins already soldered on |
| **Female-to-female jumper wires** | About eight |

## Wiring

The pins are the Pi's header numbers. With the panel on a ribbon and a
Mini Black HAT Hack3r, use the Hack3r's second header: it carries the same
pins.

```
BH1750 sensor              Pi header
  VCC  ------------------  pin 1   (3.3 V)
  GND  ------------------  pin 9   (ground)
  SDA  ------------------  pin 3   (GPIO 2, I2C)
  SCL  ------------------  pin 5   (GPIO 3, I2C)
  ADDR    (leave unconnected: address 0x23)

MOSFET module              Pi header
  signal (SIG / IN / PWM)  pin 32  (GPIO 12)
  signal ground (GND)      pin 34  (ground)
  power in  +  ----------  pin 2   (5 V)
  power in  -  ----------  pin 6   (ground)
  power out +  ----------  strip +
  power out -  ----------  strip -
```

Module layouts differ: some have one screw terminal for the supply and one
for the load, some share the grounds. Follow the labels on yours.

The sensor shares the I2C bus with the panel's ID chip (at 0x50), which is
fine. GPIO 12 is free with the Inky Impression 4.0" (it uses GPIO 17, 22
and 27, the SPI pins, and 5, 6, 16 and 24 for its buttons). If you use
another pin, set it in `FOCUS_LIGHT_PIN`. Check first that nothing else
claims the pin: `pinctrl get 12` should say `input` and nothing more.

## Placement

- **The strip** goes along the top of the panel, shining down across the
  screen, so the glass doesn't reflect it at you. Until the stand has a
  lip for it, double-sided tape on the top of the bumper works.
- **The sensor must not see the strip.** Put it behind the panel, facing
  up or back, so it measures the room. If it sees the strip, the light
  turns itself off a few seconds after it comes on.

To check, turn the light on and off by hand (`focus light on`, then
`focus light off`) and compare the lux in `focus light`. The reading
should barely change.

## Turn it on

Check that the Pi sees the sensor:

```bash
sudo apt install -y i2c-tools
i2cdetect -y 1          # 23 is the sensor, 50 the panel's ID chip
```

Add the pin to the settings, then restart everything that uses it:

```bash
echo 'FOCUS_LIGHT_PIN=12' | sudo tee -a /etc/focus-assistant/focus.env
sudo systemctl restart focus-display focus-page focus-voice
journalctl -u focus-display -f      # "Front light on GPIO 12", then "Front light on: the room is dim"
```

The `focus-display` service runs the light. It already has the GPIO pins
and the I2C bus for the panel, so nothing else needs permission. The
status page and voice only need `FOCUS_LIGHT_PIN` to know there is a light.

## Using it

Automatic, it's on while the room is under 30 lux, from 08:00 to midnight,
and off once the desk camera has seen the empty desk for 5 minutes. So it
doesn't flicker as daylight fades, it goes off only once the room is twice
as bright again, and only after 30 seconds either way.

By hand, any of these:

| | |
|---|---|
| Voice | "Light on", "light off", "light auto" |
| The panel | Hold **D** for a second: on if it's off, off if it's on |
| The status page | **On**, **Off** or **Auto**, under the list |
| The command line | `focus light on`, `focus light off`, `focus light auto`; `focus light` says what it's doing and why |

On or off by hand holds until you say "auto", or until midnight.

## Settings

| Setting | Default | |
|---|---|---|
| `FOCUS_LIGHT_PIN` | (none: no light) | GPIO pin of the MOSFET module's signal input, e.g. `12` |
| `FOCUS_LIGHT_LUX` | `30` | The room counts as dim below this. Raise it if the light comes on too late |
| `FOCUS_LIGHT_HOURS` | `08:00-00:00` | When it may come on by itself. An end at or before the start runs past midnight |

## Troubleshooting

| | |
|---|---|
| `Can't read the light sensor` in the journal | Check the four sensor wires, then `i2cdetect -y 1`. The light stays off until the sensor answers, then carries on |
| `focus light` says "no word from focus-display yet" | `focus-display` isn't running, or has no `FOCUS_LIGHT_PIN`: `journalctl -u focus-display -n 30` |
| The journal says it's on, but the strip is dark | Swap the strip's wires on the module's output (strips only light one way round), and make sure the module is a 3.3 V one |
| It turns off a few seconds after coming on | The sensor sees the strip: move or shade it |
| `Front light couldn't start` | Another program has the pin, or it's the wrong number: `pinctrl get 12` |
