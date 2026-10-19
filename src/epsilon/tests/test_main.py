import threading
from types import SimpleNamespace

from epsilon import main
from epsilon.channels import telegram, web

TELEGRAM_ENV = {"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "123"}


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


# --- Arranque: la web manda, el resto son hilos opcionales -------------------


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
