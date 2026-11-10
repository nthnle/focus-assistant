"""The front light: a warm LED strip along the top of the panel, run by the
focus-display service. A MOSFET switch module on a GPIO pin turns the strip
on and off, and a BH1750 light sensor on the I2C bus measures the room.

Automatic: on while the room is dim (under FOCUS_LIGHT_LUX, 30 lux) during
FOCUS_LIGHT_HOURS (08:00-00:00); off when the room is bright, outside those
hours, or once the desk camera has seen you away. So it doesn't flicker
around the threshold, it goes off only once the room is twice as bright,
and only after the room has stayed on the other side for SETTLE_S. The
sensor must not see the strip (see docs/light.md), or the light would
switch itself off.

By hand: "light on", "light off" or "light auto", by voice, with
`focus light`, on the status page, or a long press on the panel's D button
(which toggles it). A manual on or off holds until "auto" or midnight.

The service writes light.json (on or off, why, the last reading) for the
status page and `focus status`. Without FOCUS_LIGHT_PIN there's no light,
and nothing here runs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Callable

from . import actions, presence, state as st
from .config import Config

log = logging.getLogger(__name__)

ON, OFF, AUTO = "on", "off", "auto"
MODES = (ON, OFF, AUTO)

DEFAULT_HOURS = "08:00-00:00"
DEFAULT_LUX = 30.0
OFF_FACTOR = 2.0       # off only once the room is this many times brighter than "dim"
SETTLE_S = 30          # the room must stay on the other side this long before the light follows
POLL_S = 2             # how often it reads the sensor and the state
RECORD_S = 30          # light.json is rewritten at least this often, and on every change

BH1750_ADDRESS = 0x23  # with its ADDR pin low or unconnected; 0x5c with it high
BH1750_CONTINUOUS_HIGH = 0x10   # 1 lux resolution, a new reading every 120 ms
I2C_SLAVE = 0x0703     # the Linux ioctl that picks the device to talk to


def pin() -> int | None:
    """The GPIO pin of the MOSFET switch, or None when there's no light."""
    raw = os.environ.get("FOCUS_LIGHT_PIN", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("FOCUS_LIGHT_PIN=%r isn't a GPIO number; the light is off", raw)
        return None


def enabled() -> bool:
    return pin() is not None


def hours() -> tuple[clock_time, clock_time]:
    """FOCUS_LIGHT_HOURS as (start, end). An end at or before the start runs
    past midnight; the same time twice means all day."""
    raw = os.environ.get("FOCUS_LIGHT_HOURS", DEFAULT_HOURS)
    try:
        start, end = (clock_time.fromisoformat(part.strip()) for part in raw.split("-"))
    except ValueError:
        raise ValueError(f"FOCUS_LIGHT_HOURS={raw!r}: use HH:MM-HH:MM, e.g. 08:00-00:00") from None
    return start, end


def in_hours(t: clock_time, start: clock_time, end: clock_time) -> bool:
    if start < end:
        return start <= t < end
    return t >= start or t < end


def threshold() -> float:
    raw = os.environ.get("FOCUS_LIGHT_LUX", "")
    try:
        return float(raw) if raw.strip() else DEFAULT_LUX
    except ValueError:
        raise ValueError(f"FOCUS_LIGHT_LUX={raw!r} isn't a number of lux") from None


class Room:
    """Whether the room counts as dim, from lux readings: below `dim` it is,
    above `dim * OFF_FACTOR` it isn't, and a change counts only once the
    readings have agreed for `settle_s`. The first reading counts at once."""

    def __init__(self, dim: float, settle_s: float = SETTLE_S,
                 clock: Callable[[], float] = time.monotonic):
        self.dim_below = dim
        self.bright_above = dim * OFF_FACTOR
        self.settle_s = settle_s
        self.clock = clock
        self.dim: bool | None = None       # None until the first reading
        self._changing_since: float | None = None

    def update(self, lux: float | None) -> bool | None:
        if lux is None:                    # a failed reading changes nothing
            return self.dim
        if self.dim is None:
            self.dim = lux < self.dim_below
            return self.dim
        flip = lux > self.bright_above if self.dim else lux < self.dim_below
        if not flip:
            self._changing_since = None
            return self.dim
        now = self.clock()
        if self._changing_since is None:
            self._changing_since = now
        if now - self._changing_since >= self.settle_s:
            self.dim, self._changing_since = not self.dim, None
        return self.dim


def decide(manual: str | None, dim: bool | None, now: datetime, away: bool,
           span: tuple[clock_time, clock_time]) -> tuple[bool, str]:
    """(on, why). `manual` is the state's light setting: "on", "off" or None (auto)."""
    if manual == ON:
        return True, "on by hand"
    if manual == OFF:
        return False, "off by hand"
    if not in_hours(now.time(), *span):
        return False, "outside its hours"
    if away:
        return False, "you're away"
    if dim is None:
        return False, "no reading from the light sensor"
    return (True, "the room is dim") if dim else (False, "the room is bright")


# --- light.json: what the service last did, for the page and `focus status`

def path(cfg: Config) -> Path:
    return cfg.state_dir / "light.json"


def record(cfg: Config, on: bool, why: str, lux: float | None, at: datetime) -> None:
    # Rewritten every RECORD_S while the service runs, so no fsync.
    st.write_json(path(cfg), {"on": on, "why": why, "lux": None if lux is None else round(lux),
                              "at": at.isoformat(timespec="seconds")}, durable=False)


def read(cfg: Config) -> dict | None:
    try:
        data = json.loads(path(cfg).read_text())
        return data if isinstance(data, dict) and "on" in data else None
    except (OSError, ValueError):
        return None


def describe(cfg: Config, s: st.State) -> str:
    """One line on the light, e.g. "on (the room is dim, 12 lux)"."""
    seen = read(cfg)
    if seen is None:
        return "no word from focus-display yet"
    lux = f", {seen['lux']} lux" if seen.get("lux") is not None else ""
    mode = f"; set {s.light} by hand until midnight" if s.light else ""
    return f"{'on' if seen['on'] else 'off'} ({seen.get('why', '')}{lux}){mode}"


def toggle(cfg: Config, now: datetime) -> str:
    """The D button's long press: off if it's on now, on if it's off. Holds until midnight."""
    seen = read(cfg)
    mode = OFF if seen and seen.get("on") else ON
    with st.locked_state(cfg, today=now.date()) as s:
        actions.set_light(s, mode)
    return f"Light {mode} until midnight."


# --- the hardware

class Sensor:
    """A BH1750 on /dev/i2c-1, read with plain ioctl: no extra library.
    A failed read returns None and is retried, so a loose wire recovers."""

    def __init__(self, bus: int = 1, address: int = BH1750_ADDRESS):
        self.device = f"/dev/i2c-{bus}"
        self.address = address
        self.fd: int | None = None
        self._warned = False

    def read(self) -> float | None:
        import fcntl  # Linux only, like the sensor

        try:
            if self.fd is None:
                self.fd = os.open(self.device, os.O_RDWR)
                fcntl.ioctl(self.fd, I2C_SLAVE, self.address)
                os.write(self.fd, bytes([BH1750_CONTINUOUS_HIGH]))
                time.sleep(0.2)            # the first reading takes up to 180 ms
            raw = os.read(self.fd, 2)
        except OSError as exc:
            if not self._warned:
                log.warning("Can't read the light sensor at 0x%02x on %s (%s); "
                            "the light stays off until it answers", self.address, self.device, exc)
                self._warned = True
            self.close()
            return None
        if self._warned:
            log.info("The light sensor answers again")
            self._warned = False
        return int.from_bytes(raw, "big") / 1.2   # the datasheet's counts per lux

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Switch:
    """The MOSFET module's signal pin, as a gpiod output (gpiod comes with inky)."""

    def __init__(self, gpio_pin: int):
        import gpiod
        import gpiodevice
        from gpiod.line import Direction, Value

        self._value = Value
        chip = gpiodevice.find_chip_by_platform()
        self.offset = chip.line_offset_from_id(gpio_pin)
        settings = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)
        self.request = chip.request_lines(consumer="focus-light", config={self.offset: settings})
        log.info("Front light on GPIO %d", gpio_pin)

    def set(self, on: bool) -> None:
        self.request.set_value(self.offset, self._value.ACTIVE if on else self._value.INACTIVE)


