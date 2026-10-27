import asyncio
import csv
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "conectores" / "mercadopago" / "mercadopago_mcp.py"


@pytest.fixture
def mp(monkeypatch):
    for key in [k for k in os.environ if k.startswith(("MP_TOKEN_", "MP_DEFAULT"))]:
        monkeypatch.delenv(key)
    spec = importlib.util.spec_from_file_location("mercadopago_mcp", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó hablar con Mercado Pago de verdad")

    monkeypatch.setattr(module, "_http", forbidden)  # el ÚNICO punto de red
    return module


def fake_http(mp, monkeypatch, answers):
    """answers: ruta -> (status, cuerpo). Anota cada pedido en la lista que devuelve."""
    calls = []

    def fake(path, token, params=None):
        calls.append((path, token, params))
        return answers[path]

    monkeypatch.setattr(mp, "_http", fake)
    return calls


PAYMENT = {"id": 111, "status": "approved", "status_detail": "accredited", "date_created": "2026-10-23T14:05:11.000-03:00",
           "date_approved": "2026-10-23T14:05:12.000-03:00", "transaction_amount": 15000.5, "currency_id": "ARS",
           "description": "Remera\nroja " + "x" * 200, "payment_method_id": "visa", "payment_type_id": "credit_card",
           "external_reference": "pedido-7", "payer": {"email": "ana@example.com"},
           "fee_details": [{"amount": 700.25}], "transaction_details": {"net_received_amount": 14300.25}}


def test_script_declares_its_own_dependencies():
    head = SCRIPT.read_text().split('"""')[0]

    assert "# /// script" in head and 'dependencies = ["mcp' in head


def test_server_defines_the_read_only_tools_and_buttons_that_fit_them(mp):
    found = {t.name: t.input_schema for t in asyncio.run(mp.server.list_tools())}

    assert set(found) == {"cuentas", "buscar_pagos", "exportar_pagos", "ver_pago", "saldo"}
    for button in json.loads(mp.buttons()):
        assert button["tool"] in found and not found[button["tool"]].get("required")


def test_accounts_come_from_env_and_the_default_rules(mp, monkeypatch):
    assert "No hay ninguna cuenta" in mp.cuentas()
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "tok-negocio")
    assert mp.pick(None) == ("negocio", "tok-negocio")  # una sola: esa
    monkeypatch.setenv("MP_TOKEN_PERSONAL", "tok-personal")
    with pytest.raises(mp.Problem, match="cuenta=<nombre>"):
        mp.pick(None)
    monkeypatch.setenv("MP_DEFAULT", "Personal")
    assert mp.pick(None) == ("personal", "tok-personal")
    assert mp.pick("NEGOCIO") == ("negocio", "tok-negocio")
    with pytest.raises(mp.Problem, match="No existe la cuenta"):
        mp.pick("otra")
    assert "personal (predeterminada)" in mp.cuentas() and "tok-" not in mp.cuentas()


def test_search_asks_for_today_in_local_time_and_formats_the_answer(mp, monkeypatch):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "tok-negocio")
    calls = fake_http(mp, monkeypatch, {"/v1/payments/search": (200, {
        "paging": {"total": 2}, "results": [PAYMENT, {**PAYMENT, "id": 112, "status": "pending", "transaction_amount": 100}]})})

    text = mp.buscar_pagos(estado="approved", limite=500)

    path, token, params = calls[0]
    assert token == "tok-negocio" and params["status"] == "approved" and params["limit"] == mp.MAX_LIMIT
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT00:00:00\.000[+-]\d\d:\d\d", params["begin_date"])  # hora local con su zona
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT23:59:59\.999[+-]\d\d:\d\d", params["end_date"])
    assert params["begin_date"][:10] == params["end_date"][:10]  # sin fechas: solo hoy
    assert "[negocio]" in text and "2 pagos" in text
    assert "#111 · 2026-10-23 14:05 · aprobado · ARS 15.000,50" in text
    assert "#112" in text and "pendiente" in text
    assert "Aprobado en total: ARS 15.000,50" in text
    assert "x" * 100 not in text and "\n" not in PAYMENT_LINE_DESC(text)  # la descripción se corta y va en una línea


def PAYMENT_LINE_DESC(text):
    return text.splitlines()[1]


def test_search_says_when_there_is_more_than_what_it_shows_and_when_there_is_nothing(mp, monkeypatch):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    fake_http(mp, monkeypatch, {"/v1/payments/search": (200, {"paging": {"total": 90}, "results": [PAYMENT]})})
    shown = mp.buscar_pagos(desde="2026-10-01", hasta="2026-10-23")
    assert "mostrando 1 de 90 pagos" in shown and "Aprobado en total" not in shown
    fake_http(mp, monkeypatch, {"/v1/payments/search": (200, {"paging": {"total": 0}, "results": []})})
    assert "Sin pagos" in mp.buscar_pagos(desde="2026-10-23")


@pytest.mark.parametrize("since, until, message", [("23/10/2026", None, "AAAA-MM-DD"), ("2026-10-23", "2026-10-01", "anterior")])
def test_search_rejects_bad_dates_without_calling_mercado_pago(mp, monkeypatch, since, until, message):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")

    text = mp.buscar_pagos(desde=since, hasta=until)  # _http prohibido: si llamara, fallaría

    assert message in text
    if since == "23/10/2026":
        assert "desde='23/10/2026'" in text  # dice qué recibió


