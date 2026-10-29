import threading
from types import SimpleNamespace

import pytest

from epsilon import main
from epsilon.data import notes
from epsilon.settings import config
from epsilon.channels import telegram, web

TELEGRAM_ENV = {"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "123"}


@pytest.fixture(autouse=True)
def isolated_plugins(tmp_path, monkeypatch):
    """main() busca plugins en DATA_DIR: que sea una carpeta vacía y no la real."""
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)


class FakeChannel:
    """Un canal de mentira que anota en qué hilo corrió."""

    def __init__(self, configured=True, error=None):
        self.configured = configured
        self.error = error
        self.threads = []
        self.started = threading.Event()

    def is_configured(self, env):
        return self.configured

    def run(self):
        self.threads.append(threading.current_thread())
        self.started.set()
        if self.error:
            raise self.error


def use_channels(monkeypatch, **channels):
    monkeypatch.setattr(main, "CHANNELS", {name: "x" for name in channels})
    monkeypatch.setattr(main, "load", lambda name: channels[name])


def wait_for(channel):
    assert channel.started.wait(timeout=2), "el canal no llegó a arrancar"


# --- Qué canales están configurados ------------------------------------------


def test_web_is_always_configured():
    assert web.is_configured({}) is True


def test_telegram_is_configured_with_token_and_user():
    assert telegram.is_configured(TELEGRAM_ENV) is True


def test_telegram_is_not_configured_without_them():
    assert telegram.is_configured({}) is False
    assert telegram.is_configured({"TELEGRAM_TOKEN": "abc"}) is False


def test_web_comes_first_and_is_always_enabled():
    assert main.CHANNELS and next(iter(main.CHANNELS)) == "web"
    assert main.enabled_channels({}) == ["web"]
    assert main.enabled_channels(TELEGRAM_ENV) == ["web", "telegram"]


def test_the_voice_companion_is_a_switchable_channel_that_starts_off(monkeypatch):
    assert main.CHANNELS["companion"] == "epsilon.channels.voice"
    assert main.switchable("companion")
    assert main.enabled_channels({}) == ["web"]  # apagado por defecto: nadie graba el micrófono sin pedirlo

    monkeypatch.setenv("COMPANION_ENABLED", "si")

    assert main.enabled_channels({}) == ["web", "companion"]


# --- Arranque: la web manda, el resto son hilos opcionales -------------------


def test_main_turns_on_the_routines_engine_and_survives_if_it_fails(monkeypatch):
    web_channel, started = FakeChannel(), []
    use_channels(monkeypatch, web=web_channel)
    monkeypatch.setattr(main.engine, "start", lambda: started.append(True))

    main.main()
    assert started == [True] and web_channel.threads

    def broken():
        raise RuntimeError("sin hilos")

    monkeypatch.setattr(main.engine, "start", broken)
    web_two = FakeChannel()
    use_channels(monkeypatch, web=web_two)

    main.main()  # la web arranca igual
    assert web_two.threads


def test_first_channel_runs_in_the_main_thread(monkeypatch):
    web_channel = FakeChannel()
    use_channels(monkeypatch, web=web_channel)

    main.main()

    assert web_channel.threads == [threading.main_thread()]


def test_other_channels_run_in_their_own_thread(monkeypatch):
    web_channel, other = FakeChannel(), FakeChannel()
    use_channels(monkeypatch, web=web_channel, other=other)

    main.main()
    wait_for(other)

    assert web_channel.threads == [threading.main_thread()]
    assert other.threads[0] is not threading.main_thread()


def test_a_failing_channel_does_not_stop_the_main_one(monkeypatch, capsys):
    web_channel = FakeChannel()
    broken = FakeChannel(error=RuntimeError("token inválido"))
    use_channels(monkeypatch, web=web_channel, telegram=broken)

    main.main()  # no tiene que lanzar nada
    wait_for(broken)
    broken.threads[0].join(timeout=2)

    assert web_channel.threads == [threading.main_thread()]
    out = capsys.readouterr().out
    assert "telegram" in out and "token inválido" in out


