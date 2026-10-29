import importlib.util

import pytest

from epsilon.voice import overlay


class FakeTimer:
    """Hace de threading.Timer: no espera; el test lo dispara a mano."""

    made = []

    def __init__(self, seconds, function):
        self.seconds, self.function = seconds, function
        self.started = self.cancelled = False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function()

    @classmethod
    def live(cls):
        return [timer for timer in cls.made if timer.started and not timer.cancelled]


class Rig:
    def __init__(self, monkeypatch):
        FakeTimer.made = []
        self.sent = []
        self.presenter = overlay.Presenter(self.sent.append, timer=FakeTimer, label_of=lambda name: f"<{name}>")

    def feed(self, *events, turn=None):
        for event in events:
            self.presenter.on_event({**event, **({"turn": turn} if turn is not None else {})})

    def last(self):
        return self.sent[-1]

    def shown(self):
        return [c for c in self.sent if c["op"] == "show"]


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def status(text):
    return {"type": "status", "text": text}


# --- Qué se muestra en cada momento ----------------------------------------------------------------------------------------


def test_listening_opens_the_window_with_just_a_title(rig):
    rig.feed(status("Escuchando…"))

    command = rig.last()
    assert command["op"] == "show" and command["kind"] == "listening"
    assert command["title"] == "Escuchando…" and command["heard"] == "" and command["text"] == ""


def test_what_you_said_stays_visible_while_the_ai_thinks(rig):
    rig.feed(status("Escuchando…"), status("Transcribiendo…"), status("Dijiste: ¿qué hora es?"), status("Pensando…"))

    command = rig.last()
    assert command["kind"] == "working" and command["title"] == "Pensando…" and command["heard"] == "¿qué hora es?"


def test_using_a_tool_names_it_and_finishing_it_changes_nothing(rig):
    rig.feed(status("Dijiste: abrí spotify"), status("Pensando…"))
    before = len(rig.sent)

    rig.feed({"type": "tool", "name": "app_abrir", "state": "running"})
    rig.feed({"type": "tool", "name": "app_abrir", "state": "done"})

    assert rig.last()["title"] == "Usando <app_abrir>…"
    assert len(rig.sent) == before + 1


def test_the_answer_is_shown_as_it_is_written(rig):
    rig.feed(status("Dijiste: hola"), status("Pensando…"))

    rig.feed({"type": "text", "text": "Son las"}, {"type": "text", "text": "Son las diez de la noche."})

    command = rig.last()
    assert command["kind"] == "talking" and command["title"] == "Respondiendo…"
    assert command["text"] == "Son las diez de la noche." and command["heard"] == "hola"


def test_the_final_reply_closes_the_card_as_done(rig):
    rig.feed(status("Dijiste: hola"), {"type": "reply", "text": "Son las diez de la noche."})

    command = rig.last()
    assert command["kind"] == "done" and command["title"] == "Listo" and command["text"] == "Son las diez de la noche."


def test_a_problem_shows_the_whole_message_as_an_alert(rig):
    rig.feed({"type": "say", "text": "No pude grabar: no hay micrófono. Conectá uno y fijalo con wpctl."})

    command = rig.last()
    assert command["kind"] == "alert" and command["text"] == "No pude grabar: no hay micrófono. Conectá uno y fijalo con wpctl."


def test_an_error_event_is_an_alert_too(rig):
    rig.feed({"type": "error", "text": "Algo falló al hablar con la IA."})

    assert rig.last()["kind"] == "alert"


def test_a_new_recording_wipes_what_was_there(rig):
    rig.feed(status("Dijiste: viejo"), {"type": "reply", "text": "respuesta vieja"})

    rig.feed(status("Escuchando…"))

    command = rig.last()
    assert command["heard"] == "" and command["text"] == "" and command["kind"] == "listening"


def test_the_same_picture_twice_is_sent_once(rig):
    rig.feed(status("Pensando…"), status("Pensando…"))

    assert len(rig.sent) == 1


