import pytest

from epsilon.channels import voice
from epsilon.voice import capture


class FakeTimer:
    """Hace de threading.Timer: no espera; el test lo dispara a mano."""

    made = []

    def __init__(self, seconds, function):
        self.seconds = seconds
        self.function = function
        self.started = False
        self.cancelled = False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function()


class Mic:
    """Reemplaza a voice.capture: nadie graba de verdad y cada grabación dura `seconds`."""

    def __init__(self, monkeypatch):
        self.seconds = 3.0
        self.fail_start = None
        self.fail_stop = None
        self.started = 0
        self.cancelled = 0
        monkeypatch.setattr(capture, "start", self.start)
        monkeypatch.setattr(capture, "stop", self.stop)
        monkeypatch.setattr(capture, "cancel", self.cancel)

    def start(self, folder):
        if self.fail_start:
            raise capture.CaptureError(self.fail_start)
        self.started += 1
        path = folder / capture.FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return capture.Recording(path, None)

    def stop(self, recording):
        if self.fail_stop:
            raise capture.CaptureError(self.fail_stop)
        return capture.Clip(recording.path, self.seconds)

    def cancel(self, recording):
        self.cancelled += 1
        recording.path.unlink(missing_ok=True)


@pytest.fixture
def mic(monkeypatch):
    return Mic(monkeypatch)


@pytest.fixture
def companion(mic, tmp_path):
    FakeTimer.made = []
    said, clips = [], []
    instance = voice.Companion(tmp_path, said.append, clips.append, timer=FakeTimer)
    instance.said, instance.clips = said, clips
    return instance


def test_the_first_toggle_starts_listening(companion):
    assert companion.toggle() == "recording"

    assert companion.status() == "recording"
    assert companion.said == ["Escuchando…"]


def test_the_second_toggle_stops_and_hands_over_the_clip(companion):
    companion.toggle()

    assert companion.toggle() == "idle"

    assert companion.status() == "idle"
    assert [clip.seconds for clip in companion.clips] == [3.0]
    assert companion.clips[0].path.exists()  # quien lo recibe lo borra cuando lo usa


def test_a_clip_too_short_to_be_speech_is_dropped(companion, mic):
    mic.seconds = 0.2
    companion.toggle()

    companion.toggle()

    assert companion.clips == []
    assert companion.said[-1] == "No te escuché: probá de nuevo."
    assert not (companion.folder / capture.FILE_NAME).exists()


def test_a_microphone_error_is_said_and_nothing_stays_recording(companion, mic):
    mic.fail_start = "Falta el programa «pw-record» (PipeWire) para grabar el micrófono."

    assert companion.toggle() == "idle"

    assert companion.status() == "idle"
    assert companion.said == [mic.fail_start]


def test_an_error_when_stopping_is_said_and_goes_back_to_idle(companion, mic):
    companion.toggle()
    mic.fail_stop = "No quedó nada grabado: revisá que haya un micrófono conectado y activo."

    assert companion.toggle() == "idle"

    assert companion.status() == "idle"
    assert companion.said[-1] == mic.fail_stop


def numbers(**values):
    return lambda key: values[key]


def test_the_recording_is_cut_after_the_limit_as_if_the_hotkey_was_pressed(companion, monkeypatch):
    monkeypatch.setattr(voice.config, "number", numbers(VOICE_MAX_RECORD_S=45, VOICE_MAX_AUDIO_MINUTES=10, VOICE_MAX_AUDIO_MB=25))
    companion.toggle()
    timer = FakeTimer.made[-1]

    assert (timer.seconds, timer.started) == (45, True)
    timer.fire()

    assert companion.status() == "idle"
    assert len(companion.clips) == 1
    assert "límite de 45 s" in " ".join(companion.said)


def test_the_limit_is_never_more_than_what_can_be_transcribed_afterwards(monkeypatch):
    # No tiene sentido grabar una charla de media hora si después Whisper la rechaza por larga: se corta antes.
    monkeypatch.setattr(voice.config, "number", numbers(VOICE_MAX_RECORD_S=3600, VOICE_MAX_AUDIO_MINUTES=10, VOICE_MAX_AUDIO_MB=25))

    assert voice.limit() == (600, "voice.max_audio_minutes")


def test_the_limit_also_respects_the_size_the_transcriber_accepts(monkeypatch):
    monkeypatch.setattr(voice.config, "number", numbers(VOICE_MAX_RECORD_S=3600, VOICE_MAX_AUDIO_MINUTES=120, VOICE_MAX_AUDIO_MB=1))

    seconds, source = voice.limit()

    assert source == "voice.max_audio_mb"
    assert seconds == 1024 * 1024 // (capture.SAMPLE_RATE * 2)  # 16 kHz, 16 bits, mono


