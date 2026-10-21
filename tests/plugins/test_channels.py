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