def test_events_that_show_nothing_new_send_nothing(rig):
    rig.feed({"type": "model", "step": 1}, {"type": "turn", "state": "start", "turn": 1}, {"type": "speech", "state": "start"})

    assert rig.sent == []


def test_unknown_statuses_are_shown_as_a_line_of_information(rig):
    rig.feed(status("El atajo «X» no sirve: pruebe otro."))

    assert rig.last()["kind"] == "info" and rig.last()["title"].startswith("El atajo")


# --- Texto largo -----------------------------------------------------------------------------------------------------------


def test_a_long_answer_shows_its_end_as_it_grows(rig):
    text = " ".join(f"palabra{n}" for n in range(300))

    rig.feed({"type": "text", "text": text})

    shown = rig.last()["text"]
    assert shown.startswith("…") and shown.endswith("palabra299") and len(shown) <= overlay.MAX_TEXT + 1
    assert "palabra0 " not in shown


def test_the_tail_never_cuts_a_word_in_half():
    # Los últimos 12 caracteres serían «o cinco seis»: arrancan a mitad de «cuatro», así que ese pedazo se descarta.
    assert overlay.tail("uno dos tres cuatro cinco seis", 12) == "…cinco seis"


def test_short_text_is_untouched_but_whitespace_is_collapsed():
    assert overlay.tail("hola\n\n  mundo", 100) == "hola mundo"


def test_what_you_said_is_cut_short_too(rig):
    rig.feed(status("Dijiste: " + "x" * 1000))

    assert len(rig.last()["heard"]) <= overlay.MAX_HEARD + 1


# --- Esconderse -------------------------------------------------------------------------------------------------------------


def test_it_hides_a_few_seconds_after_the_answer_when_nothing_is_being_spoken(rig, monkeypatch):
    monkeypatch.setenv("VOICE_OVERLAY_HIDE_S", "9")
    rig.feed({"type": "reply", "text": "Listo."})

    (timer,) = FakeTimer.live()
    assert timer.seconds == 9

    timer.fire()

    assert rig.last() == {"op": "hide"}


def test_while_it_is_still_speaking_it_waits_for_the_end_of_the_speech(rig):
    rig.feed({"type": "speech", "state": "start"}, {"type": "reply", "text": "Una respuesta larga que todavía se está diciendo."})

    assert [t.seconds for t in FakeTimer.live()] == [overlay.SPEECH_CAP]  # solo la red de seguridad, no el esconderse normal
    rig.feed({"type": "speech", "state": "end"})

    live = FakeTimer.live()
    assert len(live) == 1 and live[0].seconds == 6  # recién ahora empieza la cuenta, y la red de seguridad se canceló


def test_a_speech_end_with_no_answer_yet_does_not_hide_a_card_that_is_still_open(rig):
    rig.feed({"type": "speech", "state": "start"}, {"type": "text", "text": "Hablo y hablo y hablo y hablo."})
    rig.feed({"type": "speech", "state": "end"})

    assert FakeTimer.live() == [] and all(command["op"] == "show" for command in rig.sent)


def test_a_new_recording_cancels_the_pending_hide(rig):
    rig.feed({"type": "reply", "text": "Listo."})
    (timer,) = FakeTimer.live()

    rig.feed(status("Escuchando…"))

    assert timer.cancelled
    timer.fire()
    assert rig.last()["op"] == "show"  # no se escondió encima de la nueva grabación


def test_an_alert_also_goes_away_on_its_own(rig):
    rig.feed({"type": "say", "text": "No te escuché: probá de nuevo."})

    (timer,) = FakeTimer.live()
    timer.fire()

    assert rig.last() == {"op": "hide"}