def test_one_payment_shows_fee_net_and_context(mp, monkeypatch):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    calls = fake_http(mp, monkeypatch, {"/v1/payments/111": (200, PAYMENT)})

    text = mp.ver_pago(111)

    assert calls[0][0] == "/v1/payments/111"
    assert "Pago #111: aprobado (accredited)" in text
    assert "Comisión: ARS 700,25 · Recibido: ARS 14.300,25" in text
    assert "visa (credit_card)" in text and "Referencia: pedido-7" in text and "Pagador: ana@example.com" in text
    assert "Aprobado: 2026-10-23 14:05" in text
    assert "positivo" in mp.ver_pago(0)


def test_balance_uses_the_user_id_and_explains_a_missing_permission(mp, monkeypatch):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    calls = fake_http(mp, monkeypatch, {"/users/me": (200, {"id": 42}), "/users/42/mercadopago_account/balance": (
        200, {"currency_id": "ARS", "available_balance": 2500, "unavailable_balance": 10.5})})

    assert mp.saldo() == "[negocio] Disponible: ARS 2.500,00 · No disponible: ARS 10,50"
    assert [c[0] for c in calls] == ["/users/me", "/users/42/mercadopago_account/balance"]

    fake_http(mp, monkeypatch, {"/users/me": (200, {"id": 42}), "/users/42/mercadopago_account/balance": (403, {})})
    assert "no deja leer el saldo" in mp.saldo()


@pytest.mark.parametrize("status, body, expected", [
    (401, {}, "no aceptó el token"), (404, {}, "no encontró"), (500, {"message": "falló"}, "error 500: falló")])
def test_http_errors_become_clear_spanish_messages(mp, monkeypatch, status, body, expected):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    fake_http(mp, monkeypatch, {"/v1/payments/5": (status, body)})

    assert expected in mp.ver_pago(5)


def test_a_token_never_shows_in_an_error(mp, monkeypatch):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "APP_USR-secreto-123")
    fake_http(mp, monkeypatch, {"/v1/payments/5": (400, {"message": "bad token APP_USR-secreto-123"})})

    text = mp.ver_pago(5)

    assert "APP_USR-secreto-123" not in text and "***" in text


def test_a_network_failure_is_reported_without_a_traceback(mp, monkeypatch):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")

    def down(path, token, params=None):
        raise mp.Problem("No pude hablar con Mercado Pago (URLError). Revisá la conexión.")

    monkeypatch.setattr(mp, "_http", down)

    assert "No pude hablar con Mercado Pago" in mp.saldo()


def paged_http(mp, monkeypatch, payments):
    """Un /v1/payments/search que respeta limit y offset, como el de verdad."""
    calls = []

    def fake(path, token, params=None):
        calls.append(params)
        start = params["offset"]
        return 200, {"paging": {"total": len(payments)}, "results": payments[start:start + params["limit"]]}

    monkeypatch.setattr(mp, "_http", fake)
    return calls


def test_export_pages_through_everything_and_writes_the_csv(mp, monkeypatch, tmp_path):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    monkeypatch.setenv("MP_EXPORT_DIR", str(tmp_path))
    payments = [{**PAYMENT, "id": n} for n in range(1, 121)]  # 120: tres páginas de 50
    payments[0]["description"] = "=HYPERLINK(1)"
    calls = paged_http(mp, monkeypatch, payments)

    text = mp.exportar_pagos(desde="2026-10-01", hasta="2026-10-31", estado="approved")

    assert [c["offset"] for c in calls] == [0, 50, 100] and calls[0]["status"] == "approved"
    assert "120 pagos guardados en mp_negocio_2026-10-01_2026-10-31.csv" in text and "Aprobado en total" in text
    rows = list(csv.reader((tmp_path / "mp_negocio_2026-10-01_2026-10-31.csv").open(encoding="utf-8")))
    assert rows[0] == mp.EXPORT_FIELDS and len(rows) == 121
    assert rows[1] == ["1", "2026-10-23 14:05", "approved", "15000.5", "ARS", "700.25", "14300.25", "visa", "'=HYPERLINK(1)"]
    assert "x" * 200 not in rows[2][-1] and "\n" not in rows[2][-1]


def test_export_never_overwrites_and_needs_a_folder(mp, monkeypatch, tmp_path):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    paged_http(mp, monkeypatch, [PAYMENT])

    assert "MP_EXPORT_DIR" in mp.exportar_pagos(desde="2026-10-23")  # sin carpeta no escribe
    monkeypatch.setenv("MP_EXPORT_DIR", str(tmp_path / "no-existe"))
    assert "no es una carpeta" in mp.exportar_pagos(desde="2026-10-23")
    monkeypatch.setenv("MP_EXPORT_DIR", str(tmp_path))
    mp.exportar_pagos(desde="2026-10-23")
    again = mp.exportar_pagos(desde="2026-10-23")

    assert "mp_negocio_2026-10-23_2.csv" in again
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mp_negocio_2026-10-23.csv", "mp_negocio_2026-10-23_2.csv"]


def test_export_refuses_too_many_and_writes_nothing_when_there_are_none(mp, monkeypatch, tmp_path):
    monkeypatch.setenv("MP_TOKEN_NEGOCIO", "t")
    monkeypatch.setenv("MP_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(mp, "MAX_EXPORT", 10)
    paged_http(mp, monkeypatch, [PAYMENT] * 11)
    assert "período más corto" in mp.exportar_pagos(desde="2026-10-01", hasta="2026-10-31")
    paged_http(mp, monkeypatch, [])
    assert "No hay cobros en ese período" in mp.exportar_pagos(desde="2026-10-01") and "no se creó ningún archivo" in mp.exportar_pagos(desde="2026-10-01", estado="approved")
    assert list(tmp_path.iterdir()) == []
