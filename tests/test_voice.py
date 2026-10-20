import array
import io
import sys
import time
import wave

import pytest

from focus import config
from focus.coach import CoachError
from focus.inputs import voice


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_STATE_DIR", str(tmp_path))
    return config.load()


def event(type_, code, value):
    return voice._EVENT.pack(0, 0, type_, code, value)


def test_key_presses_counts_only_the_press():
    raw = b"".join([
        event(4, 4, 0xC00CD),                    # MSC_SCAN
        event(voice.EV_KEY, voice.KEY_PLAYPAUSE, 1),
        event(0, 0, 0),                          # SYN_REPORT
        event(voice.EV_KEY, voice.KEY_PLAYPAUSE, 0),
        event(voice.EV_KEY, 115, 1),             # volume up
        event(voice.EV_KEY, voice.KEY_PLAYPAUSE, 1),
    ])
    assert len(list(voice.key_presses(io.BytesIO(raw)))) == 2


def test_find_input_matches_by_name(tmp_path):
    for n, name in [(4, "Anker Anker PowerConf Consumer Control"), (5, "Anker Anker PowerConf")]:
        (tmp_path / f"event{n}" / "device").mkdir(parents=True)
        (tmp_path / f"event{n}" / "device" / "name").write_text(name + "\n")
    assert voice.find_input("PowerConf Consumer Control", tmp_path).name == "event4"
    with pytest.raises(FileNotFoundError):
        voice.find_input("Jabra", tmp_path)


def test_level():
    assert voice.level(array.array("h", [0] * 1600).tobytes()) == 0
    assert voice.level(array.array("h", [1000, -1000] * 800).tobytes()) == pytest.approx(1000)


def feed_all(ep, levels):
    for lvl in levels:
        verdict = ep.feed(lvl)
        if verdict:
            return verdict
    return None


def test_endpointer_speech_then_pause():
    ep = voice.Endpointer()
    # Three chunks of room noise set the threshold, then speech, then 0.8 s (10 chunks) quiet.
    assert feed_all(ep, [100, 120, 110] + [2000] * 10 + [100] * 9) is None
    assert ep.feed(100) == "done"
    assert ep.threshold == 330  # 3 x the median noise level (110)


def test_endpointer_threshold_has_a_floor():
    ep = voice.Endpointer()
    feed_all(ep, [5, 5, 5])
    assert ep.threshold == ep.floor


def test_endpointer_nobody_speaks():
    ep = voice.Endpointer(start_timeout_s=1.0)
    assert feed_all(ep, [100] * 3 + [100] * 20) == "nothing"


def test_endpointer_max_length():
    ep = voice.Endpointer(fixed_level=500, max_s=2.0)
    assert feed_all(ep, [2000] * 30) == "done"


def test_tone_is_a_wav():
    with wave.open(io.BytesIO(voice.LISTENING)) as w:
        assert w.getframerate() == 22050 and w.getnframes() > 0


class FakeStream:
    def __init__(self, sink):
        self.sink, self.parts, self.spoke, self.first_audio_at = sink, [], False, None

    def feed(self, text):
        self.parts.append(text)
        self.spoke = True

    def finish(self):
        self.sink.append("".join(self.parts))

    def wait(self, timeout=None):
        return True  # "played" instantly

    def stop(self):
        pass


class Recorder:
    def __init__(self, cfg, audio=b"\x00\x00" * 100, heard="add buy coffee filters",
                 reply="Added buy coffee filters.", fail=None, hint=None):
        self.spoken, self.played, self.asked, self.hints = [], [], [], []

        def transcribe(a, hint):
            self.hints.append(hint)
            return heard

        def ask(_cfg, text, on_text, voice):
            assert voice is True  # spoken turns ask for short replies
            self.asked.append(text)
            if fail:
                raise CoachError(fail)
            for i, word in enumerate(reply.split(" ")):  # arrives in pieces, like a stream
                on_text(word if i == 0 else " " + word)
            return reply

        self.voice = voice.Voice(
            cfg, speak=self.spoken.append, stream=lambda: FakeStream(self.spoken),
            play=self.played.append, listen=lambda stop: audio, transcribe=transcribe, ask=ask,
            hint=lambda: hint,
        )


def test_turn_hears_asks_and_speaks(cfg):
    r = Recorder(cfg)
    r.voice.turn(lambda: False)
    assert r.played == [voice.LISTENING, voice.GOT_IT]
    assert r.asked == ["add buy coffee filters"]
    assert r.spoken == ["Added buy coffee filters."]


