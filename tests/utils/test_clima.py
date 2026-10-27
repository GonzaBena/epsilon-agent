import httpx
import pytest

from epsilon.settings import config
from epsilon.utils import clima

REAL_GET = clima._get  # el conftest bloquea clima._get en cada test; esta es la verdadera


def day(date, low, high, desc):
    hourly = [{"weatherDesc": [{"value": "x"}]}] * 4 + [{"lang_es": [{"value": desc}]}]
    return {"date": date, "mintempC": low, "maxtempC": high, "hourly": hourly}


DATA = {
    "current_condition": [
        {"temp_C": "18", "FeelsLikeC": "17", "humidity": "60", "windspeedKmph": "12", "winddir16Point": "SO",
         "lang_es": [{"value": "Parcialmente nublado"}]}
    ],
    "nearest_area": [{"areaName": [{"value": "Rosario"}], "country": [{"value": "Argentina"}]}],
    "weather": [day("2026-10-21", "12", "21", "Soleado"), day("2026-10-22", "13", "23", "Nublado"),
                day("2026-10-23", "11", "19", "Lluvia")],
}


def test_text_has_the_current_weather_and_a_forecast_table(monkeypatch):
    monkeypatch.setattr(clima, "_get", lambda city: DATA)

    assert clima.text("Rosario") == (
        "**Clima en Rosario, Argentina**\n\n"
        "Ahora: 18 °C (sensación 17 °C) · Parcialmente nublado  \n"
        "Humedad 60 % · Viento 12 km/h SO\n\n"
        "| Día | Mín | Máx | Estado |\n"
        "|---|---:|---:|---|\n"
        "| Hoy | 12 °C | 21 °C | Soleado |\n"
        "| Mañana | 13 °C | 23 °C | Nublado |\n"
        "| Viernes | 11 °C | 19 °C | Lluvia |"
    )


def test_same_city_uses_the_cache_whatever_the_case(monkeypatch):
    calls = []
    monkeypatch.setattr(clima, "_get", lambda city: calls.append(city) or DATA)

    clima.text("Rosario")
    clima.text("rosario")

    assert len(calls) == 1


def test_cache_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(clima, "_get", lambda city: calls.append(city) or DATA)
    now = [1000.0]
    monkeypatch.setattr(clima.time, "monotonic", lambda: now[0])

    clima.text("Rosario")
    now[0] += config.number("WEATHER_CACHE_SECONDS") + 1
    clima.text("Rosario")

    assert len(calls) == 2


def test_an_answer_that_is_not_a_forecast_means_unknown_city(monkeypatch):
    monkeypatch.setattr(clima, "_get", lambda city: {"error": "no"})

    with pytest.raises(ValueError, match="Zzz"):
        clima.text("Zzz")


@pytest.mark.parametrize("city", ["Rosario", "Salta, Argentina", "Río Cuarto", "San Martín de los Andes", "L'Aquila"])
def test_valid_cities(city):
    assert clima.valid_city(city) == city


@pytest.mark.parametrize("city", ["", "   ", "123", "a\nb=c", "x" * 61, "../etc", "a/b", "a?b#c"])
def test_invalid_cities(city):
    with pytest.raises(ValueError):
        clima.valid_city(city)


def test_city_is_quoted_in_the_url_and_failures_hide_it(monkeypatch):
    seen = []

    def boom(url, **kwargs):
        seen.append(url)
        raise httpx.ConnectError("https://secreto.example/falla")

    monkeypatch.setattr(clima.httpx, "get", boom)

    with pytest.raises(ValueError) as error:
        REAL_GET("Río Cuarto")

    assert seen == ["https://wttr.in/R%C3%ADo%20Cuarto?format=j1&lang=es"]
    assert "secreto" not in str(error.value) and "Río Cuarto" in str(error.value)
