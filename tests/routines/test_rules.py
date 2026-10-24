from datetime import datetime, timedelta

import pytest

from epsilon.routines import rules

# jueves 22 de octubre de 2026, 14:30
NOW = datetime(2026, 10, 22, 14, 30, 45)


# --- cuándo -----------------------------------------------------------------------


@pytest.mark.parametrize("text, days", [
    ("23:00", [0, 1, 2, 3, 4, 5, 6]),
    ("lun-vie 09:00", [0, 1, 2, 3, 4]),
    ("Sáb,dom 10:30", [5, 6]),
    ("mié 7:05", [2]),
    ("finde 11:00", [5, 6]),
    ("vie-lun 08:00", [0, 4, 5, 6]),  # el rango da la vuelta a la semana
    ("lunes-viernes 09:00", [0, 1, 2, 3, 4]),
])
def test_daily_times(text, days):
    when = rules.parse_when(text, NOW)

    assert when["kind"] == "daily" and when["days"] == days
    assert when["time"] == f"{int(text[-5:].strip(' ').split(':')[0]):02d}:{text[-2:]}"


@pytest.mark.parametrize("text, minutes", [("en 30m", 30), ("en 2h", 120), ("en 1h30m", 90), ("EN 45 m", 45)])
def test_in_a_while_counts_exactly_from_now_without_dropping_seconds(text, minutes):
    when = rules.parse_when(text, NOW)

    assert when["kind"] == "once"
    assert datetime.fromisoformat(when["at"]) == (NOW + timedelta(minutes=minutes)).replace(microsecond=0)


@pytest.mark.parametrize("text", ["", "mañana", "25:00", "23:60", "lun 9", "lun mar 09:00", "xyz-vie 09:00", "en", "en 0m", "en 99999m", "en 400h", "23:00; rm -rf /"])
def test_when_refuses_what_it_does_not_understand(text):
    with pytest.raises(ValueError):
        rules.parse_when(text, NOW)


# --- qué ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("volumen 20", {"kind": "volumen", "arg": "20"}),
    ("Volumen +5", {"kind": "volumen", "arg": "+5"}),
    ("musica pausa", {"kind": "musica", "arg": "pausa"}),
    ("musica", {"kind": "musica", "arg": ""}),
    ("avisar sacar la  pizza", {"kind": "avisar", "arg": "sacar la pizza"}),
])
def test_actions(text, expected):
    assert rules.parse_do(text) == expected


@pytest.mark.parametrize("text", ["", "apagar", "sistema ram", "volumen", "volumen mucho", "volumen 40; reboot", "musica quit", "avisar", "avisar " + "x" * 201, "ls -la", "/volumen 20"])
def test_actions_outside_the_fixed_list_or_with_bad_arguments_are_refused(text):
    with pytest.raises(ValueError):
        rules.parse_do(text)


# --- próxima vez ---------------------------------------------------------------------


def test_next_run_today_if_it_has_not_passed_yet_else_the_next_allowed_day():
    daily = {"kind": "daily", "time": "23:00", "days": list(range(7))}
    weekdays = {"kind": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4]}

    assert rules.next_after(daily, NOW) == datetime(2026, 10, 22, 23, 0)
    assert rules.next_after(weekdays, NOW) == datetime(2026, 10, 23, 9, 0)  # el viernes
    assert rules.next_after(weekdays, datetime(2026, 10, 23, 10, 0)) == datetime(2026, 10, 26, 9, 0)  # salta el finde


def test_next_run_is_strictly_after_so_a_run_cannot_repeat_in_the_same_minute():
    daily = {"kind": "daily", "time": "14:30", "days": list(range(7))}

    assert rules.next_after(daily, datetime(2026, 10, 22, 14, 30, 0)) == datetime(2026, 10, 23, 14, 30)


def test_a_single_run_that_already_passed_has_no_next():
    once = {"kind": "once", "at": "2026-10-22T14:00:00"}

    assert rules.next_after(once, NOW) is None


# --- regla completa -------------------------------------------------------------------


def test_build_a_full_rule():
    rule = rules.build("23:00 | volumen 20", NOW)

    assert rule == {"when": {"kind": "daily", "time": "23:00", "days": list(range(7))}, "name": "volumen 20",
                    "steps": [{"tool": "volumen", "args": "20"}], "enabled": True, "next_run": "2026-10-22T23:00:00"}


@pytest.mark.parametrize("text", ["", "23:00", "23:00 |", "| volumen 20", "volumen 20", "23:00 | "])
def test_build_needs_both_halves(text):
    with pytest.raises(ValueError):
        rules.build(text, NOW)


def test_the_text_can_hold_a_bar_in_the_notice():
    assert rules.build("en 5m | avisar a | b", NOW)["steps"][0]["args"] == "a | b"


def test_descriptions_read_like_people_talk():
    assert rules.describe(rules.build("23:00 | volumen 20", NOW)) == "todos los días a las 23:00 → volumen 20"
    assert rules.describe(rules.build("lun-vie 09:00 | avisar Standup", NOW)) == "de lunes a viernes a las 09:00 → avisar: Standup"
    assert rules.describe(rules.build("finde 10:00 | musica play", NOW)) == "sábado y domingo a las 10:00 → musica play"
    assert rules.describe(rules.build("lun,mié 10:00 | musica", NOW)) == "lunes, miércoles a las 10:00 → musica"
    assert rules.describe(rules.build("en 30m | avisar pizza", NOW)) == "una vez, 22/10 a las 15:00 → avisar: pizza"


