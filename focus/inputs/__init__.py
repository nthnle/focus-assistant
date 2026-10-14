"""Input adapters (later phases). Each one turns hardware into calls on
`focus.actions` inside `focus.state.locked_state`, and nothing else:

    buttons.py  Inky Impression A/B/C/D -> complete top priority / on track /
                drifted / snooze (gpiozero, long-running service)
    dock.py     iPhone Shortcut reports charger on/off over the home network
                -> PHONE_OUT / PHONE_IN events
    voice.py    push-to-talk -> whisper.cpp -> intent -> an action, reply via
                the speech output

They run as their own systemd services, so a crash in one never takes down
the scheduled check-ins.
"""
