from epsilon import core
from epsilon.ai import permissions, usage
from epsilon.routines import store as routine_store
from tests.test_core_tools import CID, OTHER, inbox, results, say, script, step  # noqa: F401 - `script` es un fixture

# El modo «YOLO seguro» de la voz dentro de core: lo que escribe sin preguntar y lo que no hace nunca.


def voice_mode(cid=CID):
    permissions.set_mode(cid, permissions.SAFE_AUTO)


def test_a_write_that_would_normally_ask_just_runs_by_voice(script):
    voice_mode()
    script.steps = [step(calls=[("notas.guardar", "comprar pan")]), step("Anotado")]

    reply = say("anotá comprar pan")

    assert reply.text == "Anotado" and not reply.actions
    assert "comprar pan" in inbox()


def test_the_same_write_still_asks_in_a_conversation_without_the_mode(script):
    script.steps = [step(calls=[("notas.guardar", "comprar pan")])]

    reply = say("anotá comprar pan", OTHER)

    assert reply.actions and "comprar pan" not in inbox()


def test_a_destructive_tool_is_not_run_and_the_model_is_told_so(script):
    voice_mode()
    from epsilon.memory import store as memory

    memory.remember("mi gato se llama Tom")
    script.steps = [step(calls=[("memoria.borrar", "1")]), step("No pude")]

    say("olvidá lo del gato")

    assert "Tom" in memory.read()
    assert results(script.seen[1][0]) == [core.UNAVAILABLE]


def test_creating_a_routine_is_never_confirmed_by_voice_even_if_allowed(script):
    voice_mode()
    states = permissions.load()
    states["rutina_crear"] = "allow"
    permissions.save(states)
    script.steps = [step(calls=[("rutinas.crear", "23:00 | volumen 20")]), step("No pude")]

    reply = say("creá una rutina")

    assert reply.text == "No pude" and not reply.actions
    assert results(script.seen[1][0]) == [core.UNAVAILABLE]
    assert routine_store.rules() == []


def test_when_the_browser_guard_would_ask_nothing_is_done_and_the_model_hears_why(script, monkeypatch):
    # Tocar un botón de «enviar» en un sitio ajeno: la guardia endurece a «preguntar»; por voz no hay a quién preguntar.
    voice_mode()
    monkeypatch.setattr(core.browser_guard, "review", lambda tool, args, turn: core.browser_guard.Review("ask", "sitio ajeno"))
    script.steps = [step(calls=[("notas.buscar", "x")]), step("No pude")]

    reply = say("buscá x")

    assert reply.text == "No pude" and not reply.actions
    assert results(script.seen[1][0]) == [core.NO_ASKING_VOICE]


def test_the_cost_limit_still_applies_to_a_voice_conversation(script):
    voice_mode()
    with usage.conversation(CID), usage.action("Chat"):
        usage.record("modelo-a", 100_000_000, 0)  # mucho más que el tope
    script.steps = [step("no debería llegar")]

    reply = say("hola")

    assert "límite" in reply.text.lower()
    assert script.seen == []
