import pytest

from epsilon.data import notes
from epsilon.plugins import channels

GOOD = """
name = "discord"
label = "Discord"
about = "El bot de Discord. Responde solo a tu usuario."

[[settings]]
key = "DISCORD_TOKEN"
label = "Token de Discord"
secret = true
pattern = "[\\\\w.\\\\-]{20,100}"
message = "El token no tiene un formato válido."
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    (tmp_path / "plugins").mkdir()
    return tmp_path / "plugins"


def make_plugin(root, name="discord", toml=GOOD, code="def is_configured(env): return True\n"):
    folder = root / name
    folder.mkdir()
    if toml is not None:
        (folder / "plugin.toml").write_text(toml, encoding="utf-8")
    (folder / "channel.py").write_text(code, encoding="utf-8")
    return folder


def invalid_reason(root, **kwargs):
    make_plugin(root, **kwargs)
    found = channels.discover()
    assert found.plugins == {}
    (reason,) = found.invalid.values()
    return reason


def test_valid_manifest_is_read(root):
    folder = make_plugin(root)
    plugin = channels.read_manifest(folder)
    assert plugin.name == "discord"
    assert plugin.label == "Discord"
    assert plugin.entry == folder / "channel.py"
    assert plugin.settings[0].key == "DISCORD_TOKEN"
    assert plugin.settings[0].secret is True


def test_no_plugins_folder_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    found = channels.discover()
    assert found.plugins == {} and found.invalid == {}


def test_discover_sorts_and_skips_hidden_and_files(root):
    make_plugin(root, "zeta", GOOD.replace("discord", "zeta").replace("DISCORD", "ZETA"))
    make_plugin(root, "alfa", GOOD.replace("discord", "alfa").replace("DISCORD", "ALFA"))
    (root / ".oculta").mkdir()
    (root / "_privada").mkdir()
    (root / "suelto.txt").write_text("x")
    found = channels.discover()
    assert list(found.plugins) == ["alfa", "zeta"]
    assert found.invalid == {}


def replace(old, new):
    return GOOD.replace(old, new)


BAD_MANIFESTS = {
    "broken toml": "name = ",
    "name differs from folder": replace('name = "discord"', 'name = "otro"'),
    "uppercase name": replace('name = "discord"', 'name = "Discord"'),
    "name is web": replace('name = "discord"', 'name = "web"'),
    "name is telegram": replace('name = "discord"', 'name = "telegram"'),
    "empty label": replace('label = "Discord"', 'label = ""'),
    "long label": replace('label = "Discord"', 'label = "' + "x" * 41 + '"'),
    "long about": replace('about = "El bot', 'about = "' + "x" * 201 + ' El bot'),
    "key without prefix": replace('key = "DISCORD_TOKEN"', 'key = "TOKEN"'),
    "reserved enabled key": replace('key = "DISCORD_TOKEN"', 'key = "DISCORD_ENABLED"'),
    "no regex compiles": replace('pattern = "[\\\\w.\\\\-]{20,100}"', 'pattern = "(("'),
    "entry escapes": 'entry = "../fuera.py"\n' + GOOD,
}


@pytest.mark.parametrize("toml", BAD_MANIFESTS.values(), ids=BAD_MANIFESTS.keys())
def test_bad_manifest_is_invalid_not_a_crash(root, toml):
    assert invalid_reason(root, toml=toml)


def test_missing_manifest_is_invalid(root):
    assert invalid_reason(root, toml=None)


def test_non_utf8_manifest_is_invalid(root):
    folder = make_plugin(root, toml=GOOD)
    (folder / "plugin.toml").write_bytes(b"name = \xff\xfe")
    found = channels.discover()
    assert "discord" in found.invalid and found.plugins == {}


def test_huge_manifest_is_invalid(root):
    folder = make_plugin(root, toml=GOOD)
    (folder / "plugin.toml").write_text(GOOD + "#" + "x" * 17_000)
    assert "discord" in channels.discover().invalid


def test_eleven_settings_are_too_many(root):
    extra = "".join(f'\n[[settings]]\nkey = "DISCORD_K{i}"\nlabel = "k"\n' for i in range(10))
    assert invalid_reason(root, toml=GOOD + extra)


def test_setting_key_of_another_epsilon_setting_is_invalid(root):
    # plugin «gemini» con la clave GEMINI_API_KEY, que ya es un ajuste de Epsilon
    toml = GOOD.replace('name = "discord"', 'name = "gemini"').replace("DISCORD_TOKEN", "GEMINI_API_KEY")
    assert invalid_reason(root, name="gemini", toml=toml)


def test_repeated_key_inside_a_plugin_is_invalid(root):
    again = '\n[[settings]]\nkey = "DISCORD_TOKEN"\nlabel = "otra"\n'
    assert invalid_reason(root, toml=GOOD + again)


def test_same_key_in_two_plugins_marks_the_second_invalid(root):
    make_plugin(root, "alfa", GOOD.replace('name = "discord"', 'name = "alfa"').replace("DISCORD_TOKEN", "ALFA_TOKEN"))
    second = GOOD.replace('name = "discord"', 'name = "beta"').replace("DISCORD_TOKEN", "ALFA_TOKEN")
    make_plugin(root, "beta", second)
    found = channels.discover()
    assert list(found.plugins) == ["alfa"]
    assert "beta" in found.invalid


def test_entry_symlink_to_outside_is_invalid(root, tmp_path):
    outside = tmp_path / "fuera.py"
    outside.write_text("def is_configured(env): return True\n")
    folder = make_plugin(root)
    (folder / "channel.py").unlink()
    (folder / "channel.py").symlink_to(outside)
    assert "discord" in channels.discover().invalid


def test_missing_entry_is_invalid(root):
    folder = make_plugin(root)
    (folder / "channel.py").unlink()
    assert "discord" in channels.discover().invalid


# --- Importar el código (solo al prender) ------------------------------------

CONTRACT = "def is_configured(env): return True\ndef run(): pass\ndef stop(): pass\n"


def load(root, code):
    folder = make_plugin(root, code=code)
    return channels.load_module(channels.read_manifest(folder))


def test_load_module_returns_a_module_with_the_contract(root):
    module = load(root, CONTRACT)
    assert module.is_configured({}) is True
    assert callable(module.run) and callable(module.stop)


def test_import_error_becomes_plugin_error_without_the_secret(root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "TOKENSECRETO12345")
    with pytest.raises(channels.PluginError) as caught:
        load(root, 'raise RuntimeError("boom TOKENSECRETO12345")\n')
    assert "RuntimeError" in str(caught.value)
    assert "TOKENSECRETO12345" not in str(caught.value)


def test_system_exit_at_import_becomes_plugin_error(root):
    with pytest.raises(channels.PluginError):
        load(root, 'raise SystemExit("me voy")\n')


def test_syntax_error_becomes_plugin_error(root):
    with pytest.raises(channels.PluginError):
        load(root, "def (:\n")


@pytest.mark.parametrize("missing", ["is_configured", "run", "stop"])
def test_missing_part_of_the_contract_is_a_plugin_error(root, missing):
    code = "\n".join(line for line in CONTRACT.splitlines() if missing not in line)
    with pytest.raises(channels.PluginError, match=missing):
        load(root, code)


def test_a_failed_import_leaves_nothing_in_sys_modules(root):
    import sys

    with pytest.raises(channels.PluginError):
        load(root, 'raise RuntimeError("x")\n')
    assert "epsilon_plugin_discord" not in sys.modules


def test_listing_never_imports_the_code(root):
    import sys

    marker = root / "marker.txt"
    code = f'open({str(marker)!r}, "w").write("corrió")\nraise RuntimeError("no debería correr")\n'
    make_plugin(root, code=code)
    found = channels.discover()
    assert "discord" in found.plugins
    assert not marker.exists()
    assert "epsilon_plugin_discord" not in sys.modules


# --- Hallazgos de la revisión final ------------------------------------------


def test_a_plugin_whose_enabled_key_is_an_epsilon_setting_is_invalid(root):
    # «voice» generaría VOICE_ENABLED, que ya es un ajuste de Epsilon
    toml = GOOD.replace('name = "discord"', 'name = "voice"').replace("DISCORD_", "VOICE_")
    assert invalid_reason(root, name="voice", toml=toml)


def test_a_declared_key_ending_in_enabled_is_invalid(root):
    # FOO_BAR_ENABLED sería el interruptor de un plugin llamado «foo_bar»
    assert invalid_reason(root, toml=GOOD.replace("DISCORD_TOKEN", "DISCORD_BAR_ENABLED"))


def test_a_fifo_as_manifest_is_invalid_and_does_not_hang(root):
    import os
    import threading

    folder = make_plugin(root)
    (folder / "plugin.toml").unlink()
    os.mkfifo(folder / "plugin.toml")
    result = {}
    worker = threading.Thread(target=lambda: result.update(found=channels.discover()), daemon=True)
    worker.start()
    worker.join(2)
    assert not worker.is_alive(), "discover() se quedó esperando el FIFO"
    assert "discord" in result["found"].invalid


def test_an_entry_inside_an_unreadable_folder_is_invalid_not_a_crash(root):
    folder = make_plugin(root, toml="entry = \"sub/x.py\"\n" + GOOD)
    sub = folder / "sub"
    sub.mkdir()
    (sub / "x.py").write_text("")
    sub.chmod(0)
    try:
        assert "discord" in channels.discover().invalid
    finally:
        sub.chmod(0o755)


def test_a_manifest_that_is_a_symlink_to_a_device_is_invalid(root):
    # Regresión del mismo arreglo que el FIFO: no es un archivo común, no se lee (y /dev/zero no se agota).
    folder = make_plugin(root)
    (folder / "plugin.toml").unlink()
    (folder / "plugin.toml").symlink_to("/dev/zero")
    assert "discord" in channels.discover().invalid
