import threading

import pytest

from epsilon.data import notes
from epsilon.routines import notify, runner
from epsilon.settings import config


@pytest.fixture(autouse=True)
def quiet(tmp_path, monkeypatch):
    """Datos temporales; avisos en `sent`, llamadas al ejecutor en `calls`; los hilos corren en el lugar."""
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    sent, calls = [], []
    monkeypatch.setattr(notify, "_notify_send", sent.append)
    monkeypatch.setattr(runner, "_launch", lambda job: job())
    monkeypatch.setattr(runner, "_running", set())
    runner._stop.clear()

    def executor(tool, args):
        calls.append((tool, args))
        return runner.StepResult(True, f"hecho {tool} {args}")

    runner.set_executor(executor)
    yield sent, calls
    runner.set_executor(None)
    runner._stop.clear()


def rule(*chain, id=1, **extra):
    return {"id": id, "name": "Prueba", "steps": [{"tool": t, "args": a} for t, a in chain], "when": {"kind": "daily"}, **extra}


def log():
    return (notes.DATA_DIR / runner.LOG_FILE).read_text(encoding="utf-8")


def test_the_output_of_a_step_reaches_the_next_one(quiet):
    sent, calls = quiet

    results = runner.run_chain(rule(("nav_abrir", "yt"), ("avisar", "Suena {{paso 1}}")))

    assert [r.ok for r in results] == [True, True]
    assert calls == [("nav_abrir", "yt")]
    assert sent == ["⏰ Suena hecho nav_abrir yt"]


def test_a_failure_stops_the_chain_and_says_which_step(quiet):
    sent, calls = quiet
    runner.set_executor(lambda tool, args: runner.StepResult(tool != "nav_abrir", "Chromium no abre"))

    runner.run_job(rule(("volumen", "15"), ("nav_abrir", "yt"), ("musica", "play")))

    assert len(sent) == 1 and "paso 2" in sent[0] and "nav_abrir" in sent[0] and "Chromium no abre" in sent[0]
    assert "musica" not in log()


def test_a_wait_over_the_limit_fails_without_sleeping(quiet, monkeypatch):
    monkeypatch.setattr(runner._stop, "wait", lambda seconds: pytest.fail("no debería dormir"))

    results = runner.run_chain(rule(("esperar", "90")))

    assert results[0].ok is False


def test_a_wait_sleeps_and_stop_releases_it(quiet):
    waiter = threading.Thread(target=lambda: runner.run_chain(rule(("esperar", "30"))))
    waiter.start()
    runner.stop()
    waiter.join(2)

    assert not waiter.is_alive()


def test_a_second_start_of_the_same_rule_is_skipped_and_told(quiet, monkeypatch):
    sent, _ = quiet
    monkeypatch.setattr(runner, "_launch", lambda job: None)  # el primero queda «corriendo»

    assert runner.start(rule(("volumen", "10"))) is True
    assert runner.start(rule(("volumen", "10"))) is False

    assert any("sigue en curso" in text for text in sent)


def test_other_rules_are_not_blocked(quiet, monkeypatch):
    monkeypatch.setattr(runner, "_launch", lambda job: None)

    assert runner.start(rule(("volumen", "10"), id=1)) is True
    assert runner.start(rule(("volumen", "10"), id=2)) is True


def test_the_running_mark_is_cleared_after_the_job(quiet):
    assert runner.start(rule(("volumen", "10"))) is True
    assert runner.start(rule(("volumen", "10"))) is True


def test_without_an_executor_a_normal_step_fails_clearly(quiet):
    runner.set_executor(None)

    results = runner.run_chain(rule(("volumen", "10")))

    assert results[0].ok is False and "ejecutor" in results[0].text


