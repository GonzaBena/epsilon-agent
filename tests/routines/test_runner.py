import threading

import pytest

from epsilon.data import notes
from epsilon.routines import notify, runner


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
    ticks = iter([0, 0, runner.RUN_BUDGET + 1, runner.RUN_BUDGET + 2])
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