# --- condiciones ------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, metric, value", [("disco>90", "disco", 90), ("Disco > 85 %", "disco", 85), ("ram>1", "ram", 1), ("RAM>99%", "ram", 99)])
def test_conditions(text, metric, value):
    assert rules.parse_when(text, NOW) == {"kind": "cond", "metric": metric, "value": value}


@pytest.mark.parametrize("text", ["disco>0", "disco>100", "ram>150", "disco<10", "cpu>90", "disco>", "disco>9x", "disco>90; reboot", "disco ram>90"])
def test_conditions_refuse_what_is_not_a_known_metric_with_a_threshold(text):
    with pytest.raises(ValueError):
        rules.parse_when(text, NOW)


def test_a_condition_rule_has_no_schedule_but_keeps_its_state():
    rule = rules.build("disco>90 | avisar poco espacio", NOW)

    assert rule == {"when": {"kind": "cond", "metric": "disco", "value": 90}, "name": "avisar: poco espacio",
                    "steps": [{"tool": "avisar", "args": "poco espacio"}], "enabled": True, "tripped": False, "last_run": None}
    assert rules.next_after(rule["when"], NOW) is None
    assert rules.describe(rule) == "cuando el disco pase del 90 % → avisar: poco espacio"
    assert rules.describe_when({"kind": "cond", "metric": "ram", "value": 85}) == "cuando la memoria pase del 85 %"


def cond(value=90):
    return rules.build(f"disco>{value} | avisar x", NOW)


def test_it_fires_when_it_crosses_the_threshold_and_not_again_while_it_stays_above():
    rule = cond()

    assert rules.check_condition(rule, 80.0, NOW) is False
    assert rules.check_condition(rule, 91.0, NOW) is True
    assert rules.check_condition(rule, 95.0, NOW + timedelta(minutes=1)) is False  # sigue arriba: ya avisó
    assert rule["tripped"] is True and rule["last_run"] == NOW.isoformat()


def test_equal_to_the_threshold_is_not_above_it():
    assert rules.check_condition(cond(), 90.0, NOW) is False


def test_it_rearms_when_the_value_comes_back_down_but_waits_out_the_cooldown():
    rule = cond()
    rules.check_condition(rule, 91.0, NOW)

    assert rules.check_condition(rule, 70.0, NOW + timedelta(minutes=5)) is False and rule["tripped"] is False
    assert rules.check_condition(rule, 92.0, NOW + timedelta(minutes=10)) is False  # sube otra vez, pero hace 10 min que avisó
    assert rules.check_condition(rule, 92.0, NOW + timedelta(minutes=59)) is False
    assert rules.check_condition(rule, 92.0, NOW + timedelta(hours=1)) is True  # terminó el reposo y sigue alto


def test_without_a_reading_nothing_happens_and_the_state_is_kept():
    rule = cond()
    rules.check_condition(rule, 91.0, NOW)

    assert rules.check_condition(rule, None, NOW + timedelta(hours=3)) is False
    assert rule["tripped"] is True


# --- pasos encadenados y migración --------------------------------------------------------------------

KNOWN = {"volumen", "musica", "nav_abrir"}


def test_build_a_chain_of_steps():
    rule = rules.build("08:00 | volumen 15 > abrir https://music.youtube.com/x > esperar 5 > avisar Suena {{paso 2}}", NOW, KNOWN)

    assert [s["tool"] for s in rule["steps"]] == ["volumen", "nav_abrir", "esperar", "avisar"]
    assert rules.describe(rule).endswith("→ volumen 15 > nav_abrir https://music.youtube.com/x > esperar 5 > avisar: Suena {{paso 2}}")


def test_build_from_structured_input():
    rule = rules.build_from("Despertador", "lun-vie 08:00", [{"tool": "volumen", "args": "15"}], NOW, KNOWN, layout={"1": {"x": 10, "y": 20}})

    assert rule["name"] == "Despertador" and rule["layout"] == {"1": {"x": 10, "y": 20}}
    assert rule["next_run"] == "2026-10-23T08:00:00"


@pytest.mark.parametrize("name", ["", "x" * 61])
def test_build_from_refuses_bad_names(name):
    with pytest.raises(ValueError):
        rules.build_from(name, "08:00", [{"tool": "volumen", "args": "15"}], NOW, KNOWN)


def test_build_refuses_a_reference_to_a_later_step():
    with pytest.raises(ValueError, match="paso"):
        rules.build("08:00 | avisar {{paso 2}} > volumen 10", NOW, KNOWN)


@pytest.mark.parametrize("text", ["23:00 | volumen mucho", "23:00 | musica bailar", "23:00 | esperar 90", "23:00 | esperar cinco", "23:00 | avisar"])
def test_the_built_in_steps_are_validated_when_saving(text):
    with pytest.raises(ValueError):
        rules.build(text, NOW, KNOWN)


def test_migrate_an_old_rule():
    old = {"id": 4, "when": {"kind": "daily", "time": "23:00", "days": [0]}, "do": {"kind": "volumen", "arg": "20"}, "enabled": True, "next_run": "x"}

    new = rules.migrate(old)

    assert "do" not in new and new["steps"] == [{"tool": "volumen", "args": "20"}] and new["name"] == "volumen 20" and new["id"] == 4
    assert rules.migrate(new) == new  # idempotente


def test_view_has_name_and_steps_and_keeps_the_old_keys():
    rule = {**rules.build("23:00 | volumen 20", NOW), "id": 1}

    shown = rules.view(rule)

    assert shown["name"] == "volumen 20" and shown["steps"] == [{"tool": "volumen", "args": "20", "text": "volumen 20"}]
    assert shown["do"] == {"kind": "volumen", "arg": "20"} and shown["when"] == "Todos los días a las 23:00"
