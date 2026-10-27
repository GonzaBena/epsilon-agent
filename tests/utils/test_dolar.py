import httpx
import pytest

from epsilon.settings import config
from epsilon.utils import dolar

REAL_GET = dolar._get  # el conftest bloquea dolar._get en cada test; esta es la verdadera

DATA = [
    {"casa": "blue", "compra": 1480, "venta": 1500},
    {"casa": "oficial", "compra": 1400.5, "venta": 1450},
    {"casa": "cripto", "compra": 1, "venta": 2},
]


def test_text_lists_known_houses_in_order_with_argentine_format(monkeypatch):
    monkeypatch.setattr(dolar, "_get", lambda: DATA)

    assert dolar.text() == (
        "**Dólar hoy** (DolarApi)\n\n"
        "| Casa | Compra | Venta |\n"
        "|---|---:|---:|\n"
        "| Oficial | $1.400,5 | $1.450 |\n"
        "| Blue | $1.480 | $1.500 |"
    )


def test_second_call_uses_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(dolar, "_get", lambda: calls.append(1) or DATA)

    dolar.text()
    dolar.text()

    assert len(calls) == 1


def test_cache_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(dolar, "_get", lambda: calls.append(1) or DATA)
    now = [1000.0]
    monkeypatch.setattr(dolar.time, "monotonic", lambda: now[0])

    dolar.text()
    now[0] += config.number("DOLLAR_CACHE_SECONDS") + 1
    dolar.text()

    assert len(calls) == 2


def test_unknown_houses_only_is_an_error(monkeypatch):
    monkeypatch.setattr(dolar, "_get", lambda: [{"casa": "cripto", "compra": 1, "venta": 2}])

    with pytest.raises(ValueError):
        dolar.text()


def test_missing_price_shows_a_dash(monkeypatch):
    monkeypatch.setattr(dolar, "_get", lambda: [{"casa": "tarjeta", "compra": None, "venta": 2000}])

    assert "| Tarjeta | — | $2.000 |" in dolar.text()


def test_network_failure_gives_a_clear_message_without_details(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("https://secreto.example/falla")

    monkeypatch.setattr(dolar.httpx, "get", boom)

    with pytest.raises(ValueError) as error:
        REAL_GET()

    assert "secreto" not in str(error.value)
    assert "dólar" in str(error.value)