def test_the_budget_runs_out(quiet, monkeypatch):
    ticks = iter([0, 0, config.number("ROUTINES_MAX_RUN_SECONDS") + 1, config.number("ROUTINES_MAX_RUN_SECONDS") + 2])
    monkeypatch.setattr(runner, "_clock", lambda: next(ticks))

    results = runner.run_chain(rule(("volumen", "10"), ("volumen", "20")))

    assert [r.ok for r in results] == [True, False] and "tiempo" in results[1].text


def test_the_output_is_redacted_and_trimmed_before_the_next_step(quiet, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k" * 30)
    runner.set_executor(lambda tool, args: runner.StepResult(True, "clave " + "k" * 30 + " " + "x" * 5000) if tool == "a" else runner.StepResult(True, args))

    results = runner.run_chain(rule(("a", ""), ("b", "{{paso 1}}")))

    assert "k" * 30 not in results[1].text and len(results[1].text) <= 1100


def test_a_condition_notice_says_what_triggered_it(quiet):
    sent, _ = quiet

    runner.run_chain(rule(("avisar", "poco espacio")), trigger="el disco al 93 %")

    assert sent == ["⚠ poco espacio (el disco al 93 %)"]


def test_an_executor_that_raises_is_a_failed_step(quiet):
    def broken(tool, args):
        raise RuntimeError("boom")

    runner.set_executor(broken)

    assert runner.run_chain(rule(("volumen", "10")))[0].ok is False


def test_every_step_is_logged(quiet):
    runner.run_job(rule(("volumen", "15"), ("avisar", "hola")))

    assert log().count("\n") == 2


def test_every_step_lands_in_the_activity_with_the_routine_name(quiet):
    from epsilon.data import activity

    runner.run_chain(rule(("volumen", "20"), ("avisar", "listo"), name="Mañana"))

    items = activity.read()["items"]
    assert [(e["who"], e["tool"], e["routine"], e["ok"]) for e in items] == [
        ("rutina", "avisar", "Mañana", True),
        ("rutina", "volumen", "Mañana", True),
    ]
    assert items[1]["arg"] == "20" and "asked" not in items[1]


def test_a_test_run_from_the_editor_is_marked_as_such(quiet):
    from epsilon.data import activity

    runner.run_chain({"name": "Nueva", "steps": [{"tool": "volumen", "args": "20"}]})

    assert activity.read()["items"][0]["routine"] == "Nueva (prueba)"


# --- pasos con IA ------------------------------------------------------------------------------------------------


@pytest.fixture
def ai_executor():
    seen = []

    def execute(rule, step, outputs):
        seen.append((rule["id"], step["tool"], dict(outputs)))
        return runner.StepResult(True, "Puse new calm: tranquila y de energía baja")

    runner.set_ai_executor(execute)
    yield seen
    runner.set_ai_executor(None)


def ai_rule(**extra):
    return rule(("ia", ""), id=3, ai={"instruction": "Poné música", "tools": {"fecha": "green"}}, **extra)


def test_ia_step_without_an_ai_executor_fails_cleanly(quiet):
    results = runner.run_chain(ai_rule())

    assert [(r.ok, r.text) for r in results] == [(False, "No hay un ejecutor de IA registrado.")]


def test_ia_step_goes_to_the_ai_executor_with_the_rule_and_earlier_outputs(quiet, ai_executor):
    results = runner.run_chain(rule(("volumen", "15"), ("ia", ""), id=3, ai={"instruction": "x", "tools": {"volumen": "green"}}))

    assert [r.ok for r in results] == [True, True]
    assert ai_executor == [(3, "ia", {1: "hecho volumen 15"})]


def test_run_job_sends_a_one_line_notice_for_ai_rules(quiet, ai_executor):
    sent, _ = quiet

    runner.run_job(ai_rule())

    assert sent == ["🤖 Prueba: Puse new calm: tranquila y de energía baja"]


def test_the_notice_is_one_short_line(quiet, ai_executor):
    sent, _ = quiet
    runner.set_ai_executor(lambda rule, step, outputs: runner.StepResult(True, "linea uno\n" + "x" * 500))

    runner.run_job(ai_rule())

    assert len(sent) == 1 and "\n" not in sent[0] and len(sent[0]) < 260


def test_a_fixed_rule_sends_no_extra_notice(quiet):
    sent, _ = quiet

    runner.run_job(rule(("volumen", "15")))

    assert sent == []


def test_a_failed_ai_step_sends_the_failure_notice_not_the_summary(quiet):
    sent, _ = quiet
    runner.set_ai_executor(lambda rule, step, outputs: runner.StepResult(False, "Esta rutina llegó a su tope de hoy"))
    try:
        runner.run_job(ai_rule())
    finally:
        runner.set_ai_executor(None)

    assert len(sent) == 1 and "tope de hoy" in sent[0] and "paso 1" in sent[0]


def test_hand_edited_recipe_with_a_non_green_tool_fails_at_run_time(quiet):
    sent, calls = quiet
    edited = rule(("fecha", ""), ("musica_poner", "playlist PLabcdefghij"), id=4, ai={"instruction": "x", "tools": {"fecha": "green", "musica_poner": "red"}})

    runner.run_job(edited)

    assert calls == [("fecha", "")]  # la roja no corrió
    assert len(sent) == 1 and "musica_poner" in sent[0] and "no está en verde" in sent[0]


def test_a_tool_missing_from_the_ai_block_or_a_broken_block_does_not_run(quiet):
    _, calls = quiet

    for broken in ({"instruction": "x"}, {"instruction": "x", "tools": "roto"}, "roto"):
        assert [r.ok for r in runner.run_chain(rule(("fecha", ""), ai=broken))] == [False]
    assert calls == []


def test_local_steps_run_in_ai_rules_without_being_green(quiet):
    sent, _ = quiet

    runner.run_chain(rule(("avisar", "hola"), ai={"instruction": "x", "tools": {}}))

    assert sent == ["⏰ hola"]


def test_a_recipe_step_with_a_tool_not_in_green_does_not_run(quiet):
    sent, calls = quiet

    results = runner.run_chain(rule(("musica_poner", "playlist PLabcdefghij"), id=5, ai={"instruction": "x", "tools": {"musica_poner": "red"}}))

    assert [r.ok for r in results] == [False] and calls == [] and "no está en verde" in results[0].text


def test_the_output_of_a_source_step_is_kept_whole_and_the_executor_is_told_the_cap(quiet):
    _, calls = quiet
    seen = []
    runner.set_executor(lambda tool, args, **extra: (seen.append(extra), runner.StepResult(True, "x" * 5000))[1])
    runner.set_ai_executor(lambda rule, step, outputs: runner.StepResult(True, str(len(outputs[1]))))
    chain = [("musica_playlists", ""), ("elegir", "x"), ("fecha", "")]
    r = rule(*chain, id=6, ai={"instruction": "x", "tools": {"musica_playlists": "green", "fecha": "green"}})
    r["steps"][1].update(source=1, source_tool="musica_playlists")

    results = runner.run_chain(r)

    assert results[0].text == "x" * 5000 and results[1].text == "5000"  # la fuente no se recorta a MAX_OUTPUT
    assert seen == [{"cap": runner.steps.MAX_SOURCE_OUTPUT}, {}]  # solo la fuente recibe el tope; fecha, no


# --- receta vieja: caída a la IA completa -------------------------------------------------------------------------------

from epsilon.routines import store


@pytest.fixture
def saved(quiet):
    """Una rutina con receta guardada de verdad (para ver que se marca) y un ejecutor de IA que anota lo que corre."""
    runs = []

    def full(rule, step, outputs):
        runs.append(step["tool"])
        return runner.StepResult(True, "Puse algo tranquilo")

    runner.set_ai_executor(full)
    recipe = [{"tool": "fecha", "args": "", "kind": "fijo"}, {"tool": "volumen", "args": "15", "kind": "fijo"}]
    rule = store.add(__import__("epsilon.routines.rules", fromlist=["build_ai"]).build_ai(
        "Despertador", "08:00", "Poné música", {"fecha": "green", "volumen": "green"}, __import__("datetime").datetime(2026, 10, 22, 14, 30), {"fecha", "volumen"}, recipe=recipe,
    ))
    yield rule, runs
    runner.set_ai_executor(None)


def test_a_failing_fixed_step_falls_back_to_one_full_ai_run_and_marks_stale(quiet, saved):
    sent, calls = quiet
    rule, runs = saved
    runner.set_executor(lambda tool, args: runner.StepResult(tool != "volumen", "el volumen no responde"))

    runner.run_job(rule)

    assert runs == ["ia"]  # una corrida de la IA completa
    assert store.rules()[0]["ai"]["stale"] is True
    assert len(sent) == 1 and "quedó vieja" in sent[0] and "paso 2" in sent[0] and "el volumen no responde" in sent[0] and "Puse algo tranquilo" in sent[0]
    assert "Volvé a aprenderla" in sent[0]


def test_the_fallback_runs_at_most_once(quiet, saved):
    sent, _ = quiet
    rule, _runs = saved
    attempts = []
    runner.set_executor(lambda tool, args: runner.StepResult(False, "caído"))
    runner.set_ai_executor(lambda r, step, outputs: (attempts.append(1), runner.StepResult(False, "tampoco anda"))[1])

    runner.run_job(rule)

    assert attempts == [1] and len(sent) == 1 and "tampoco anda" in sent[0] and "quedó vieja" not in sent[0]
    assert store.rules()[0]["ai"]["stale"] is True  # la receta igual se marca


def test_a_stale_rule_runs_full_ai_until_relearned(quiet, saved):
    _, calls = quiet
    rule, runs = saved
    store.mark_stale(rule["id"], True)

    runner.run_job(store.rules()[0])

    assert runs == ["ia"] and calls == []  # ni un paso fijo de la receta


def test_a_rule_without_a_recipe_does_not_fall_back(quiet):
    sent, _ = quiet
    attempts = []
    runner.set_ai_executor(lambda r, step, outputs: (attempts.append(1), runner.StepResult(False, "sin conexión"))[1])
    try:
        runner.run_job(ai_rule())
    finally:
        runner.set_ai_executor(None)

    assert attempts == [1] and len(sent) == 1 and "quedó vieja" not in sent[0] and "sin conexión" in sent[0]


def test_a_budget_failure_does_not_mark_the_recipe_stale_or_retry(quiet, saved):
    sent, _ = quiet
    rule, runs = saved
    runner.set_executor(lambda tool, args, **extra: runner.StepResult(True, "ok"))
    runner.set_ai_executor(lambda r, step, outputs: runner.StepResult(False, "llegó a su tope de hoy", fatal=True))
    rule = {**rule, "steps": [{"tool": "elegir", "args": "x", "source": 1, "source_tool": "fecha"}], "ai": {**rule["ai"]}}
    rule["steps"].insert(0, {"tool": "fecha", "args": ""})

    runner.run_job(rule)

    assert store.rules()[0]["ai"]["stale"] is False and len(sent) == 1 and "tope de hoy" in sent[0] and "quedó vieja" not in sent[0]


def test_a_test_run_without_an_id_falls_back_without_touching_the_store(quiet):
    sent, _ = quiet
    runner.set_executor(lambda tool, args: runner.StepResult(False, "caído"))
    runner.set_ai_executor(lambda r, step, outputs: runner.StepResult(True, "lo hizo la IA"))
    try:
        r = {"name": "Prueba", "steps": [{"tool": "fecha", "args": ""}], "when": {"kind": "daily"}, "ai": {"instruction": "x", "recipe": True, "stale": False, "tools": {"fecha": "green"}}}
        runner.run_job(r)
    finally:
        runner.set_ai_executor(None)

    assert store.rules() == [] and "quedó vieja" in sent[0]
