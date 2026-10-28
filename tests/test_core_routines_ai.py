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