def run(cfg: Config, switch, sensor, *, room: Room | None = None,
        span: tuple[clock_time, clock_time] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
        running: Callable[[], bool] = lambda: True, poll_s: float = POLL_S) -> None:
    """Keep the light where `decide` says, until `running` says stop."""
    room = room or Room(threshold(), clock=clock)
    span = span or hours()
    shown: bool | None = None
    recorded_at = float("-inf")
    last_why = ""
    while running():
        t = now()
        try:
            manual = st.peek(cfg).light
        except st.StateError as exc:
            log.warning("Can't read the state: %s", exc)
            manual = None
        lux = sensor.read()
        on, why = decide(manual, room.update(lux), t, presence.away(cfg, t), span)
        if on != shown:
            log.info("Front light %s: %s", "on" if on else "off", why)
            try:
                switch.set(on)
            except Exception:   # keep reading; the next change tries again
                log.exception("Switching the front light failed")
            else:
                shown = on
        if why != last_why or clock() - recorded_at >= RECORD_S:
            record(cfg, on, why, lux, t)
            recorded_at, last_why = clock(), why
        sleep(poll_s)


def start(cfg: Config) -> bool:
    """Run the light in a background thread of the display service, if there
    is one. If the pin can't be had, it logs why and the panel goes on."""
    import threading

    gpio_pin = pin()
    if gpio_pin is None:
        return False
    try:
        switch, sensor = Switch(gpio_pin), Sensor()
        room, span = Room(threshold()), hours()
    except Exception:
        log.exception("The front light couldn't start; the panel works without it")
        return False

    def go() -> None:
        try:
            run(cfg, switch, sensor, room=room, span=span)
        except Exception:
            log.exception("The front light stopped; restart focus-display to bring it back")

    threading.Thread(target=go, daemon=True, name="light").start()
    return True
