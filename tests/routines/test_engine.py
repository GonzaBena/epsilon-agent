from datetime import datetime, timedelta
import threading

import pytest

from epsilon.data import notes
from epsilon.routines import engine, notify, rules, store
from epsilon.utils import multimedia

REAL_VOLUME = multimedia.volume  # antes de que el fixture lo reemplace
NOW = datetime(2026, 10, 22, 23, 0, 10)  # diez segundos después de las 23:00


@pytest.fixture(autouse=True)
def quiet(tmp_path, monkeypatch):
    """Datos temporales; los avisos se anotan en `sent` y las acciones del sistema, en `done`."""
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    sent, done = [], []
    monkeypatch.setattr(notify, "_notify_send", sent.append)
    monkeypatch.setattr(multimedia, "volume", lambda arg: done.append(("volumen", arg)) or f"Volumen: {arg} %")
    monkeypatch.setattr(multimedia, "music", lambda arg: done.append(("musica", arg)) or "Pausé. (chromium)")
    return sent, done


def add(text, created=datetime(2026, 10, 22, 8, 0)):
    return store.add(rules.build(text, created))


def log():
    return (notes.DATA_DIR / engine.LOG_FILE).read_text(encoding="utf-8")


def test_a_due_rule_runs_with_the_normal_functions_tells_you_and_moves_to_tomorrow(quiet):
    sent, done = quiet
    add("23:00 | volumen 20")

    engine.tick(NOW)

    assert done == [("volumen", "20")]
    assert sent == ["Rutina 1: Volumen: 20 %"]
    assert store.rules()[0]["next_run"] == "2026-10-23T23:00:00"
    assert "ejecutada" in log()


def test_it_does_nothing_before_the_time(quiet):
    sent, done = quiet
    add("23:00 | volumen 20")

    engine.tick(datetime(2026, 10, 22, 22, 59, 50))

    assert done == [] and sent == [] and store.rules()[0]["next_run"] == "2026-10-22T23:00:00"


def test_a_notice_rule_just_notifies(quiet):
    sent, done = quiet
    add("23:00 | avisar sacar la pizza")

    engine.tick(NOW)

    assert sent == ["⏰ sacar la pizza"] and done == []


def test_a_single_run_is_removed_after_it_runs(quiet):
    sent, _ = quiet
    add("en 30m | avisar hola", created=datetime(2026, 10, 22, 22, 30))

    engine.tick(NOW)

    assert sent == ["⏰ hola"] and store.rules() == []


def test_the_same_minute_never_runs_twice(quiet):
    _, done = quiet
    add("23:00 | volumen 20")

    engine.tick(NOW), engine.tick(NOW + timedelta(seconds=30)), engine.tick(NOW + timedelta(seconds=60))

    assert done == [("volumen", "20")]


def test_a_missed_rule_is_not_run_late_just_reported_in_one_message(quiet):
    sent, done = quiet
    add("23:00 | volumen 20"), add("22:00 | musica pausa"), add("en 5m | avisar viejo", created=datetime(2026, 10, 22, 20, 0))

    engine.tick(datetime(2026, 10, 23, 8, 0))  # la PC estuvo apagada toda la noche

    assert done == []
    assert len(sent) == 1 and sent[0].startswith("Me perdí 3 rutinas") and "era 22/10 23:00" in sent[0]
    assert [r["id"] for r in store.rules()] == [1, 2]  # la de «una vez» vencida se borró
    assert store.rules()[0]["next_run"] == "2026-10-23T23:00:00"


def test_paused_rules_do_not_run(quiet):
    _, done = quiet
    add("23:00 | volumen 20")
    store.set_enabled(1, False, datetime(2026, 10, 22, 8, 0))

    engine.tick(NOW)

    assert done == []


def test_the_global_switch_stops_everything_without_touching_the_rules(quiet, monkeypatch):
    sent, done = quiet
    add("23:00 | volumen 20")
    monkeypatch.setenv("ROUTINES_ENABLED", "no")

    engine.tick(NOW)

    assert done == [] and sent == [] and store.rules()[0]["next_run"] == "2026-10-22T23:00:00"


def test_a_failing_action_is_reported_and_does_not_stop_the_next_rule(quiet, monkeypatch):
    sent, done = quiet

    def broken(arg):
        raise multimedia.MultimediaError("No hay ningún reproductor abierto.")

    monkeypatch.setattr(multimedia, "music", broken)
    add("23:00 | musica pausa"), add("23:00 | volumen 20")

    engine.tick(NOW)

    assert done == [("volumen", "20")]
    assert sent[0].startswith("No pude cumplir la rutina 1") and "ningún reproductor" in sent[0]
    assert "falló" in log()


def test_a_broken_rule_is_paused_instead_of_failing_every_pass(quiet):
    sent, done = quiet
    add("23:00 | volumen 20")
    store.update(lambda all_rules: all_rules[0].update(next_run="no es una fecha"))

    engine.tick(NOW), engine.tick(NOW)

    assert done == [] and store.rules()[0]["enabled"] is False
    assert log().count("regla inválida") == 1


def test_a_failing_notification_is_logged_but_the_rule_still_counts(quiet, monkeypatch):
    _, done = quiet

    def broken(text):
        raise RuntimeError("sin bus")

    monkeypatch.setattr(notify, "_notify_send", broken)
    add("23:00 | volumen 20")

    engine.tick(NOW)

    assert done == [("volumen", "20")] and "aviso falló" in log()


def test_a_hand_edited_rule_is_validated_again_by_the_real_function(quiet, monkeypatch):
    sent, _ = quiet
    monkeypatch.setattr(multimedia, "volume", REAL_VOLUME)  # sin el reemplazo del fixture
    store.update(lambda all_rules: all_rules.append({
        "id": 1, "when": {"kind": "daily", "time": "23:00", "days": list(range(7))},
        "do": {"kind": "volumen", "arg": "40; reboot"}, "enabled": True, "next_run": "2026-10-22T23:00:00"}))

    engine.tick(NOW)

    assert sent and sent[0].startswith("No pude cumplir la rutina 1") and "Escribí un número" in sent[0]


def test_start_and_stop_the_thread(monkeypatch):
    enough = threading.Event()
    passes = []

    def fake_tick(now):
        passes.append(now)
        if len(passes) >= 3:
            enough.set()

    monkeypatch.undo()  # el conftest dejó `start` en un no-op; acá se prueba el real
    monkeypatch.setattr(engine, "tick", fake_tick)
    monkeypatch.setattr(engine, "TICK", 0.01)

    engine.start()
    first = engine._thread
    engine.start()  # una segunda vez no lanza otro hilo
    assert engine._thread is first and enough.wait(2)
    engine.stop()

    assert not first.is_alive()
