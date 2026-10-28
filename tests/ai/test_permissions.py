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
        "nota": "ask", "nota_nombrada": "ask", "nota_editar": "ask", "crear_archivo": "ask", "recordar": "ask", "editar": "ask",
        "olvidar": "deny", "resumir": "deny", "skill_usar": "allow",
        "nav_pestanas": "allow", "nav_leer": "allow", "nav_abrir": "allow", "nav_buscar": "ask", "nav_clic": "allow", "nav_escribir": "allow",
        "volumen": "allow", "sonando": "allow", "musica": "allow", "ventanas": "ask", "musica_poner": "allow",
        "rutina_crear": "ask", "rutina_ver": "allow", "rutina_borrar": "ask",
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
    assert len(loaded) == 33
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

    assert len(ids) == 31
    assert "olvidar" not in ids and "resumir" not in ids


# --- el navegador de la IA ----------------------------------------------------------------------

NAV_IDS = ["nav_pestanas", "nav_leer", "nav_abrir", "nav_clic", "nav_escribir"]


def test_browser_tools_are_allowed_by_default():
    states = permissions.load()

    assert all(states[tool_id] == "allow" for tool_id in NAV_IDS)


def test_turning_the_browser_off_hides_and_blocks_its_tools(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "no")

    offered = {tools.ai_id(t) for t in permissions.offered()}

    assert not offered & {*NAV_IDS, "nav_buscar"}
    assert permissions.decide("c1", "nav_leer") == "block"
    assert permissions.decide("c1", "fecha") == "run"  # lo demás no cambia


def test_with_the_browser_on_its_tools_are_offered(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "si")

    assert set(NAV_IDS) <= {tools.ai_id(t) for t in permissions.offered()}


def test_grant_is_tied_to_the_version_and_reset_drops_every_version():
    permissions.grant("c1", "nota", "H1")

    assert permissions.decide("c1", "nota", "H1") == "run"
    assert permissions.decide("c1", "nota", "H2") == "ask"  # la herramienta cambió: vuelve a preguntar
    assert permissions.decide("c1", "nota") == "ask"
    permissions.reset("nota")
    assert permissions.decide("c1", "nota", "H1") == "ask"


def test_is_saved():
    assert permissions.is_saved("nota") is False
    permissions.save(permissions.load())
    assert permissions.is_saved("nota") is True
    permissions.reset("nota")
    assert permissions.is_saved("nota") is False