def test_when_your_own_limit_is_the_smallest_nothing_else_is_blamed(monkeypatch):
    monkeypatch.setattr(voice.config, "number", numbers(VOICE_MAX_RECORD_S=180, VOICE_MAX_AUDIO_MINUTES=10, VOICE_MAX_AUDIO_MB=25))

    assert voice.limit() == (180, "")


def test_hitting_a_limit_set_by_the_transcriber_says_which_setting_to_raise(companion, monkeypatch):
    monkeypatch.setattr(voice.config, "number", numbers(VOICE_MAX_RECORD_S=3600, VOICE_MAX_AUDIO_MINUTES=10, VOICE_MAX_AUDIO_MB=25))
    companion.toggle()

    FakeTimer.made[-1].fire()

    assert "voice.max_audio_minutes" in " ".join(companion.said)


def test_stopping_by_hand_cancels_the_limit_timer(companion):
    companion.toggle()
    timer = FakeTimer.made[-1]

    companion.toggle()

    assert timer.cancelled


def test_a_stale_limit_timer_does_not_cut_the_next_recording(companion):
    companion.toggle()
    old = FakeTimer.made[-1]
    companion.toggle()
    companion.toggle()  # otra grabación, con su propio temporizador

    old.fire()

    assert companion.status() == "recording"


def test_cancel_throws_the_recording_away(companion, mic):
    companion.toggle()

    assert companion.cancel() == "idle"

    assert mic.cancelled == 1
    assert companion.clips == []
    assert companion.status() == "idle"


def test_cancel_while_idle_does_nothing(companion, mic):
    assert companion.cancel() == "idle"

    assert mic.cancelled == 0


def test_a_failing_clip_handler_never_leaves_it_stuck(companion):
    def broken(clip):
        raise RuntimeError("boom")

    companion.on_clip = broken
    companion.toggle()

    assert companion.toggle() == "idle"

    assert companion.status() == "idle"
    assert companion.said[-1].startswith("Algo falló")


def test_the_default_handler_reports_the_length_and_deletes_the_audio(mic, tmp_path):
    said = []
    companion = voice.Companion(tmp_path, said.append, timer=FakeTimer)
    mic.seconds = 4.25
    companion.toggle()

    companion.toggle()

    assert said[-1] == "Grabé 4,2 s."
    assert not (tmp_path / capture.FILE_NAME).exists()


# --- El socket y el canal entero ---------------------------------------------------------------------------------


import socket as socket_module
import threading
import time

from epsilon import state
from epsilon.utils import hyprland
from epsilon.voice import client


def ask(path, word):
    with socket_module.socket(socket_module.AF_UNIX) as conn:
        conn.settimeout(3)
        conn.connect(str(path))
        conn.sendall(word if isinstance(word, bytes) else (word + "\n").encode())
        return conn.recv(100).decode().strip()


