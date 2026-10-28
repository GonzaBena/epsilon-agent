import pytest

from epsilon import core, tools
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


# --- correr el paso `ia` -----------------------------------------------------------------------------------------

from datetime import datetime

from epsilon.ai import usage
from epsilon.routines import rules as routine_rules, runner, store as routine_store
from epsilon.routines.steps import AI_STEPS  # noqa: F401

NOW = datetime(2026, 10, 28, 8, 0).astimezone()


@pytest.fixture
def ai_rule(script, monkeypatch):
    """Una rutina con IA guardada (verdes: fecha y musica_poner); el reloj fijo en el 28/10/2026."""
    monkeypatch.setattr(core.clock, "now", lambda: NOW)
    known = core._catalog_ids()
    rule = routine_store.add(routine_rules.build_ai(
        "Despertador", "08:00", "Poné música para despertarme", {"fecha": "green", "musica_poner": "green", "nota": "red"}, NOW, known,
    ))
    return rule


def run(rule, step_dict=None, outputs=None, trace=None):
    return core.run_ai_step(rule, step_dict or rule["steps"][0], outputs or {}, trace)


def test_ia_step_runs_with_only_green_tools(ai_rule, script):
    script.steps = [step(calls=[("notas.guardar", "hola")]), step("No pude guardar")]

    result = run(ai_rule)

    assert (result.ok, result.text) == (True, "No pude guardar")
    assert results(script.seen[1][0]) == [core.UNAVAILABLE]  # `nota_guardar` está en rojo: no corre — Review Focus 1
    assert not (notes.DATA_DIR / "inbox.md").exists()


def test_ia_step_offers_the_model_the_instruction_as_the_persons_text(ai_rule, script):
    script.steps = [step("Listo")]

    run(ai_rule, outputs={1: "Volumen al 15 %"})

    sent = str(script.seen[0][0][0])
    assert "Poné música para despertarme" in sent and "instrucción de la persona" in sent.lower()
    assert "Volumen al 15 %" in sent and "datos" in sent


def test_ia_step_only_offers_the_green_tools(ai_rule, script):
    script.steps = [step("Listo")]

    run(ai_rule)

    offered = script.seen[0][1]
    assert "consultar" in offered and "musica" in offered
    assert "notas" not in offered and "herramientas" not in offered  # todo lo verde ya viaja entero


def test_an_only_list_on_the_step_narrows_the_green_tools(ai_rule, script):
    script.steps = [step(calls=[("musica.poner", "playlist PLabcdefghij")]), step("Listo")]

    run(ai_rule, {"tool": "ia", "args": "", "only": ["fecha"]})

    assert results(script.seen[1][0]) == [core.UNAVAILABLE]


def test_ia_step_without_a_known_price_does_not_call_the_model(ai_rule, script, monkeypatch):
    monkeypatch.setattr(usage, "PRICES", {})
    script.steps = []

    result = run(ai_rule)

    assert not result.ok and "precio" in result.text and script.seen == []


def test_ollama_model_runs_without_a_price_problem(ai_rule, script, monkeypatch):
    monkeypatch.setenv("AI_MODEL_ASK", "ollama:llama3.1:8b")
    monkeypatch.setattr(usage, "PRICES", {})
    monkeypatch.setattr(llm, "capabilities", lambda: type("C", (), {"tools": True})())
    script.steps = [step("Listo")]

    assert run(ai_rule).ok


def test_ia_step_stops_at_the_daily_cap_and_the_notice_says_so(ai_rule, script, monkeypatch):
    routine_store.add_spent(ai_rule["id"], 0.05, NOW.date())
    script.steps = []

    result = run(routine_store.rules()[0])

    assert not result.ok and "tope de hoy" in result.text and "0.05" in result.text and script.seen == []


def test_spend_resets_on_a_new_day(ai_rule, script, monkeypatch):
    routine_store.add_spent(ai_rule["id"], 0.05, NOW.date())
    monkeypatch.setattr(core.clock, "now", lambda: datetime(2026, 10, 29, 8, 0).astimezone())
    script.steps = [step("Listo")]

    assert run(routine_store.rules()[0]).ok


