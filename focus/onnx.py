"""onnxruntime without its telemetry.

Piper, the wake word and the desk camera's model all run on onnxruntime,
which in recent versions has telemetry on by default. Nothing here should
report anything anywhere, so each service turns it off before loading a
model. (The camera's unit has no network anyway; the voice service's does,
for the model host.)
"""

from __future__ import annotations


def no_telemetry() -> None:
    try:
        import onnxruntime
    except ImportError:
        return  # not installed on this machine; nothing to turn off
    disable = getattr(onnxruntime, "disable_telemetry_events", None)
    if disable is not None:
        disable()
