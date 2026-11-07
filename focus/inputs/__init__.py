"""Input adapters (later phases). Each one turns hardware into calls on
`focus.actions` inside `focus.state.locked_state`, and nothing else:

    buttons.py  Inky Impression A/B/C/D -> complete the suggested item / undo /
                pause or resume / redraw (gpiod, read by the focus-display service)
    page.py     the status page: the e-ink layout in a browser, and a list editor
    voice.py    speakerphone play button -> faster-whisper -> the chat coach,
                reply spoken by the speech output

They run as their own systemd services, so a crash in one never takes down
the scheduled check-ins.
"""