def test_a_channel_that_exits_with_systemexit_is_contained(monkeypatch, capsys):
    web_channel = FakeChannel()
    quits = FakeChannel(error=SystemExit("Falta TELEGRAM_TOKEN"))
    use_channels(monkeypatch, web=web_channel, telegram=quits)

    main.main()
    wait_for(quits)
    quits.threads[0].join(timeout=2)

    assert "Falta TELEGRAM_TOKEN" in capsys.readouterr().out


def test_unconfigured_channels_are_skipped_with_a_notice(monkeypatch, capsys):
    web_channel = FakeChannel()
    off = FakeChannel(configured=False)
    use_channels(monkeypatch, web=web_channel, apagado=off)

    main.main()

    assert off.threads == []
    assert "apagado" in capsys.readouterr().out


def test_threads_do_not_keep_the_process_alive(monkeypatch):
    # Si la web termina (por ejemplo, con la señal de systemd), el proceso
    # tiene que poder cerrarse aunque Telegram siga corriendo.
    web_channel = FakeChannel()
    forever = SimpleNamespace(
        is_configured=lambda env: True, run=lambda: threading.Event().wait()
    )
    use_channels(monkeypatch, web=web_channel, telegram=forever)

    main.main()

    threads = [t for t in threading.enumerate() if t is not threading.main_thread()]
    assert all(t.daemon for t in threads if t.name == "canal-telegram")


# --- Estado de los canales y errores claros ----------------------------------

import pytest

from epsilon import state

LEAKY_TOKEN = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


def test_threaded_channels_start_as_connecting(monkeypatch):
    seen = []

    class Spy(FakeChannel):
        def run(self):
            seen.append(state.snapshot()["telegram"]["state"])
            super().run()

    web_channel, spy = FakeChannel(), Spy()
    use_channels(monkeypatch, web=web_channel, telegram=spy)

    main.main()
    wait_for(spy)

    assert seen == ["starting"]


def test_unconfigured_channels_are_marked_as_skipped(monkeypatch):
    use_channels(monkeypatch, web=FakeChannel(), telegram=FakeChannel(configured=False))

    main.main()

    assert state.snapshot()["telegram"]["state"] == "skipped"


def finish(channel):
    wait_for(channel)
    channel.threads[0].join(timeout=2)


def test_a_failed_channel_is_marked_stopped_with_the_reason(monkeypatch, capsys):
    broken = FakeChannel(error=RuntimeError("token inválido"))
    use_channels(monkeypatch, web=FakeChannel(), telegram=broken)

    main.main()
    finish(broken)

    snap = state.snapshot()["telegram"]
    assert snap["state"] == "stopped" and "token inválido" in snap["detail"]
    assert "Canal telegram: detenido" in capsys.readouterr().out


def test_a_channel_can_explain_its_own_errors(monkeypatch):
    broken = FakeChannel(error=RuntimeError("detalle crudo"))
    broken.explain = lambda e: "Revisá TELEGRAM_TOKEN en la configuración."
    use_channels(monkeypatch, web=FakeChannel(), telegram=broken)

    main.main()
    finish(broken)

    detail = state.snapshot()["telegram"]["detail"]
    assert detail.startswith("Revisá TELEGRAM_TOKEN")
    assert "RuntimeError" in detail  # el tipo técnico queda al final, para investigar


def test_a_channel_that_cannot_explain_falls_back_to_the_error(monkeypatch):
    broken = FakeChannel(error=RuntimeError("algo raro"))
    broken.explain = lambda e: None
    use_channels(monkeypatch, web=FakeChannel(), telegram=broken)

    main.main()
    finish(broken)

    assert state.snapshot()["telegram"]["detail"] == "RuntimeError: algo raro"


def test_systemexit_messages_are_kept_as_the_reason(monkeypatch):
    quits = FakeChannel(error=SystemExit("Falta TELEGRAM_TOKEN en el .env"))
    use_channels(monkeypatch, web=FakeChannel(), telegram=quits)

    main.main()
    finish(quits)

    assert state.snapshot()["telegram"]["detail"] == "Falta TELEGRAM_TOKEN en el .env"


