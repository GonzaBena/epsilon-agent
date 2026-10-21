import shutil
import sys
import threading

import pytest

from epsilon import main, state
from epsilon.data import notes
from tests.plugins.test_channels import GOOD, make_plugin

TOML = GOOD.replace("discord", "fake").replace("DISCORD", "FAKE").replace("Discord", "Fake")

# Un canal de verdad: corre hasta que le piden parar, como el bot de Telegram.
CODE = """
import threading
_stop = threading.Event()
THREAD = []

def is_configured(env):
    return bool(env.get("FAKE_TOKEN"))

def run():
    THREAD.append(threading.current_thread())
    _stop.wait(timeout=5)

def stop():
    _stop.set()

def reset():
    _stop.clear()
"""


class FakeWeb:
    def __init__(self):
        self.threads = []

    def is_configured(self, env):
        return True

    def run(self):
        self.threads.append(threading.current_thread())


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    """DATA_DIR temporal con su carpeta de plugins y el estado de main limpio."""
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "CHANNELS", {"web": "epsilon.channels.web", "telegram": "epsilon.channels.telegram"})
    monkeypatch.setattr(main, "_plugins", {})
    monkeypatch.setattr(main, "_modules", {})
    monkeypatch.setattr(main, "INVALID", {})
    monkeypatch.setattr(main, "_threads", {})
    (tmp_path / "plugins").mkdir()
    yield tmp_path / "plugins"
    sys.modules.pop("epsilon_plugin_fake", None)


def fake_web_and_real_plugins(monkeypatch):
    """La web y Telegram se reemplazan por mentiras; los plugins se cargan de verdad."""
    web = FakeWeb()
    real = main.load
    monkeypatch.setattr(main, "CHANNELS", {"web": "x"})
    monkeypatch.setattr(main, "load", lambda name: web if name == "web" else real(name))
    return web


def test_refresh_adds_plugins_after_builtins_and_marks_them_off(plugins_dir):
    make_plugin(plugins_dir, "fake", TOML, CODE)

    main.refresh_plugins()

    assert list(main.CHANNELS) == ["web", "telegram", "fake"]
    assert main.is_plugin("fake") and not main.is_plugin("web")
    assert state.snapshot()["fake"]["state"] == "off"
    assert main.switchable("fake") is True


def test_refresh_does_not_import_plugin_code(plugins_dir):
    make_plugin(plugins_dir, "fake", TOML, CODE)

    main.refresh_plugins()
    main.enabled_channels({"FAKE_TOKEN": "x"})

    assert "epsilon_plugin_fake" not in sys.modules


def test_refresh_lists_invalid_plugins_with_the_reason(plugins_dir):
    make_plugin(plugins_dir, "fake", "name = ", CODE)

    main.refresh_plugins()

    assert "fake" in main.INVALID and "fake" not in main.CHANNELS


def test_a_disabled_plugin_is_not_loaded_at_startup(plugins_dir, monkeypatch):
    make_plugin(plugins_dir, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_TOKEN", "x")
    web = fake_web_and_real_plugins(monkeypatch)

    main.main()

    assert web.threads == [threading.main_thread()]
    assert "epsilon_plugin_fake" not in sys.modules
    assert state.snapshot()["fake"]["state"] == "off"


def test_an_enabled_plugin_starts_in_its_own_thread_and_stops(plugins_dir, monkeypatch):
    make_plugin(plugins_dir, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_TOKEN", "x")
    monkeypatch.setenv("FAKE_ENABLED", "si")
    fake_web_and_real_plugins(monkeypatch)

    main.main()

    module = main.load("fake")
    for _ in range(100):
        if module.THREAD:
            break
        threading.Event().wait(0.02)
    assert module.THREAD and module.THREAD[0] is not threading.main_thread()
    assert state.snapshot()["fake"]["state"] in ("starting", "running")
    main.stop_channel("fake")
    assert not module.THREAD[0].is_alive()
    assert state.snapshot()["fake"]["state"] == "off"


def test_an_enabled_but_unconfigured_plugin_is_skipped(plugins_dir, monkeypatch):
    make_plugin(plugins_dir, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_ENABLED", "si")
    monkeypatch.delenv("FAKE_TOKEN", raising=False)
    fake_web_and_real_plugins(monkeypatch)

    main.main()

    assert state.snapshot()["fake"]["state"] == "skipped"


def test_a_plugin_that_fails_to_import_is_stopped_but_the_web_runs(plugins_dir, monkeypatch):
    make_plugin(plugins_dir, "fake", TOML, 'raise RuntimeError("boom")\n')
    monkeypatch.setenv("FAKE_ENABLED", "si")
    web = fake_web_and_real_plugins(monkeypatch)

    main.main()

    assert web.threads == [threading.main_thread()]
    assert state.snapshot()["fake"]["state"] == "stopped"
    assert "RuntimeError" in state.snapshot()["fake"]["detail"]
    with pytest.raises(main.ChannelError, match="RuntimeError"):
        main.start_channel("fake")
    assert state.snapshot()["fake"]["state"] == "stopped"


def test_a_plugin_removed_from_disk_leaves_the_channels(plugins_dir):
    folder = make_plugin(plugins_dir, "fake", TOML, CODE)
    main.refresh_plugins()
    assert "fake" in main.CHANNELS

    shutil.rmtree(folder)
    main.refresh_plugins()

    assert "fake" not in main.CHANNELS


def test_a_running_plugin_removed_from_disk_stays_until_stopped(plugins_dir, monkeypatch):
    folder = make_plugin(plugins_dir, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_TOKEN", "x")
    main.refresh_plugins()
    main.start_channel("fake")
    module = main.load("fake")
    for _ in range(100):
        if module.THREAD:
            break
        threading.Event().wait(0.02)

    shutil.rmtree(folder)
    main.refresh_plugins()
    assert "fake" in main.CHANNELS

    main.stop_channel("fake")
    main.refresh_plugins()
    assert "fake" not in main.CHANNELS