def test_turn_with_silence_just_chimes(cfg):
    r = Recorder(cfg, audio=None)
    r.voice.turn(lambda: False)
    assert r.played == [voice.LISTENING, voice.NOTHING]
    assert r.asked == [] and r.spoken == []


def test_turn_with_nothing_understood(cfg):
    r = Recorder(cfg, heard="")
    r.voice.turn(lambda: False)
    assert r.asked == []
    assert r.spoken == ["Sorry, I didn't catch that."]


def test_turn_speaks_coach_errors(cfg):
    r = Recorder(cfg, fail="Daily chat limit (50) reached")
    r.voice.turn(lambda: False)
    assert r.spoken == ["Sorry, that didn't work. Daily chat limit (50) reached"]


SILENCE = b"\x00\x00" * voice.CHUNK_SAMPLES


class Done(Exception):
    pass


class FakeMic:
    """Hands out silent chunks, then stops the endless loop."""

    def __init__(self, chunks=50):
        self.left, self.cleared = chunks, 0

    def read(self):
        if self.left == 0:
            raise Done
        self.left -= 1
        time.sleep(0.001)  # let the button thread run
        return SILENCE

    def clear(self):
        self.cleared += 1


class FakeWake:
    name = "hey_jarvis"
    last_score = 0.93

    def __init__(self, fire_on):
        self.calls, self.resets, self.fire_on = 0, 0, fire_on

    def heard(self, chunk):
        self.calls += 1
        return self.calls in self.fire_on

    def reset(self):
        self.resets += 1


class CountingVoice:
    def __init__(self):
        self.turns = 0

    def turn(self, pressed_again):
        self.turns += 1


def test_run_stops_when_the_button_goes_away():
    with pytest.raises(RuntimeError, match="Lost"):
        voice.run(CountingVoice(), FakeMic(), iter([]))


def test_wake_word_starts_a_turn_and_is_reset_after():
    v, mic, wake = CountingVoice(), FakeMic(), FakeWake(fire_on={3, 10})
    with pytest.raises(Done):
        voice.run(v, mic, None, wake)
    assert v.turns == 2
    assert wake.resets == 2 and mic.cleared == 2


def test_button_starts_a_turn_without_a_wake_word():
    v = CountingVoice()
    presses = iter([None])  # one press; then the button "goes away"
    with pytest.raises((Done, RuntimeError)):
        voice.run(v, FakeMic(), presses)
    assert v.turns == 1


def _mic(script, **kw):
    return voice.Mic("test", command=[sys.executable, "-c", script], **kw)


def test_mic_reads_whole_chunks_then_reports_arecord_failing():
    mic = _mic(f"import sys; sys.stdout.buffer.write(b'\\x01\\x00' * {voice.CHUNK_SAMPLES * 2}); "
               "sys.stderr.write('device gone')")
    assert mic.read() == b"\x01\x00" * voice.CHUNK_SAMPLES
    assert mic.read() == b"\x01\x00" * voice.CHUNK_SAMPLES
    with pytest.raises(RuntimeError, match="device gone"):
        mic.read()
    with pytest.raises(RuntimeError):  # and keeps failing
        mic.read()


def test_mic_keeps_only_the_newest_audio():
    # 10 chunks numbered 0..9 into a queue that holds 4: the oldest are dropped.
    script = ("import sys\n"
              f"for i in range(10): sys.stdout.buffer.write(bytes([i, 0]) * {voice.CHUNK_SAMPLES})\n")
    mic = _mic(script, keep_s=4 * voice.CHUNK_SECONDS)
    mic.proc.wait(timeout=10)
    time.sleep(0.2)  # let the reader thread finish
    first = mic.read()
    assert first[0] >= 5  # at most four chunks plus the end marker survive
    mic.clear()
    with pytest.raises(RuntimeError):  # clear() keeps the end marker
        mic.read()


def bare_wake(threshold=0.5, frames=3):
    """A WakeWord without loading openWakeWord, to test its frame counting."""
    w = object.__new__(voice.WakeWord)
    w.name, w.threshold, w.frames, w.last_score, w._run = "hey_jarvis", threshold, frames, 0.0, []
    return w


def test_wake_needs_enough_high_frames_in_a_row():
    w = bare_wake()
    assert [w.score(s) for s in [0.9, 0.9, 0.2, 0.9, 0.8]] == [False] * 5   # broken by a low frame
    assert w.score(0.95) is True                                            # third high in a row
    assert w.last_score == 0.95
    assert w.score(0.9) is False                                            # starts counting again