def test_secrets_never_reach_the_state_nor_the_console(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_TOKEN", LEAKY_TOKEN)
    broken = FakeChannel(error=RuntimeError(f"The token `{LEAKY_TOKEN}` was rejected"))
    use_channels(monkeypatch, web=FakeChannel(), telegram=broken)

    main.main()
    finish(broken)

    assert LEAKY_TOKEN not in str(state.snapshot())
    assert LEAKY_TOKEN not in capsys.readouterr().out


def test_the_main_channel_failing_stops_the_process_with_a_clear_reason(monkeypatch, capsys):
    web_channel = FakeChannel(error=RuntimeError("no pude abrir"))
    use_channels(monkeypatch, web=web_channel)

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 1
    assert state.snapshot()["web"]["state"] == "stopped"
    assert "no pude abrir" in capsys.readouterr().out


def test_systemexit_from_the_main_channel_keeps_its_own_message(monkeypatch, capsys):
    # Por ejemplo, «el puerto está ocupado»: ya viene con un mensaje claro.
    use_channels(monkeypatch, web=FakeChannel(error=SystemExit("El puerto 8765 está ocupado.")))

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 1  # el motivo se muestra una sola vez, en la consola
    assert "El puerto 8765 está ocupado." in capsys.readouterr().out


def test_logs_are_redacted_from_startup(monkeypatch):
    import logging

    monkeypatch.setattr(main, "CHANNELS", {"web": "x"})
    monkeypatch.setattr(main, "load", lambda name: FakeChannel())
    root = logging.getLogger()
    before = list(root.handlers)

    try:
        main.main()
        added = [h for h in root.handlers if h not in before]
        handlers = added or root.handlers
        assert any(
            isinstance(f, config.RedactingFilter) for h in handlers for f in h.filters
        )
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)


# --- Prender y apagar un canal sin reiniciar el servicio ---------------------


