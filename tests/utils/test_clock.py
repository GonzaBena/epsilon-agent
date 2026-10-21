from datetime import datetime, timedelta, timezone

from epsilon.utils import clock

MOMENT = datetime(2026, 10, 21, 14, 32, tzinfo=timezone(timedelta(hours=-3)))


def test_today_text_is_in_spanish():
    assert clock.today_text(MOMENT) == "miércoles 21 de octubre de 2026"


def test_now_text_has_time_and_zone():
    assert clock.now_text(MOMENT) == "miércoles 21 de octubre de 2026, 14:32 (UTC-03:00)"


def test_now_without_argument_uses_the_system_clock():
    assert clock.now().tzinfo is not None
    assert clock.today_text()