def test_one_loud_frame_is_not_enough():
    w = bare_wake()
    assert not any(w.score(s) for s in [0.0, 1.0, 0.0, 1.0, 0.0])


@pytest.mark.parametrize("phantom", ["You", "you.", "Thank you.", "Thanks for watching!"])
def test_whisper_phantoms_are_ignored(cfg, phantom):
    r = Recorder(cfg, heard=phantom)
    r.voice.turn(lambda: False)
    assert r.asked == [] and r.spoken == []
    assert r.played == [voice.LISTENING, voice.GOT_IT, voice.NOTHING]


def test_real_short_commands_still_go_through(cfg):
    r = Recorder(cfg, heard="You there?")
    r.voice.turn(lambda: False)
    assert r.asked == ["You there?"]


class Marks:
    def __init__(self, first_text_at=None, first_sentence_at=None, first_audio_at=None):
        self.first_text_at, self.first_sentence_at, self.first_audio_at = first_text_at, first_sentence_at, first_audio_at


def test_timing_splits_the_wait():
    # Recording ended at 100.0; text at 103.7; coach's first words 104.9; sentence 105.4; audio 106.0.
    line = voice.timing(2.7, 100.0, 103.7, Marks(104.9, 105.4, 106.0), 110.0)
    assert line == ("2.7 s of speech, transcribe 3.7 s, coach 1.2 s, sentence 0.5 s, voice 0.6 s, "
                    "first word at 6.0 s, done at 10.0 s")


def test_timing_when_nothing_was_spoken():
    line = voice.timing(1.0, 100.0, 101.0, Marks(), 102.0)
    assert "coach -" in line and "first word at -" in line


def test_priorities_go_to_the_recogniser_as_a_hint(cfg):
    from focus import actions, state as st
    with st.locked_state(cfg) as s:
        actions.add_priority(s, "Study for the biology exam")
        done = actions.add_priority(s, "Old thing")
        actions.complete_priority(s, done.id)
    assert voice.vocabulary(cfg) == "My priorities: Study for the biology exam."
    hint = voice.vocabulary(cfg)
    r = Recorder(cfg, heard="add review biology notes", hint=hint)
    r.voice.turn(lambda: False)
    assert r.hints == [hint] and r.asked == ["add review biology notes"]


def test_no_priorities_no_hint(cfg):
    assert voice.vocabulary(cfg) is None


def test_hint_echoed_back_is_ignored(cfg):
    hint = "My priorities: Study for the biology exam."
    r = Recorder(cfg, heard="Study for the biology exam", hint=hint)
    r.voice.turn(lambda: False)
    assert r.asked == [] and r.played[-1] == voice.NOTHING


def test_model_setting_picks_the_recogniser(monkeypatch):
    made = []
    monkeypatch.setattr(voice, "Transcriber", lambda name, d: made.append(("whisper", name)))
    monkeypatch.setattr(voice, "MoonshineTranscriber", lambda name, d: made.append(("moonshine", name)))
    voice.make_transcriber("tiny.en", "/m")
    voice.make_transcriber("moonshine/tiny", "/m")
    assert made == [("whisper", "tiny.en"), ("moonshine", "moonshine/tiny")]


def followup_voice(cfg, heard, listens, fail_on=()):
    """A Voice whose listen() returns `listens` in order; records what happened."""
    log = {"listen": [], "asked": [], "played": [], "spoken": []}
    queue = list(listens)
    heard_queue = list(heard)

    def listen(stop, start_timeout_s=None):
        log["listen"].append(start_timeout_s)
        return queue.pop(0)

    def ask(_cfg, text, on_text, voice):
        log["asked"].append(text)
        if text in fail_on:
            raise CoachError("no network")
        on_text("Done.")

    v = voice.Voice(cfg, speak=log["spoken"].append, stream=lambda: FakeStream(log["spoken"]),
                    play=log["played"].append, listen=listen,
                    transcribe=lambda a, hint: heard_queue.pop(0), ask=ask, followup_s=5)
    return v, log


def test_follow_up_needs_no_wake_word(cfg):
    audio = b"\x00\x00" * 100
    v, log = followup_voice(cfg, ["add car inspection", "move it to the top"], [audio, audio, None])
    v.turn(lambda: False)
    assert log["asked"] == ["add car inspection", "move it to the top"]
    assert log["listen"] == [None, 5, 5]          # the first waits as usual, follow-ups 5 s
    assert log["played"].count(voice.FOLLOW_UP) == 2
    assert voice.NOTHING not in log["played"]     # a quiet end, no low tone