class Stoppable(FakeChannel):
    """Un canal que corre hasta que le piden parar, como el bot de Telegram."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stopped = threading.Event()
        self.runs = 0

    def run(self):
        self.runs += 1
        self.threads.append(threading.current_thread())
        self.started.set()
        self.stopped.clear()
        self.stopped.wait(timeout=5)

    def stop(self):
        self.stopped.set()


def running_telegram(monkeypatch, channel=None):
    channel = channel or Stoppable()
    use_channels(monkeypatch, web=FakeChannel(), telegram=channel)
    main.main()
    wait_for(channel)
    return channel


def test_a_switchable_channel_is_one_with_an_enabled_setting_and_a_stop():
    assert main.switchable("telegram") is True
    assert main.switchable("web") is False  # la web no se puede apagar desde la web


def test_stopping_a_channel_ends_its_thread_and_marks_it_off(monkeypatch):
    channel = running_telegram(monkeypatch)

    main.stop_channel("telegram")

    assert not channel.threads[0].is_alive()
    assert state.snapshot()["telegram"]["state"] == "off"  # y no «detenido» ni un error


def test_a_stopped_channel_can_be_started_again(monkeypatch):
    channel = running_telegram(monkeypatch)
    main.stop_channel("telegram")

    main.start_channel("telegram")
    deadline = threading.Event()
    for _ in range(100):
        if channel.runs == 2:
            break
        deadline.wait(0.02)

    assert channel.runs == 2 and channel.threads[1].is_alive()
    main.stop_channel("telegram")


def test_starting_a_channel_first_forgets_an_old_stop_request(monkeypatch):
    resets = []
    channel = Stoppable()
    channel.reset = lambda: resets.append(1)
    use_channels(monkeypatch, web=FakeChannel(), telegram=channel)

    main.start_channel("telegram")
    wait_for(channel)

    assert resets == [1]
    main.stop_channel("telegram")


def test_starting_twice_does_not_start_two_threads(monkeypatch):
    channel = running_telegram(monkeypatch)

    main.start_channel("telegram")

    assert channel.runs == 1
    main.stop_channel("telegram")


def test_stopping_something_that_is_not_running_just_marks_it_off(monkeypatch):
    use_channels(monkeypatch, web=FakeChannel(), telegram=Stoppable())

    main.stop_channel("telegram")

    assert state.snapshot()["telegram"]["state"] == "off"


def test_a_channel_that_does_not_stop_in_time_says_so(monkeypatch):
    class Stubborn(Stoppable):
        def stop(self):
            pass  # ignora el pedido

    channel = running_telegram(monkeypatch, Stubborn())

    with pytest.raises(main.ChannelError, match="a tiempo"):
        main.stop_channel("telegram", timeout=0.2)
    channel.stopped.set()


def test_a_channel_without_a_stop_cannot_be_switched_off(monkeypatch):
    use_channels(monkeypatch, web=FakeChannel(), telegram=FakeChannel())

    assert main.switchable("telegram") is False
    with pytest.raises(main.ChannelError):
        main.stop_channel("telegram")


def test_an_unconfigured_channel_cannot_be_started(monkeypatch):
    use_channels(monkeypatch, web=FakeChannel(), telegram=Stoppable(configured=False))

    with pytest.raises(main.ChannelError, match="configurarlo"):
        main.start_channel("telegram")


def test_a_channel_switched_off_in_the_settings_does_not_start_with_the_service(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "no")
    channel = Stoppable()
    use_channels(monkeypatch, web=FakeChannel(), telegram=channel)

    main.main()

    assert channel.threads == []
    assert state.snapshot()["telegram"]["state"] == "off"  # apagado por vos, no «sin configurar»
    assert main.enabled_channels(TELEGRAM_ENV) == ["web"]


def test_an_unconfigured_and_switched_off_channel_still_says_unconfigured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "no")
    use_channels(monkeypatch, web=FakeChannel(), telegram=Stoppable(configured=False))

    main.main()

    assert state.snapshot()["telegram"]["state"] == "skipped"


# --- Conectores MCP ------------------------------------------------------------


def test_autostart_connectors_start_with_epsilon_and_failures_do_not_stop_it(monkeypatch):
    from epsilon import tools
    from epsilon.connectors import manager, registry, store
    from tests.connectors.fakes import FakeLink, install

    good, broken, lazy = FakeLink(), FakeLink(open_error=OSError("no existe el programa")), FakeLink()
    install(monkeypatch, {"bueno": good, "roto": broken, "perezoso": lazy})
    for name in ("bueno", "roto", "perezoso"):
        store.add(name, [name], {})
    store.update("bueno", autostart=True)
    store.update("roto", autostart=True)
    stopped = []
    real_stop_all = manager.stop_all
    monkeypatch.setattr(manager, "stop_all", lambda: stopped.append(True) or real_stop_all())
    web_channel = FakeChannel()
    use_channels(monkeypatch, web=web_channel)

    main.main()  # la web arranca igual

    assert web_channel.threads
    thread = next(t for t in threading.enumerate() if t.name == "conectores-arranque")
    thread.join(timeout=5)
    assert good.opens and broken.opens and not lazy.opens  # sin «arrancar con Epsilon», espera al primer uso
    assert store.get("roto")["error"]  # quedó «detenido» con el motivo
    assert manager.status("roto") == "detenido"
    assert registry.provide in tools._providers  # sus herramientas llegan a la IA
    assert stopped  # al cerrar la web, se cierran los conectores
    manager._reset()


def test_main_survives_if_connectors_cannot_be_set_up(monkeypatch):
    def broken(*args):
        raise RuntimeError("sin conectores")

    monkeypatch.setattr(main.tools, "add_provider", broken)
    web_channel = FakeChannel()
    use_channels(monkeypatch, web=web_channel)

    main.main()

    assert web_channel.threads


def test_main_seeds_the_factory_skills_once(monkeypatch):
    from epsilon.skills import store

    use_channels(monkeypatch, web=FakeChannel())
    monkeypatch.setattr(main.engine, "start", lambda: None)

    main.main()
    assert sorted(s.name for s in store.list_all()) == ["correo", "informe", "prioridades"]

    store.delete("correo")
    use_channels(monkeypatch, web=FakeChannel())
    main.main()
    assert store.get("correo") is None  # una borrada no vuelve