def test_a_turn_that_ends_without_an_answer_hides_too(rig):
    rig.feed(status("Escuchando…"), {"type": "turn", "state": "start", "turn": 1})
    rig.feed({"type": "turn", "state": "end"}, turn=1)

    assert len(FakeTimer.live()) == 1  # lo cortaron a mitad: no queda colgado en pantalla


def test_a_turn_that_ended_with_its_answer_does_not_schedule_a_second_hide(rig):
    rig.feed({"type": "turn", "state": "start", "turn": 1})
    rig.feed({"type": "reply", "text": "Listo."}, {"type": "turn", "state": "end"}, turn=1)

    assert len(FakeTimer.live()) == 1


# --- Turnos: lo de uno cortado no se mezcla con el nuevo ------------------------------------------------------------------


def test_late_events_of_an_interrupted_turn_are_ignored(rig):
    rig.feed({"type": "turn", "state": "start", "turn": 1})
    rig.feed(status("Escuchando…"))  # la persona le habló encima: arranca otra grabación
    before = len(rig.sent)

    rig.feed({"type": "text", "text": "respuesta vieja"}, {"type": "reply", "text": "respuesta vieja"}, turn=1)

    assert len(rig.sent) == before


def test_events_of_the_current_turn_are_shown(rig):
    rig.feed(status("Escuchando…"), {"type": "turn", "state": "start", "turn": 2})

    rig.feed({"type": "text", "text": "hola"}, turn=2)

    assert rig.last()["text"] == "hola"


# --- Dónde y cómo se dibuja (viene de los ajustes, en vivo) -----------------------------------------------------------------


def test_the_layout_comes_from_the_settings(rig, monkeypatch):
    monkeypatch.setenv("VOICE_OVERLAY_POSITION", "arriba-izquierda")
    monkeypatch.setenv("VOICE_OVERLAY_MARGIN", "40")
    monkeypatch.setenv("VOICE_OVERLAY_WIDTH", "600")
    monkeypatch.setenv("VOICE_OVERLAY_FONT", "18")

    rig.feed(status("Escuchando…"))

    assert rig.last()["layout"] == {"anchors": ["top", "left"], "margin": 40, "width": 600, "font": 18}


def test_the_default_corner_is_bottom_right(rig):
    rig.feed(status("Escuchando…"))

    assert rig.last()["layout"]["anchors"] == ["bottom", "right"]


def test_the_center_has_no_anchors(rig, monkeypatch):
    monkeypatch.setenv("VOICE_OVERLAY_POSITION", "centro")

    rig.feed(status("Escuchando…"))

    assert rig.last()["layout"]["anchors"] == []


def test_changing_the_position_applies_to_the_very_next_card(rig, monkeypatch):
    rig.feed(status("Escuchando…"))
    monkeypatch.setenv("VOICE_OVERLAY_POSITION", "abajo-centro")

    rig.feed(status("Transcribiendo…"))

    assert rig.last()["layout"]["anchors"] == ["bottom"]


def test_a_broken_position_falls_back_to_the_default_corner(rig, monkeypatch):
    monkeypatch.setenv("VOICE_OVERLAY_POSITION", "en-la-luna")

    rig.feed(status("Escuchando…"))

    assert rig.last()["layout"]["anchors"] == ["bottom", "right"]


def test_a_tool_label_loses_its_explanation_in_parentheses():
    assert overlay.tool_label("captura") == "Mirar la pantalla"
    assert overlay.tool_label("no_existe") == "no_existe"


# --- El proceso de la ventanita ----------------------------------------------------------------------------------------------

import json  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402


