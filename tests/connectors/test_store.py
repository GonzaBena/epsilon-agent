import json

import pytest

from epsilon.connectors import store
from epsilon.data import notes


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())


def test_add_and_get_roundtrip():
    saved = store.add("luces", ["uvx", "mcp-luces"], {"TOKEN": "secreto"})

    assert saved == {"name": "luces", "argv": ["uvx", "mcp-luces"], "env": {"TOKEN": "secreto"},
                     "enabled": True, "autostart": False, "tools": [], "error": ""}
    assert store.get("luces") == saved
    assert store.list_all() == [saved]
    assert store.get("otro") is None


@pytest.mark.parametrize("name", ["Luces", "a", "x" * 21, "1luces", "mi-luz", "", "luces\n"])
def test_name_must_match_pattern(name):
    with pytest.raises(store.StoreError):
        store.add(name, ["x"], {})


@pytest.mark.parametrize("name", ["notas", "navegador", "fecha"])
def test_name_cannot_be_an_ai_group_or_tool(name):
    with pytest.raises(store.StoreError, match="reservado"):
        store.add(name, ["x"], {})


def test_duplicate_name_rejected():
    store.add("luces", ["x"], {})

    with pytest.raises(store.StoreError, match="Ya hay"):
        store.add("luces", ["y"], {})


def test_max_connectors():
    for i in range(store.MAX_CONNECTORS):
        store.add(f"con_{i}", ["x"], {})

    with pytest.raises(store.StoreError, match="borrá alguno"):
        store.add("uno_mas", ["x"], {})


@pytest.mark.parametrize("argv", [[], "uvx x", [""], ["a", 3], ["a\nb"], ["a", "  "]])
def test_argv_must_be_nonempty_single_line_strings(argv):
    with pytest.raises(store.StoreError):
        store.add("luces", argv, {})


@pytest.mark.parametrize("env", [{"mi-clave": "x"}, {"1A": "x"}, {"TOKEN": "a\nb"}, {"TOKEN": 5}, {"TOKEN": "a\rb"}, {"TOKEN\n": "x"}])
def test_env_keys_must_look_like_variables_and_values_single_line(env):
    with pytest.raises(store.StoreError):
        store.add("luces", ["x"], env)


def test_public_never_contains_env_values():
    entry = store.add("luces", ["x"], {"TOKEN": "secreto"})

    shown = store.public(entry)

    assert shown["env"] == {"TOKEN": {"set": True, "length": 7}}
    assert "secreto" not in json.dumps(shown)


def test_corrupt_file_reads_as_empty():
    (notes.DATA_DIR / store.FILE).write_text("{basura", encoding="utf-8")

    assert store.list_all() == []
    assert store.add("luces", ["x"], {})["name"] == "luces"


def test_update_rejects_unknown_fields():
    store.add("luces", ["x"], {})

    with pytest.raises(store.StoreError):
        store.update("luces", argv=["otro"])
    with pytest.raises(store.StoreError, match="No hay"):
        store.update("nada", enabled=False)
    assert store.update("luces", enabled=False, autostart=True)["enabled"] is False
    assert store.get("luces")["autostart"] is True


def test_update_validates_env():
    store.add("luces", ["x"], {})

    with pytest.raises(store.StoreError):
        store.update("luces", env={"mala-clave": "x"})
    assert store.update("luces", env={"TOKEN": "abc"})["env"] == {"TOKEN": "abc"}


def test_remove():
    store.add("luces", ["x"], {})

    store.remove("luces")

    assert store.list_all() == []
    with pytest.raises(store.StoreError, match="No hay"):
        store.remove("luces")


def test_name_cannot_contain_double_underscore(tmp_path, monkeypatch):
    from epsilon.connectors import store
    with pytest.raises(store.StoreError, match="guiones bajos"):
        store.add("a__b", ["x"], {})