def wait_for(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Spy:
    """Un Companion falso que anota qué le pidieron."""

    def __init__(self):
        self.calls = []

    def toggle(self):
        self.calls.append("toggle")
        return "recording"

    def cancel(self):
        self.calls.append("cancel")
        return "idle"

    def status(self):
        self.calls.append("status")
        return "idle"


def test_only_three_words_are_understood_and_anything_else_is_an_error():
    spy = Spy()

    assert voice.dispatch(spy, "toggle") == "recording"
    assert voice.dispatch(spy, "cancel") == "idle"
    assert voice.dispatch(spy, "status") == "idle"
    assert voice.dispatch(spy, "rm -rf ~") == "error"
    assert voice.dispatch(spy, "") == "error"
    assert spy.calls == ["toggle", "cancel", "status"]


def test_the_socket_is_private(tmp_path):
    listener = voice.bind(tmp_path / "s.sock")
    try:
        assert (tmp_path / "s.sock").stat().st_mode & 0o777 == 0o600
    finally:
        listener.close()


def test_a_stale_socket_file_from_a_crash_is_replaced(tmp_path):
    path = tmp_path / "s.sock"
    old = socket_module.socket(socket_module.AF_UNIX)
    old.bind(str(path))
    old.close()  # el archivo queda, pero nadie escucha

    listener = voice.bind(path)
    listener.close()


def test_a_second_companion_refuses_to_take_over_a_live_socket(tmp_path):
    path = tmp_path / "s.sock"
    first = voice.bind(path)
    try:
        with pytest.raises(SystemExit, match="Ya hay otro"):
            voice.bind(path)
    finally:
        first.close()


def test_serving_answers_each_word_over_the_real_socket(tmp_path):
    path = tmp_path / "s.sock"
    listener = voice.bind(path)
    spy, stop = Spy(), threading.Event()
    thread = threading.Thread(target=voice.serve, args=(listener, spy, stop, lambda: None), daemon=True)
    thread.start()
    try:
        assert ask(path, "toggle") == "recording"
        assert ask(path, "status") == "idle"
        assert ask(path, "evil") == "error"
        assert ask(path, b"x" * 500) == "error"  # una línea larguísima no se obedece ni se parte
    finally:
        stop.set()
        thread.join(5)
        listener.close()
    assert spy.calls == ["toggle", "status"]


@pytest.fixture
def running(tmp_path, monkeypatch, mic):
    """El canal entero andando en un hilo, con el socket, los datos y Hyprland en carpetas y espías de prueba."""
    from epsilon.data import notes

    runtime, data = tmp_path / "run", tmp_path / "data"
    runtime.mkdir(mode=0o700)
    data.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(notes, "DATA_DIR", data)
    monkeypatch.setattr(voice, "POLL_SECONDS", 0.02)
    evals, notes_said = [], []

    def fake_ctl(*args):  # un Hyprland con config en Lua: contesta «ok» y anotamos el código que le llega
        if args[0] == "eval" and args[1] != hyprland.PROBE:
            evals.append(args[1])
        return (0, "ok") if args[0] == "eval" else (1, "Invalid request")

    monkeypatch.setattr(hyprland, "_ctl", fake_ctl)
    monkeypatch.setattr(voice.notify, "desktop", lambda text: notes_said.append(text) or "")
    swept = []
    monkeypatch.setattr(capture, "sweep", swept.append)
    heard = []
    monkeypatch.setattr(voice.transcribe, "audio_to_text", lambda path: heard.append(path) or "hola mundo")
    voice.reset()
    errors = []

    def target():
        try:
            voice.run()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert wait_for(client.socket_path().exists)

    class Handle:
        pass

    handle = Handle()
    handle.thread, handle.evals, handle.said, handle.errors = thread, evals, notes_said, errors
    handle.data, handle.socket = data, client.socket_path()
    handle.swept, handle.heard = swept, heard
    yield handle
    voice.stop()
    thread.join(5)


def test_the_channel_writes_its_script_and_both_config_files_and_binds_the_hotkey(running):
    script = running.data / client.FOLDER / client.SCRIPT_NAME
    folder = running.data / client.FOLDER

    assert script.exists()
    assert f"bind = ALT, X, exec, {script}" in (folder / hyprland.CONF_NAME).read_text()
    assert f'hl.bind("ALT + X", hl.dsp.exec_cmd("{script}"))' in (folder / hyprland.LUA_NAME).read_text()
    assert any(f'hl.bind("ALT + X", hl.dsp.exec_cmd("{script}"))' in code for code in running.evals)
    assert state.snapshot()["companion"]["state"] == "running"


def test_the_hotkey_toggles_recording_through_the_socket(running):
    assert ask(running.socket, "toggle") == "recording"
    assert ask(running.socket, "status") == "recording"
    assert ask(running.socket, "toggle") == "idle"
    assert "Escuchando…" in running.said


def test_changing_the_hotkey_while_running_rebinds_it_live(running, monkeypatch):
    script = running.data / client.FOLDER / client.SCRIPT_NAME
    monkeypatch.setenv("VOICE_HOTKEY", "super, z")

    assert wait_for(lambda: any(f'hl.bind("SUPER + Z", hl.dsp.exec_cmd("{script}"))' in code for code in running.evals))
    assert any('hl.unbind("ALT + X")' in code for code in running.evals)
    assert "SUPER, Z" in (running.data / client.FOLDER / hyprland.CONF_NAME).read_text()
    assert 'hl.bind("SUPER + Z"' in (running.data / client.FOLDER / hyprland.LUA_NAME).read_text()


def test_an_invalid_hotkey_is_said_once_and_keeps_the_old_one(running, monkeypatch):
    before = len(running.evals)
    monkeypatch.setenv("VOICE_HOTKEY", "X")

    assert wait_for(lambda: any("atajo" in text.lower() for text in running.said))
    time.sleep(0.2)
    assert len([t for t in running.said if "atajo" in t.lower()]) == 1  # no repite el aviso en cada vuelta
    assert len(running.evals) == before
    assert "ALT, X" in (running.data / client.FOLDER / hyprland.CONF_NAME).read_text()


def test_stopping_closes_and_removes_the_socket(running):
    voice.stop()
    running.thread.join(5)

    assert not running.thread.is_alive()
    assert not running.socket.exists()
    assert running.errors == []


def test_stopping_while_recording_throws_the_recording_away(running, mic):
    ask(running.socket, "toggle")

    voice.stop()
    running.thread.join(5)

    assert mic.cancelled == 1


def test_without_hyprland_the_channel_still_works_and_says_why(tmp_path, monkeypatch, mic):
    from epsilon.data import notes

    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(voice, "POLL_SECONDS", 0.02)
    said = []
    monkeypatch.setattr(voice.notify, "desktop", lambda text: said.append(text) or "")

    def no_hyprland(*args):
        raise hyprland.HyprlandError("Falta el programa «hyprctl»: esto solo funciona con Hyprland.")

    monkeypatch.setattr(hyprland, "_ctl", no_hyprland)
    voice.reset()
    thread = threading.Thread(target=voice.run, daemon=True)
    thread.start()
    try:
        assert wait_for(client.socket_path().exists)
        assert ask(client.socket_path(), "status") == "idle"
        assert any("hyprctl" in text for text in said)
    finally:
        voice.stop()
        thread.join(5)


def test_the_channel_is_always_configured_and_has_a_stop():
    assert voice.is_configured({}) is True
    assert callable(voice.stop) and callable(voice.reset)


# --- Qué se hace con cada audio (paso 2: transcribir) -------------------------------------------------------------


def make_clip(tmp_path, seconds=2.0):
    path = tmp_path / "clip-test.wav"
    path.write_bytes(b"audio")
    return capture.Clip(path, seconds)


@pytest.fixture
def listener(tmp_path, monkeypatch):
    """Un Listener síncrono (sin hilo) con el transcriptor y el detector de silencio reemplazados."""
    said = []
    instance = voice.Listener(said.append, start=lambda work: work())
    instance.said = said
    instance.heard = []
    instance.text = "hola mundo"
    instance.silent = False

    def fake_text(path):
        instance.heard.append(path)
        if isinstance(instance.text, Exception):
            raise instance.text
        return instance.text

    monkeypatch.setattr(voice.transcribe, "audio_to_text", fake_text)
    monkeypatch.setattr(voice.capture, "is_silent", lambda path: instance.silent)
    return instance


def test_a_clip_is_transcribed_and_the_text_is_shown(listener, tmp_path):
    clip = make_clip(tmp_path)

    listener.handle(clip)

    assert listener.heard == [clip.path]
    assert listener.said == ["Transcribiendo…", "Dijiste: hola mundo"]


def test_the_audio_is_deleted_as_soon_as_it_is_transcribed(listener, tmp_path):
    clip = make_clip(tmp_path)

    listener.handle(clip)

    assert not clip.path.exists()


def test_a_silent_clip_never_reaches_the_transcriber(listener, tmp_path):
    listener.silent = True
    clip = make_clip(tmp_path)

    listener.handle(clip)

    assert listener.heard == []
    assert listener.said == ["No te escuché: probá de nuevo."]
    assert not clip.path.exists()


def test_a_transcriber_message_is_said_as_it_is_and_the_audio_still_goes(listener, tmp_path):
    from epsilon.voice import transcribe

    listener.text = transcribe.VoiceNotInstalled("Falta instalar la voz: uv sync --extra voz")
    clip = make_clip(tmp_path)

    listener.handle(clip)

    assert listener.said[-1] == "Falta instalar la voz: uv sync --extra voz"
    assert not clip.path.exists()


def test_an_unexpected_failure_is_a_plain_message_without_details_and_the_audio_still_goes(listener, tmp_path):
    listener.text = RuntimeError("secreto interno")
    clip = make_clip(tmp_path)

    listener.handle(clip)

    assert listener.said[-1] == "Algo falló al procesar el audio."
    assert "secreto" not in " ".join(listener.said)
    assert not clip.path.exists()


def test_handle_returns_at_once_and_does_the_work_in_the_background(tmp_path, monkeypatch):
    # El socket no puede quedarse esperando a Whisper: el hilo que atiende el atajo tiene que quedar libre.
    queued = []
    instance = voice.Listener(lambda text: None, start=queued.append)
    monkeypatch.setattr(voice.transcribe, "audio_to_text", lambda path: pytest.fail("no debía correr todavía"))
    monkeypatch.setattr(voice.capture, "is_silent", lambda path: False)

    instance.handle(make_clip(tmp_path))

    assert len(queued) == 1


def test_the_default_start_really_uses_a_daemon_thread(tmp_path, monkeypatch):
    done = threading.Event()
    instance = voice.Listener(lambda text: None)
    monkeypatch.setattr(voice.capture, "is_silent", lambda path: True)
    monkeypatch.setattr(voice.capture, "discard", lambda path: done.set())

    instance.handle(make_clip(tmp_path))

    assert done.wait(3)


def test_the_whole_channel_transcribes_what_you_recorded(running, mic):
    assert ask(running.socket, "toggle") == "recording"
    assert ask(running.socket, "toggle") == "idle"

    assert wait_for(lambda: "Dijiste: hola mundo" in running.said)
    assert len(running.heard) == 1
    assert "Transcribiendo…" in running.said


def test_starting_the_channel_cleans_audio_left_by_a_crash(running):
    assert running.swept == [running.data / client.FOLDER]