def wait_for(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakePipe:
    def __init__(self):
        self.data = b""
        self.closed = False
        self.fail = None

    def write(self, chunk):
        if self.fail:
            raise self.fail
        self.data += chunk

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def commands(self):
        return [json.loads(line) for line in self.data.decode().splitlines()]


class FakeProc:
    """Hace del proceso de la ventanita: lo que escribió Epsilon y lo que él contesta por stdout («ready», «error: …»)."""

    def __init__(self, argv, env):
        self.argv, self.env = argv, env
        self.stdin = FakePipe()
        self._out = queue.Queue()
        self.stdout = self
        self.returncode = None
        self.killed = False

    def say(self, line):
        self._out.put(line.encode() + b"\n")

    def exit(self, code=0):
        self.returncode = code
        self._out.put(b"")

    def readline(self):
        try:
            return self._out.get(timeout=3)
        except queue.Empty:  # un proceso de verdad que se cerró: fin de archivo
            return b""

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode if self.returncode is not None else 0

    def kill(self):
        self.killed = True
        self.exit(-9)


class WindowRig:
    def __init__(self, monkeypatch, tmp_path):
        runtime = tmp_path / "run"
        runtime.mkdir(exist_ok=True)
        (runtime / "wayland-1").write_text("")
        lib = tmp_path / "lib"
        lib.mkdir(exist_ok=True)
        (lib / "libgtk4-layer-shell.so").write_text("")
        py = tmp_path / "python3"
        py.write_text("")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyD-ejemplo_de_clave_larga_QzXw")
        monkeypatch.setattr(overlay, "LIB_DIRS", [str(lib)])
        monkeypatch.setattr(overlay, "PYTHONS", [str(py)])
        self.spawned = []
        self.announced = []
        self.now = 1000.0
        self.lib, self.py = lib, py

        def spawn(argv, env):
            proc = FakeProc(argv, env)
            self.spawned.append(proc)
            return proc

        self.window = overlay.Window(self.announced.append, spawn=spawn, clock=lambda: self.now, defer=lambda work: work())

    @property
    def proc(self):
        return self.spawned[-1]


@pytest.fixture
def win(monkeypatch, tmp_path):
    rig = WindowRig(monkeypatch, tmp_path)
    yield rig
    rig.window.close()


SHOW = {"op": "show", "kind": "listening", "title": "Escuchando…", "heard": "", "text": "", "layout": {"anchors": ["bottom", "right"], "margin": 24, "width": 460, "font": 15}}


def test_the_first_card_starts_the_window_and_is_written_as_a_json_line(win):
    assert win.window.send(SHOW) is True

    assert len(win.spawned) == 1
    assert win.proc.stdin.commands() == [SHOW]


def test_later_cards_reuse_the_same_window(win):
    win.window.send(SHOW)
    win.window.send({**SHOW, "title": "Pensando…"})

    assert len(win.spawned) == 1 and len(win.proc.stdin.commands()) == 2


def test_hiding_something_that_was_never_shown_starts_nothing(win):
    assert win.window.send({"op": "hide"}) is False

    assert win.spawned == []


def test_the_window_runs_with_the_system_python_the_layer_shell_preloaded_and_a_minimal_environment(win):
    win.window.send(SHOW)

    argv, env = win.proc.argv, win.proc.env
    # -I: modo aislado. Sin él, la carpeta del script (con módulos llamados events, client, capture…) entraría al sys.path.
    assert argv[0] == str(win.py) and argv[1] == "-I" and argv[2].endswith("overlay_window.py")
    assert env["LD_PRELOAD"] == str(win.lib / "libgtk4-layer-shell.so")  # gtk4-layer-shell lo exige cargado antes que libwayland
    assert env["WAYLAND_DISPLAY"] == "wayland-1" and env["GDK_BACKEND"] == "wayland"
    assert "GEMINI_API_KEY" not in env and env["XDG_RUNTIME_DIR"]


def test_it_is_active_while_the_window_is_expected_to_work_even_before_it_starts(win):
    # Así la primera notificación de una charla no sale: la ventanita se encarga desde el primer evento.
    assert win.window.active() is True

    win.window.send(SHOW)

    assert win.window.active() is True


def test_it_is_not_active_when_turned_off_in_the_settings(win, monkeypatch):
    monkeypatch.setenv("VOICE_OVERLAY", "no")

    assert win.window.active() is False
    assert win.window.send(SHOW) is False and win.spawned == []


def test_turning_it_off_while_running_closes_the_window(win, monkeypatch):
    win.window.send(SHOW)
    monkeypatch.setenv("VOICE_OVERLAY", "no")

    win.window.send({**SHOW, "title": "Pensando…"})

    assert win.proc.stdin.commands()[-1] == {"op": "quit"} and win.proc.stdin.closed


def test_ready_is_noted_without_announcing_anything(win):
    win.window.send(SHOW)

    win.proc.say("ready")

    assert wait_for(lambda: win.window.ready) and win.announced == []


@pytest.mark.parametrize("missing", ["lib", "python", "display"])
def test_without_what_it_needs_it_says_what_to_install_once_and_stays_with_notifications(win, monkeypatch, missing):
    if missing == "lib":
        (win.lib / "libgtk4-layer-shell.so").unlink()
    elif missing == "python":
        win.py.unlink()
    else:
        (win.lib.parent / "run" / "wayland-1").unlink()

    assert win.window.send(SHOW) is False
    assert win.window.send(SHOW) is False

    assert win.spawned == [] and win.window.active() is False
    assert len(win.announced) == 1 and "ventanita" in win.announced[0]
    if missing == "lib":
        assert "gtk4-layer-shell" in win.announced[0]


def test_a_window_that_reports_an_error_falls_back_to_notifications_and_says_why(win):
    win.window.send(SHOW)

    win.proc.say("error: el compositor no soporta layer-shell")
    win.proc.exit(2)

    assert wait_for(lambda: not win.window.active())
    assert len(win.announced) == 1 and "layer-shell" in win.announced[0] and "avisos" in win.announced[0]


def test_a_window_that_dies_on_its_own_is_noticed_on_the_next_card(win):
    win.window.send(SHOW)
    win.proc.exit(1)
    assert wait_for(lambda: not win.window.active())  # el lector vio el cierre

    assert len(win.announced) == 1


def test_a_broken_pipe_is_a_failure_not_a_crash(win):
    win.window.send(SHOW)
    win.proc.stdin.fail = BrokenPipeError()

    assert win.window.send({**SHOW, "title": "x"}) is False
    assert win.window.active() is False


def test_it_tries_again_after_a_while_but_not_right_away(win):
    win.window.send(SHOW)
    win.proc.say("error: algo")
    win.proc.exit(2)
    assert wait_for(lambda: not win.window.active())

    assert win.window.send(SHOW) is False and len(win.spawned) == 1  # recién falló: no insiste

    win.now += overlay.RETRY_AFTER + 1
    assert win.window.active() is True
    assert win.window.send(SHOW) is True and len(win.spawned) == 2
    assert len(win.announced) == 1  # el motivo se dijo una vez, no por cada intento


def test_after_too_many_failures_it_gives_up_for_good_and_says_so(win):
    for attempt in range(overlay.MAX_FAILURES):
        win.now += overlay.RETRY_AFTER + 1
        win.window.send(SHOW)
        win.proc.say("error: otra vez")
        win.proc.exit(2)
        assert wait_for(lambda: win.window._proc is None or win.window._proc.poll() is not None)
        wait_for(lambda: not win.window.active() or attempt == overlay.MAX_FAILURES)

    win.now += 10 * overlay.RETRY_AFTER

    assert win.window.active() is False and win.window.send(SHOW) is False
    assert any("varias veces" in text for text in win.announced)


def test_closing_asks_the_window_to_quit_and_closes_the_pipe(win):
    win.window.send(SHOW)

    win.window.close()

    assert win.proc.stdin.commands()[-1] == {"op": "quit"} and win.proc.stdin.closed


def test_closing_a_window_that_never_started_or_twice_does_nothing(win):
    win.window.close()
    win.window.send(SHOW)
    win.window.close()
    win.window.close()

    assert len(win.spawned) == 1


def test_a_window_that_does_not_quit_is_killed(win):
    win.window.send(SHOW)
    win.proc.wait = lambda timeout=None: (_ for _ in ()).throw(overlay.subprocess.TimeoutExpired("x", timeout))

    win.window.close()

    assert win.proc.killed


def test_closing_the_presenter_cancels_a_pending_hide(rig):
    rig.feed({"type": "reply", "text": "Listo."})
    (timer,) = FakeTimer.live()

    rig.presenter.close()

    assert timer.cancelled


def test_the_real_timer_never_keeps_epsilon_alive_at_exit():
    timer = overlay._daemon_timer(100, lambda: None)

    assert timer.daemon is True
    assert hasattr(timer, "start") and hasattr(timer, "cancel")


def test_a_failure_is_announced_from_another_thread_so_it_cannot_deadlock_the_caller(monkeypatch, tmp_path):
    # Quien manda una tarjeta puede estar dentro de su propio candado (el Presenter); el aviso del fallo vuelve a pasar por él
    # (por el bus). Si se hiciera en el mismo hilo, esperaría para siempre a un candado que él mismo tiene tomado.
    rig = WindowRig(monkeypatch, tmp_path)
    seen = []
    window = overlay.Window(lambda text: seen.append((text, threading.get_ident())), spawn=lambda argv, env: FakeProc(argv, env))
    (tmp_path / "run" / "wayland-1").unlink()  # falta la sesión gráfica: va a avisar

    window.send(SHOW)

    assert wait_for(lambda: seen)
    assert seen[0][1] != threading.get_ident()
    rig.window.close()


def test_the_presenter_and_the_window_do_not_deadlock_when_the_window_cannot_open(monkeypatch, tmp_path):
    rig = WindowRig(monkeypatch, tmp_path)
    (tmp_path / "run" / "wayland-1").unlink()
    presenter_box = {}

    def announce(text):  # lo mismo que hace el canal: el aviso entra al bus y llega al Presenter
        presenter_box["presenter"].on_event({"type": "status", "text": text})

    window = overlay.Window(announce)
    presenter = overlay.Presenter(window.send)
    presenter_box["presenter"] = presenter
    done = threading.Event()

    def feed():
        presenter.on_event({"type": "status", "text": "Escuchando…"})
        done.set()

    threading.Thread(target=feed, daemon=True).start()

    assert done.wait(3), "se trabó: el Presenter esperaba a su propio candado"
    presenter.close()


def test_a_text_turn_starts_with_a_clean_card(rig):
    rig.presenter.on_event({"type": "turn", "state": "start", "turn": 1})
    rig.feed(status("Dijiste: algo viejo"), {"type": "reply", "text": "Respuesta vieja"}, turn=1)
    assert rig.last()["text"] == "Respuesta vieja"
    rig.presenter.on_event({"type": "turn", "state": "start", "turn": 2, "source": "text"})

    rig.feed(status("Pensando…"), turn=2)

    assert rig.last()["heard"] == "" and rig.last()["text"] == ""
    assert rig.last()["title"] == "Pensando…"


def test_a_text_turn_does_not_echo_what_you_typed(rig):
    rig.presenter.on_event({"type": "turn", "state": "start", "turn": 1, "source": "text"})

    rig.feed(status("Dijiste: recordame esto"), turn=1)

    assert rig.last()["heard"] == "" and rig.last()["title"] == "Pensando…"


def test_a_text_turn_is_not_held_open_by_the_speech_of_an_old_turn(rig):
    rig.feed({"type": "speech", "state": "start"})
    rig.presenter.on_event({"type": "turn", "state": "start", "turn": 1, "source": "text"})

    rig.feed({"type": "reply", "text": "Listo"}, turn=1)

    assert FakeTimer.live() == []  # ni espera a un audio que no va a haber ni se esconde sola


# --- Lo que la ventanita devuelve cuando se le escribe (submit / closed) ---------------------------------------------------


def wait_until(check, seconds=3.0):
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return check()


@pytest.fixture
def typed(win):
    got = {"submit": [], "closed": []}
    win.window.on_submit = got["submit"].append
    win.window.on_closed = lambda: got["closed"].append(1)
    win.window.send(SHOW)  # arranca el proceso
    return got


def test_a_submit_line_calls_on_submit_with_the_decoded_text(win, typed):
    win.proc.say('submit:"hola \\"mundo\\"\\nchau"')

    assert wait_until(lambda: typed["submit"] == ['hola "mundo"\nchau'])


def test_a_closed_line_calls_on_closed(win, typed):
    win.proc.say("closed")

    assert wait_until(lambda: typed["closed"] == [1])


@pytest.mark.parametrize("line", ["submit:", "submit:no-json", "submit:42", 'submit:""', "submit:" + '"' + "x" * 501 + '"', "submit:null"])
def test_a_malformed_or_oversized_submit_is_dropped(win, typed, line):
    win.proc.say(line)
    win.proc.say("closed")  # lo que viene después se sigue leyendo: es la señal de que la anterior ya se procesó

    assert wait_until(lambda: typed["closed"] == [1])
    assert typed["submit"] == []


def test_a_failing_callback_does_not_stop_the_reader(win):
    seen = []

    def boom(text):
        seen.append(text)
        raise RuntimeError("boom")

    win.window.on_submit = boom
    win.window.on_closed = lambda: seen.append("closed")
    win.window.send(SHOW)

    win.proc.say('submit:"uno"')
    win.proc.say("closed")

    assert wait_until(lambda: seen == ["uno", "closed"])


def test_the_window_and_epsilon_agree_on_the_longest_text(win):
    spec = importlib.util.spec_from_file_location("overlay_window_limit", overlay.SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert overlay.MAX_SUBMIT == module.MAX_INPUT


# --- Escribir (Alt+C): la tarjeta espera a que la leas, y no deja el «pensamiento» de antes de una herramienta ---------------

def text_turn(rig, turn=1):
    rig.feed({"type": "turn", "state": "start", "source": "text"}, turn=turn)
    rig.feed(status("Dijiste: anotá esto"), status("Pensando…"), turn=turn)


def test_a_typed_answer_stays_until_you_close_it(rig):
    text_turn(rig)
    rig.feed({"type": "reply", "text": "Anotado."}, {"type": "turn", "state": "end"}, turn=1)

    assert FakeTimer.live() == []
    assert rig.last()["kind"] == "done"


def test_a_typed_problem_stays_too(rig):
    text_turn(rig)
    rig.feed({"type": "error", "text": "Algo falló al hablar con la IA."}, turn=1)

    assert FakeTimer.live() == []


def test_a_spoken_answer_still_hides_after_a_typed_one(rig):
    text_turn(rig)
    rig.feed({"type": "reply", "text": "Anotado."}, turn=1)
    rig.feed(status("Escuchando…"))
    rig.feed({"type": "reply", "text": "Listo."})

    assert len(FakeTimer.live()) == 1


def test_the_text_written_before_a_tool_is_wiped_when_the_tool_starts(rig):
    rig.feed(status("Dijiste: hola"), status("Pensando…"))
    rig.feed({"type": "text", "text": "Voy a revisar la nota primero."})

    rig.feed({"type": "tool", "name": "ver", "state": "running"})

    assert rig.last()["title"] == "Usando <ver>…" and rig.last()["text"] == ""


def test_a_window_that_dies_counts_as_closed(win):
    closed = []
    win.window.on_closed = lambda: closed.append(True)
    win.window.send({"op": "show", "kind": "info", "title": "x", "heard": "", "text": "", "layout": {}})
    win.proc.exit()

    wait_for(lambda: closed)
    assert closed
