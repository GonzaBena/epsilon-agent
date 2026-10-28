import pytest

from epsilon import core
from epsilon.ai import chat, llm, permissions
from epsilon.data import notes
from tests.test_core_tools import results, script, step  # noqa: F401  (script es un fixture)

# Las rutinas con IA desde core: un turno sin nadie que conteste, con una lista cerrada de herramientas.

SCOPE = "rutina:7:1"


@pytest.fixture(autouse=True)
def clean_scopes():
    permissions._scopes.clear()
    yield
    permissions._scopes.clear()


def turn(text="Poné música"):
    return chat.Turn(SCOPE, [llm.text_turn("user", text)], label="Rutina IA", task="ask")


def test_ask_from_the_browser_guard_becomes_block_inside_a_scope(script):
    # Abrir un sitio que no es de confianza «pregunta»; con un ámbito no hay a quién preguntarle.
    permissions.set_scope(SCOPE, {"nav_abrir"})
    session = core.Session()
    script.steps = [step(calls=[("navegador.abrir", "https://example.com/")]), step("No pude")]

    reply = core._loop(turn(), [], session, False)

    assert reply.text == "No pude" and session.pending_tool is None
    assert "no puede preguntar" in results(script.seen[1][0])[0]


def test_a_tool_outside_the_scope_is_unavailable_even_if_the_model_names_it(script):
    permissions.set_scope(SCOPE, {"musica_poner"})
    script.steps = [step(calls=[("notas.guardar", "hola")]), step("Listo")]

    core._loop(turn(), [], core.Session(), False)

    assert results(script.seen[1][0]) == [core.UNAVAILABLE]
    assert not (notes.DATA_DIR / "inbox.md").exists()  # y no guardó nada


def test_the_loop_does_not_apply_chat_cost_limits_to_a_routine_scope(script):
    # El id de un ámbito no es una conversación guardada: no se le consulta límite (rompería con un id inválido).
    permissions.set_scope(SCOPE, {"fecha"})
    script.steps = [step(calls=[("consultar.fecha", "")]), step("Hoy es hoy")]

    reply = core._loop(turn(), [], core.Session(), False)

    assert reply.text == "Hoy es hoy"


def test_run_records_each_call_in_the_trace(script, monkeypatch):
    monkeypatch.setattr(core.navegador, "play_music", lambda kind, value, shuffle: "Sonando en YouTube Music: algo")
    permissions.set_scope(SCOPE, {"musica_poner", "fecha"})
    script.steps = [
        step(calls=[("consultar.fecha", ""), ("musica.poner", "playlist PLabcdefghij")]),
        step("Listo"),
    ]
    t = turn()

    core._loop(t, [], core.Session(), False)

    assert [(tool, args, ok) for tool, args, _, ok in t.trace] == [("fecha", "", True), ("musica_poner", "playlist PLabcdefghij", True)]
    assert t.trace[1][2] == "Sonando en YouTube Music: algo"  # el texto legible, sin el rótulo de «datos»


def test_a_failing_tool_is_traced_with_ok_false(script, monkeypatch):
    def boom(kind, value, shuffle):
        raise core.navegador.NavegadorError("No encuentro el botón")

    monkeypatch.setattr(core.navegador, "play_music", boom)
    permissions.set_scope(SCOPE, {"musica_poner"})
    script.steps = [step(calls=[("musica.poner", "playlist PLabcdefghij")]), step("No pude")]
    t = turn()

    core._loop(t, [], core.Session(), False)

    assert [(tool, ok) for tool, _, _, ok in t.trace] == [("musica_poner", False)]
    assert "No encuentro el botón" in t.trace[0][2]


def test_blocked_calls_are_not_traced(script):
    permissions.set_scope(SCOPE, {"fecha"})
    script.steps = [step(calls=[("notas.guardar", "x")]), step("Listo")]
    t = turn()

    core._loop(t, [], core.Session(), False)

    assert t.trace == []