def test_the_run_cost_is_added_to_the_days_spend(ai_rule, script):
    script.steps = [step("Listo")]
    script.tokens = 100_000  # a US$ 1 por millón de entrada: 0.10

    run(ai_rule)

    assert routine_rules.spent_today(routine_store.rules()[0], NOW.date()) == pytest.approx(0.1)


def test_ia_step_honours_the_step_cap(ai_rule, script, monkeypatch):
    monkeypatch.setenv("ROUTINES_AI_MAX_STEPS", "2")
    script.steps = [step(calls=[("consultar.fecha", "")]) for _ in range(5)]

    result = run(ai_rule)

    assert len(script.seen) == 3  # dos vueltas con herramientas y una última que ya no puede pedirlas
    assert not result.ok or result.text  # no se queda dando vueltas


def test_scope_is_cleared_even_if_the_model_raises(ai_rule, script, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("se cayó")

    monkeypatch.setattr(llm, "chat_tools", boom)

    result = run(ai_rule)

    assert not result.ok
    assert permissions._scopes == {}


def test_the_trace_is_copied_when_asked_for(ai_rule, script):
    script.steps = [step(calls=[("consultar.fecha", "")]), step("Listo")]
    trace = []

    run(ai_rule, trace=trace)

    assert [tool for tool, *_ in trace] == ["fecha"]


def test_each_run_gets_its_own_scope(ai_rule, script):
    seen = []
    real = permissions.set_scope
    permissions_set = lambda cid, allowed: (seen.append(cid), real(cid, allowed))  # noqa: E731
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(permissions, "set_scope", permissions_set)
    try:
        script.steps = [step("a"), step("b")]
        run(ai_rule)
        run(ai_rule)
    finally:
        mp.undo()

    assert len(set(seen)) == 2 and all(s.startswith(f"rutina:{ai_rule['id']}:") for s in seen)


def test_an_empty_answer_is_a_failed_step(ai_rule, script):
    script.steps = [step("")]

    assert not run(ai_rule).ok


def test_the_model_error_text_is_a_failed_step(ai_rule, script, monkeypatch):
    def down(*args, **kwargs):
        raise llm.ModelError("Sin conexión con Ollama")

    monkeypatch.setattr(llm, "chat_tools", down)

    result = run(ai_rule)

    assert (result.ok, result.text) == (False, "Sin conexión con Ollama")


def test_register_routine_executor_also_registers_the_ai_one():
    core.register_routine_executor()
    try:
        assert runner._ai_executor is core.run_ai_step
    finally:
        runner.set_executor(None)
        runner.set_ai_executor(None)


# --- sugerencias y catálogo de la grilla -------------------------------------------------------------------------


def test_suggestions_for_play_music_include_music_tools_and_not_private_ones(script):
    found = core.routine_suggestions("poné música para despertarme")

    assert {"musica_poner", "musica_playlists", "musica_buscar"} <= set(found)
    assert not {"yt_historial", "ventanas", "nota", "crear_archivo"} & set(found)


def test_suggestions_never_include_routine_tools_or_ai_spenders(script):
    found = set(core.routine_suggestions("cada día a las 8 resumime todo y avisame, poné música y guardá una nota"))
    spenders = {tools.ai_id(t) for t in tools.ai_tools() if t.uses_ai}

    assert not spenders & found and not {"avisar", "esperar", "elegir"} & found and "nota" not in found


def test_suggestions_of_unrelated_text_are_empty_but_the_clock(script):
    assert core.routine_suggestions("qwxz plmk") == ["fecha"]
    assert core.routine_suggestions("   ") == []


def test_catalog_marks_sensitive_tools(script):
    by_id = {item["id"]: item for item in core.routine_catalog()}

    assert by_id["yt_historial"]["sensitive"] and by_id["nota"]["sensitive"] and by_id["ventanas"]["sensitive"]
    assert not by_id["musica_poner"]["sensitive"] and not by_id["fecha"]["sensitive"]
    assert all("sensitive" in item for item in by_id.values())
