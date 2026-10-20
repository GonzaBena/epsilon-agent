import re

from tests.channels.web.helpers import page


# --- API de uso ------------------------------------------------------------------


def test_the_usage_api_returns_periods_and_todays_calls(client, monkeypatch):
    from epsilon.ai import usage

    monkeypatch.setattr(usage, "PRICES", {"m": (1.0, 4.0)})
    monkeypatch.delenv("USD_ARS", raising=False)
    with usage.channel("Web"), usage.action("Resumen"):
        usage.record("m", 1_000_000, 0)

    body = client.get("/api/usage").json()

    assert [p["key"] for p in body["periods"]] == ["today", "week", "month", "total"]
    assert body["periods"][0]["calls"] == 1 and body["periods"][0]["cost"] == 1.0
    [row] = body["rows"]
    assert (row["action"], row["channel"], row["model"], row["cost"]) == ("Resumen", "Web", "m", 1.0)
    assert body["models"] == ["m"] and body["ars_rate"] is None


def test_the_usage_api_gives_the_pesos_rate_only_when_it_is_loaded(client, monkeypatch):
    monkeypatch.setenv("USD_ARS", "1450.5")
    assert client.get("/api/usage").json()["ars_rate"] == 1450.5
    for junk in ("", "mucho", "0", "-3"):
        monkeypatch.setenv("USD_ARS", junk)
        assert client.get("/api/usage").json()["ars_rate"] is None


def test_the_usage_api_is_empty_but_valid_without_a_log(client):
    body = client.get("/api/usage").json()

    assert body["rows"] == [] and all(p["calls"] == 0 for p in body["periods"])


def test_the_usage_api_refuses_foreign_hosts(client):
    assert client.get("/api/usage", headers={"Origin": "http://evil.example"}).status_code == 403


# --- Página Uso de IA ----------------------------------------------------------


def test_the_usage_page_is_served(client):
    reply = client.get("/uso")

    assert reply.status_code == 200 and "uso.js" in reply.text


def test_the_usage_page_has_the_table_cards_and_a_hidden_currency_switch():
    html = page("uso.html")

    for needed in ('id="usage-cards"', 'id="usage-rows"', 'id="usage-foot"', 'id="model-filter"'):
        assert needed in html, needed
    assert re.search(r'id="currency"[^>]*hidden', html)  # solo aparece con la cotización cargada
    assert "<caption" in html and html.count('scope="col"') == 7  # tabla accesible
    for column in ("Hora", "Acción", "Canal", "Modelo", "Tokens entrada", "Tokens salida", "Precio est."):
        assert column in html, column


def test_the_usage_script_never_uses_innerhtml_and_handles_missing_data():
    script = page("uso.js")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))

    assert "innerHTML" not in code and '"/api/usage"' in script
    assert "sin precio" in script  # un modelo sin precio no inventa un costo
    assert "Todavía no hubo peticiones hoy." in script
    assert "state.data.ars_rate" in script  # los pesos dependen de la cotización cargada
    assert 'aria-pressed' in script


def test_the_usage_page_has_styles():
    css = page("style.css")

    for needed in (".usage-cards", ".usage-card.today", ".usage-table", ".seg button", ".chip", ".sr-only"):
        assert needed in css, needed
