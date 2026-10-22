import json

import pytest

from epsilon import tools
from epsilon.ai import permissions
from epsilon.data import notes


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    # Los permisos van a una carpeta temporal y las concesiones de sesión arrancan vacías.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    permissions._granted.clear()
    yield
    permissions._granted.clear()


def write_file(content):
    (notes.DATA_DIR / permissions.FILE).write_text(content, encoding="utf-8")


def test_defaults_follow_the_kind_and_resumir_is_denied():
    assert permissions.load() == {
        "ver": "allow", "buscar": "allow", "archivos": "allow", "memoria": "allow", "fecha": "allow", "dolar": "allow", "clima": "allow", "enviar": "allow", "csv": "allow", "sistema": "ask",
        "nota": "ask", "nota_nombrada": "ask", "nota_editar": "ask", "recordar": "ask", "editar": "ask",
        "olvidar": "deny", "resumir": "deny",
    }


def test_save_then_load_round_trips_and_survives():
    states = permissions.load()
    states["ver"] = "deny"

    saved = permissions.save(states)

    assert saved["ver"] == "deny"
    assert permissions.load()["ver"] == "deny"
    assert (notes.DATA_DIR / permissions.FILE).exists()


def test_save_rejects_unknown_tool_missing_tool_and_bad_state():
    before = permissions.load()
    extra = {**before, "inventada": "allow"}
    missing = {k: v for k, v in before.items() if k != "ver"}
    bad = {**before, "ver": "siempre"}

    for states in (extra, missing, bad):
        with pytest.raises(ValueError):
            permissions.save(states)

    assert permissions.load() == before
    assert not (notes.DATA_DIR / permissions.FILE).exists()


@pytest.mark.parametrize("content", ["{no es json", json.dumps(["ver"]),
                                     json.dumps({"ver": "siempre", "nota": "allow", "xyz": "deny"})])
def test_broken_file_falls_back_to_defaults(content):
    write_file(content)

    loaded = permissions.load()

    assert loaded["ver"] == "allow"
    assert "xyz" not in loaded
    assert len(loaded) == 17
    if "nota" in content:
        assert loaded["nota"] == "allow"
    else:
        assert loaded["nota"] == "ask"


def test_decide():
    states = permissions.load()
    states.update(ver="allow", nota="ask", olvidar="deny")
    permissions.save(states)

    assert permissions.decide("c1", "ver") == "run"
    assert permissions.decide("c1", "nota") == "ask"
    assert permissions.decide("c1", "olvidar") == "block"
    assert permissions.decide("c1", "no_existe") == "block"


def test_grant_makes_ask_run_only_in_that_conversation():
    permissions.grant("c1", "nota")

    assert permissions.decide("c1", "nota") == "run"
    assert permissions.decide("c2", "nota") == "ask"


def test_grant_never_overrides_deny():
    states = permissions.load()
    states["nota"] = "deny"
    permissions.save(states)

    permissions.grant("c1", "nota")

    assert permissions.decide("c1", "nota") == "block"


def test_invalid_ai_default_falls_back_to_kind(monkeypatch):
    bad = tools.Tool(label="x", name="x", ai="write", ai_default="siempre")
    assert permissions.default_state(bad) == "ask"


def test_offered_excludes_denied():
    ids = [tools.ai_id(t) for t in permissions.offered()]

    assert len(ids) == 15
    assert "olvidar" not in ids and "resumir" not in ids
