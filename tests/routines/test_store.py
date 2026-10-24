from datetime import datetime
import json

import pytest

from epsilon.data import notes
from epsilon.routines import rules, store

NOW = datetime(2026, 10, 22, 14, 30)


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())


def make(text="23:00 | volumen 20"):
    return rules.build(text, NOW)


def test_starts_empty_and_ids_grow():
    assert store.rules() == []

    first, second = store.add(make()), store.add(make("07:00 | musica play"))

    assert (first["id"], second["id"]) == (1, 2)
    assert [r["id"] for r in store.rules()] == [1, 2]


def test_it_lives_in_a_fixed_file_inside_the_data_folder():
    store.add(make())

    assert json.loads((notes.DATA_DIR / store.FILE).read_text(encoding="utf-8"))["rules"][0]["steps"] == [{"tool": "volumen", "args": "20"}]
    assert not list(notes.DATA_DIR.glob("*.tmp"))


def test_remove():
    store.add(make())

    assert store.remove(1) is True and store.remove(1) is False
    assert store.rules() == []


def test_the_limit(monkeypatch):
    monkeypatch.setattr(store, "MAX_RULES", 2)
    store.add(make()), store.add(make())

    with pytest.raises(store.StoreError, match="borrá alguna"):
        store.add(make())


def test_the_notice_text_is_redacted(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k" * 30)

    saved = store.add(make("en 5m | avisar la clave es " + "k" * 30))

    assert "k" * 30 not in saved["steps"][0]["args"]


def test_a_broken_file_reads_as_no_rules():
    (notes.DATA_DIR / store.FILE).write_text("{no es json", encoding="utf-8")

    assert store.rules() == []
    assert store.add(make())["id"] == 1


def test_pause_and_resume_recomputes_the_next_run():
    store.add(make())

    assert store.set_enabled(1, False, NOW)["enabled"] is False
    resumed = store.set_enabled(1, True, datetime(2026, 10, 25, 22, 0))

    assert resumed["enabled"] is True and resumed["next_run"] == "2026-10-25T23:00:00"
    assert store.set_enabled(9, True, NOW) is None


def test_resuming_a_single_run_that_already_passed_deletes_it():
    store.add(make("en 5m | avisar hola"))
    store.set_enabled(1, False, NOW)

    with pytest.raises(store.StoreError, match="ya pasó"):
        store.set_enabled(1, True, datetime(2026, 10, 23, 9, 0))
    assert store.rules() == []


def test_pausing_and_resuming_a_condition_never_deletes_it():
    store.add(make("disco>90 | avisar x"))

    assert store.set_enabled(1, False, NOW)["enabled"] is False
    assert store.set_enabled(1, True, NOW)["enabled"] is True
    assert len(store.rules()) == 1


def test_an_old_file_is_migrated_when_read():
    (notes.DATA_DIR / store.FILE).write_text(json.dumps({"rules": [
        {"id": 3, "when": {"kind": "daily", "time": "23:00", "days": [0]}, "do": {"kind": "avisar", "arg": "hola"}, "enabled": True, "next_run": "2026-10-23T23:00:00"}]}), encoding="utf-8")

    assert store.rules()[0]["steps"] == [{"tool": "avisar", "args": "hola"}]


def test_replace_keeps_id_and_enabled_and_recomputes_next_run():
    saved = store.add(make())
    store.set_enabled(saved["id"], False, NOW)

    new = rules.build_from("Otra", "07:00", [{"tool": "musica", "args": "play"}], NOW, {"musica"})
    result = store.replace(saved["id"], new)

    assert result["id"] == saved["id"] and result["enabled"] is False
    assert store.rules()[0]["name"] == "Otra" and store.rules()[0]["next_run"] == "2026-10-23T07:00:00"
    assert store.replace(99, new) is None
