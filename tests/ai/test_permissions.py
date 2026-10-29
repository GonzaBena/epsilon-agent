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
        "volumen": "allow", "sonando": "allow", "musica": "allow", "ventanas": "ask", "workspace": "allow", "foco": "allow", "app_abrir": "ask", "ventana_cerrar": "ask", "captura": "ask", "musica_poner": "allow", "musica_buscar": "allow", "musica_playlists": "allow", "yt_historial": "ask",
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
    assert len(loaded) == 41
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

    assert len(ids) == 39
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


# --- Ámbitos: una lista cerrada de herramientas, sin «preguntar» (rutinas con IA) ----------------


@pytest.fixture
def scope():
    permissions._scopes.clear()
    yield "rutina:1:1"
    permissions._scopes.clear()


def test_scope_runs_only_listed_tools_and_never_asks(scope):
    permissions.set_scope(scope, {"musica_poner"})

    assert permissions.decide(scope, "musica_poner") == "run"
    assert permissions.decide(scope, "nota") == "block"  # `nota` pregunta globalmente, pero acá nadie puede contestar
    assert permissions.decide(scope, "ver") == "block"  # ni siquiera una lectura permitida globalmente
    assert permissions.decide(scope, "yt_historial") == "block"


def test_scope_does_not_leak_to_other_conversations(scope):
    permissions.set_scope(scope, {"musica_poner"})

    assert permissions.decide("otra", "ver") == "run"
    assert permissions.decide("otra", "nota") == "ask"
    assert permissions.in_scope(scope) and not permissions.in_scope("otra")


def test_offered_in_scope_is_the_allowed_tools_only(scope):
    permissions.set_scope(scope, {"musica_poner", "fecha"})

    assert sorted(tools.ai_id(t) for t in permissions.offered(scope)) == ["fecha", "musica_poner"]
    assert len(permissions.offered()) > 2  # sin ámbito, todas las no bloqueadas


def test_clear_scope_restores_the_global_rules(scope):
    permissions.set_scope(scope, {"musica_poner"})
    permissions.clear_scope(scope)

    assert permissions.decide(scope, "ver") == "run" and not permissions.in_scope(scope)
    permissions.clear_scope(scope)  # sin ámbito: no pasa nada


def test_a_tool_denied_globally_stays_blocked_in_scope(scope):
    states = permissions.load()
    states["musica_poner"] = "deny"
    permissions.save(states)
    permissions.set_scope(scope, {"musica_poner"})

    assert permissions.decide(scope, "musica_poner") == "block"
    assert permissions.offered(scope) == []


# --- Modo «YOLO seguro» (Voice Companion): lo que se puede hacer sin preguntar por voz ---------------------------------

VOICE = "20261029-120000-cccc"
OTHER = "20261029-120001-dddd"
# Leer y escribir sin pisar nada: corren solas por voz aunque en /permisos estén en «preguntar».
SAFE = ["nota", "nota_nombrada", "crear_archivo", "recordar", "ver", "buscar", "fecha", "clima", "volumen", "musica", "nav_abrir", "nav_clic", "nav_escribir", "sistema", "ventanas", "yt_historial", "nav_buscar", "workspace", "foco", "app_abrir"]
# Destructivas (borran o pisan algo que ya estaba) o que arman automatizaciones: nunca por voz.
NEVER = ["olvidar", "rutina_borrar", "nota_editar", "editar", "rutina_crear", "ventana_cerrar"]


def tool_of(tool_id):
    return next(tool for tool in tools.ai_tools() if tools.ai_id(tool) == tool_id)


def connector_tool(state="ask"):
    return tools.Tool(
        label="luces.apagar", writes=True, ai="write", ai_default=state,
        ai_name="mcp_luces__apagar", ai_group="luces", ai_action="apagar", ai_help="apaga las luces", ai_arg="la habitación",
    )


@pytest.fixture
def voice_mode():
    permissions.set_mode(VOICE, permissions.SAFE_AUTO)
    yield VOICE
    permissions.set_mode(VOICE, None)


@pytest.mark.parametrize("tool_id", SAFE)
def test_voice_mode_runs_reads_and_non_destructive_writes_without_asking(voice_mode, tool_id):
    assert permissions.decide(voice_mode, tool_id) == "run"


def test_without_the_mode_the_same_write_still_asks():
    assert permissions.decide(OTHER, "nota") == "ask"


@pytest.mark.parametrize("tool_id", NEVER)
def test_voice_mode_never_runs_destructive_tools_even_if_you_allowed_them_in_permissions(voice_mode, tool_id):
    states = permissions.load()
    states[tool_id] = "allow"
    permissions.save(states)

    assert permissions.decide(voice_mode, tool_id) == "block"
    assert tool_id not in {tools.ai_id(t) for t in permissions.offered(voice_mode)}


@pytest.mark.parametrize("tool_id", ["olvidar", "rutina_borrar", "nota_editar", "editar", "ventana_cerrar"])
def test_what_deletes_or_overwrites_is_marked_so(tool_id):
    tool = tool_of(tool_id)

    assert tool.ai == "delete" or tool.overwrites
    assert not permissions.auto_ok(tool)


def test_only_overwriting_writes_carry_the_mark():
    assert {tools.ai_id(t) for t in tools.ai_tools() if t.overwrites} == {"nota_editar", "editar"}


def test_voice_mode_respects_a_tool_you_blocked_in_permissions(voice_mode):
    states = permissions.load()
    states["nota"] = "deny"
    permissions.save(states)

    assert permissions.decide(voice_mode, "nota") == "block"
    assert "nota" not in {tools.ai_id(t) for t in permissions.offered(voice_mode)}


def test_voice_mode_offers_the_model_only_what_it_may_use(voice_mode):
    offered = {tools.ai_id(t) for t in permissions.offered(voice_mode)}

    assert set(SAFE) <= offered
    assert not (set(NEVER) & offered)
    assert "resumir" not in offered  # sigue bloqueada en /permisos


def test_a_connector_tool_is_never_run_by_voice_unless_you_allowed_it_yourself(monkeypatch, voice_mode):
    # Un conector puede hacer cualquier cosa (facturar, reembolsar): «escribir» no alcanza para que corra solo.
    tool = connector_tool("ask")
    monkeypatch.setattr(tools, "_providers", [lambda: ((tool,), {"luces": "las luces"})])

    assert not permissions.auto_ok(tool)
    assert permissions.decide(voice_mode, "mcp_luces__apagar") == "block"
    assert "mcp_luces__apagar" not in {tools.ai_id(t) for t in permissions.offered(voice_mode)}

    states = permissions.load()
    states["mcp_luces__apagar"] = "allow"
    permissions.save(states)

    assert permissions.decide(voice_mode, "mcp_luces__apagar") == "run"  # lo permitiste vos, a propósito


def test_the_mode_only_applies_to_the_conversation_it_was_set_for(voice_mode):
    assert permissions.unattended(voice_mode)
    assert not permissions.unattended(OTHER)
    assert permissions.decide(OTHER, "nota") == "ask"


def test_a_scope_is_also_unattended_and_the_mode_can_be_cleared():
    permissions.set_scope("rutina:1:1", {"ver"})
    try:
        assert permissions.unattended("rutina:1:1")
    finally:
        permissions.clear_scope("rutina:1:1")
    permissions.set_mode(VOICE, permissions.SAFE_AUTO)
    permissions.set_mode(VOICE, None)

    assert not permissions.unattended(VOICE)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        permissions.set_mode(VOICE, "todo-permitido")