def test_no_follow_up_after_a_mis_hearing_or_failure(cfg):
    audio = b"\x00\x00" * 100
    v, log = followup_voice(cfg, ["You"], [audio])
    v.turn(lambda: False)
    assert log["asked"] == [] and voice.FOLLOW_UP not in log["played"]

    v, log = followup_voice(cfg, ["add x"], [audio], fail_on={"add x"})
    v.turn(lambda: False)
    assert log["asked"] == ["add x"] and voice.FOLLOW_UP not in log["played"]


def talk_voice(cfg, heard, listens):
    log = {"listen": [], "asked": [], "spoken": []}
    queue, heard_queue = list(listens), list(heard)

    def listen(stop, start_timeout_s=None):
        log["listen"].append(start_timeout_s)
        return queue.pop(0)

    def ask(_cfg, text, on_text, voice, talk=False):
        log["asked"].append((text, talk))
        on_text("Okay.")

    v = voice.Voice(cfg, speak=log["spoken"].append, stream=lambda: FakeStream(log["spoken"]),
                    play=lambda wav: None, listen=listen,
                    transcribe=lambda a, hint: heard_queue.pop(0), ask=ask,
                    followup_s=5, talk_followup_s=10)
    return v, log


def test_talk_mode_has_longer_replies_and_windows_until_thats_all(cfg):
    audio = b"\x00\x00" * 100
    v, log = talk_voice(cfg, ["Let's talk.", "I can't decide what to start with", "That's all, thanks"],
                        [audio, audio, audio])
    v.turn(lambda: False)
    assert log["spoken"][0] == "Sure. What's on your mind?"   # the switch alone isn't sent
    assert log["asked"] == [("I can't decide what to start with", True)]
    assert log["listen"] == [None, 10, 10]                    # talk-mode windows
    assert log["spoken"][-1] == "Okay, back to it."
    assert not v.talking


def test_talk_mode_ends_with_a_quiet_window(cfg):
    audio = b"\x00\x00" * 100
    v, log = talk_voice(cfg, ["let's talk about the exam plan", "add x"], [audio, None, audio, None])
    v.turn(lambda: False)
    assert log["asked"] == [("let's talk about the exam plan", True)]  # more than the switch: sent
    assert not v.talking
    v.turn(lambda: False)                                     # the next wake word: short again
    assert log["asked"][-1] == ("add x", False)
    assert log["listen"][-1] == 5


def test_follow_up_off_with_zero(cfg):
    audio = b"\x00\x00" * 100
    v, log = followup_voice(cfg, ["add x"], [audio])
    v.followup_s = 0
    v.turn(lambda: False)
    assert log["listen"] == [None] and voice.FOLLOW_UP not in log["played"]


class Reports:
    """Hands out one HID report per read(), as /dev/hidraw does."""

    def __init__(self, *reports):
        self.reports = list(reports)

    def read(self, n):
        return self.reports.pop(0) if self.reports else b""


def test_call_presses_are_the_02_01_reports():
    stream = Reports(b"\x02\x01", b"\x01\x08", b"\x01\x00", b"\x02\x01", b"\x02\x01")
    assert len(list(voice.call_presses(stream))) == 3   # play's 01 08 / 01 00 don't count


