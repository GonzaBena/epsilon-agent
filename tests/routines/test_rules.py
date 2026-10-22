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
def test_in_a_while_is_a_single_run_with_the_seconds_dropped(text, minutes):
    when = rules.parse_when(text, NOW)

    assert when["kind"] == "once"
    assert datetime.fromisoformat(when["at"]) == (NOW + timedelta(minutes=minutes)).replace(second=0)


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

    assert rule == {"when": {"kind": "daily", "time": "23:00", "days": list(range(7))}, "do": {"kind": "volumen", "arg": "20"},
                    "enabled": True, "next_run": "2026-10-22T23:00:00"}


@pytest.mark.parametrize("text", ["", "23:00", "23:00 |", "| volumen 20", "volumen 20", "23:00 | "])
def test_build_needs_both_halves(text):
    with pytest.raises(ValueError):
        rules.build(text, NOW)


def test_the_text_can_hold_a_bar_in_the_notice():
    assert rules.build("en 5m | avisar a | b", NOW)["do"]["arg"] == "a | b"


def test_descriptions_read_like_people_talk():
    assert rules.describe(rules.build("23:00 | volumen 20", NOW)) == "todos los días a las 23:00 → volumen 20"
    assert rules.describe(rules.build("lun-vie 09:00 | avisar Standup", NOW)) == "de lunes a viernes a las 09:00 → avisar: Standup"
    assert rules.describe(rules.build("finde 10:00 | musica play", NOW)) == "sábado y domingo a las 10:00 → musica play"
    assert rules.describe(rules.build("lun,mié 10:00 | musica", NOW)) == "lunes, miércoles a las 10:00 → musica"
    assert rules.describe(rules.build("en 30m | avisar pizza", NOW)) == "una vez, 22/10 a las 15:00 → avisar: pizza"
