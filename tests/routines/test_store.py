from datetime import date, datetime
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


# --- rutinas con IA --------------------------------------------------------------------------------

AI_KNOWN = {"musica_poner", "musica_playlists", "fecha"}


def make_ai(instruction="Poné música para despertarme"):
    return rules.build_ai("Despertador", "08:00", instruction, {"musica_poner": "green", "fecha": "red"}, NOW, AI_KNOWN)


def test_add_redacts_the_instruction_in_both_places():
    key = "AIzaSyD-ejemplo_de_clave_larga_QzXw"

    saved = store.add(make_ai(f"Usá la clave {key} para algo"))

    assert key not in json.dumps(store.rules()) and key not in saved["ai"]["instruction"] and key not in saved["steps"][0]["args"]


def test_add_spent_accumulates_within_a_day_and_resets_on_a_new_one():
    rule = store.add(make_ai())
    day1, day2 = date(2026, 10, 22), date(2026, 10, 23)

    assert store.add_spent(rule["id"], 0.01, day1) == 0.01
    assert store.add_spent(rule["id"], 0.02, day1) == pytest.approx(0.03)
    assert store.add_spent(rule["id"], 0.005, day2) == 0.005
    assert rules.spent_today(store.rules()[0], day2) == 0.005 and rules.spent_today(store.rules()[0], day1) == 0.0


def test_add_spent_ignores_unknown_rules_rules_without_ai_and_broken_blocks():
    plain = store.add(make())
    broken = store.add(make_ai())
    store.update(lambda all_rules: all_rules[1].__setitem__("ai", "roto"))

    assert store.add_spent(999, 0.01, date(2026, 10, 22)) == 0.0
    assert store.add_spent(plain["id"], 0.01, date(2026, 10, 22)) == 0.0
    assert store.add_spent(broken["id"], 0.01, date(2026, 10, 22)) == 0.0
    assert [r["ai"] for r in store.rules() if r["id"] == broken["id"]] == ["roto"]  # no la pisa


def test_replace_keeps_the_spent_counter_and_mark_stale_flips_the_flag():
    rule = store.add(make_ai())
    store.add_spent(rule["id"], 0.02, date(2026, 10, 22))

    store.replace(rule["id"], make_ai("Poné algo tranquilo"))
    store.mark_stale(rule["id"], True)

    saved = store.rules()[0]
    assert saved["ai"]["spent"] == {"day": "2026-10-22", "usd": 0.02}
    assert saved["ai"]["instruction"] == "Poné algo tranquilo" and saved["ai"]["stale"] is True
    store.mark_stale(rule["id"], False)
    store.mark_stale(999, True)  # no existe: no pasa nada
    assert store.rules()[0]["ai"]["stale"] is False


def test_replace_turns_a_manual_rule_into_an_ai_one_and_back_without_crashing():
    rule = store.add(make())

    store.replace(rule["id"], make_ai())
    assert "ai" in store.rules()[0]
    store.replace(rule["id"], make("07:00 | musica play"))
    assert "ai" not in store.rules()[0]