def test_find_hidraw_by_usb_id(tmp_path):
    for n, hid in [(0, "0003:0000046D:0000C52B"), (1, "0003:0000291A:00003301")]:
        (tmp_path / f"hidraw{n}" / "device").mkdir(parents=True)
        (tmp_path / f"hidraw{n}" / "device" / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID={hid}\n")
    assert voice.find_hidraw(voice.POWERCONF_HID, tmp_path).name == "hidraw1"
    assert voice.find_hidraw("0000FFFF:0000FFFF", tmp_path) is None


def test_call_button_marks_the_suggested_item_done(cfg):
    from focus import actions, state as st
    assert voice.mark_suggested_done(cfg) == "Nothing left to mark done."
    with st.locked_state(cfg) as s:
        for text in ("Write the report", "Email Sam", "Book dentist"):
            actions.add_priority(s, text)
    assert voice.mark_suggested_done(cfg) == "Tell me which one is done."   # no check-in picked one
    with st.locked_state(cfg) as s:
        s.checkins.append({"slot": "morning", "at": st.now_iso(), "reply": "Email Sam first.", "start_id": 2})
    assert voice.mark_suggested_done(cfg) == "Done: Email Sam. 2 left on your list."
    assert voice.mark_suggested_done(cfg) == "Tell me which one is done."   # its pick is done
    with st.locked_state(cfg) as s:
        actions.complete_priority(s, 1)
    assert voice.mark_suggested_done(cfg) == "Done: Book dentist. That's everything on your list."


def _spaced_presses(*gaps):
    """Call presses separated by these gaps (seconds), then the button stays connected."""
    import threading
    for gap in gaps:
        time.sleep(gap)
        yield None
    threading.Event().wait()


def test_single_call_presses_mark_done():
    v, singles, doubles = CountingVoice(), [], []
    with pytest.raises(Done):
        voice.run(v, FakeMic(chunks=400), None, calls=_spaced_presses(0, 0.6),
                  on_call=lambda: singles.append(1), on_double_call=lambda: doubles.append(1))
    assert len(singles) == 2 and doubles == [] and v.turns == 0


def test_double_call_press_undoes():
    v, singles, doubles = CountingVoice(), [], []
    with pytest.raises(Done):
        voice.run(v, FakeMic(chunks=400), None, calls=_spaced_presses(0, 0.1),
                  on_call=lambda: singles.append(1), on_double_call=lambda: doubles.append(1))
    assert singles == [] and doubles == [1]


def test_play_press_during_the_double_press_wait_still_starts_a_turn():
    q = voice.queue.Queue()
    q.put("play")
    assert voice.second_call_follows(q, window_s=0.05) is False
    assert q.get_nowait() == "play"


def test_undo_last_done(cfg):
    from focus import actions, state as st
    assert voice.undo_last_done(cfg) == "Nothing to undo."
    with st.locked_state(cfg) as s:
        a = actions.add_priority(s, "Write the report")
        b = actions.add_priority(s, "Email Sam")
        actions.complete_priority(s, a.id)
        s.find(a.id).done_at = "2026-10-12T10:00:00-05:00"
        actions.complete_priority(s, b.id)
        s.find(b.id).done_at = "2026-10-12T11:00:00-05:00"
    assert voice.undo_last_done(cfg) == "Undone: Email Sam is back on your list."
    with st.locked_state(cfg) as s:
        assert [p.text for p in s.open_priorities] == ["Email Sam"]


def test_play_while_the_coach_speaks_cuts_it_off(cfg):
    class SlowStream(FakeStream):
        stopped = False

        def wait(self, timeout=None):
            if timeout is not None:
                time.sleep(timeout)
            return self.stopped           # never finishes on its own

        def stop(self):
            SlowStream.stopped = True

    presses = iter([False, False, True])   # the third check sees a play press
    asked = []
    v = voice.Voice(cfg, speak=lambda t: None, stream=lambda: SlowStream([]), play=lambda w: None,
                    listen=lambda stop, start_timeout_s=None: b"\x00\x00" * 100,
                    transcribe=lambda a, hint: "tell me everything",
                    ask=lambda c, t, on_text, voice: (asked.append(t), on_text("A long answer. "))[0],
                    followup_s=5)
    assert v.answer(b"\x00\x00" * 100, lambda: next(presses)) is False   # cut off: no follow-up
    assert SlowStream.stopped and asked == ["tell me everything"]


def test_call_during_a_turn_is_ignored():
    import threading
    turn_started, forever = threading.Event(), threading.Event()

    def presses():          # one play press, then the button stays connected
        yield None
        forever.wait()

    def calls():            # a call press that arrives during the turn
        turn_started.wait()
        yield None
        forever.wait()

    seen, called = [], []

    class PeekingVoice:
        def turn(self, pressed_again):
            turn_started.set()
            time.sleep(0.1)                 # let the call press land in the queue
            seen.append(pressed_again())    # a call press must not end the turn

    with pytest.raises(Done):
        voice.run(PeekingVoice(), FakeMic(), presses(), calls=calls(), on_call=lambda: called.append(1))
    forever.set()
    assert seen == [False]   # ignored during the turn...
    assert called == []      # ...and dropped afterwards, not run late


def test_mic_gives_up_when_arecord_goes_quiet():
    # arecord left hanging on a vanished device: no output, no exit.
    mic = _mic("import time; time.sleep(30)", stall_s=0.3)
    with pytest.raises(RuntimeError, match="No audio"):
        mic.read()
    assert mic.proc.wait(timeout=5) is not None  # the hung arecord is killed


def test_run_stops_when_a_button_reader_crashes():
    def calls():
        yield None
        raise OSError(5, "Input/output error")  # the PowerConf left USB

    with pytest.raises(RuntimeError, match="Lost"):
        voice.run(CountingVoice(), FakeMic(chunks=400), None, calls=calls(), on_call=lambda: None)
