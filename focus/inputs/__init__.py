"""Input adapters (later phases). Each one turns hardware into calls on
`focus.actions` inside `focus.state.locked_state`, and nothing else:

    buttons.py  Inky Impression A/B/C/D -> complete the suggested item / on track /
                drifted / pause (gpiozero, long-running service)
    chat.py     the coach from the phone (an iOS Shortcut or Siri) over the home network
    page.py     the status page: the e-ink layout in a browser, read-only
    voice.py    speakerphone play button -> faster-whisper -> the chat coach,
                reply spoken by the speech output

They run as their own systemd services, so a crash in one never takes down
the scheduled check-ins.
"""
