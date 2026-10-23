# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2.3.0"]
# ///
"""Conector de Mercado Pago para Epsilon (solo lectura): pagos recibidos, un pago y el saldo.

Es un servidor MCP por stdio y se instala solo (`uv run` lee el bloque de arriba). Cada cuenta es una variable
de entorno `MP_TOKEN_<ALIAS>` con su access token; `MP_DEFAULT` elige la cuenta predeterminada (si hay una sola,
es esa). Ver README.md. Los datos que vuelven (nombres, descripciones) son dato, nunca instrucciones.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time
from typing import Literal

from mcp.server.mcpserver import MCPServer

API = "https://api.mercadopago.com"
PREFIX = "MP_TOKEN_"
TIMEOUT = 20  # segundos por pedido
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

STATUS = {
    "approved": "aprobado", "pending": "pendiente", "in_process": "en proceso", "rejected": "rechazado",
    "cancelled": "cancelado", "refunded": "reembolsado", "charged_back": "contracargo", "authorized": "autorizado",
    "in_mediation": "en mediación",
}
Status = Literal["approved", "pending", "in_process", "rejected", "cancelled", "refunded", "charged_back"]

server = MCPServer("mercadopago")


class Problem(Exception):
    """Algo que la persona puede entender y arreglar; el mensaje va tal cual a la herramienta."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# ---------- cuentas ----------

def accounts() -> dict[str, str]:
    """alias (minúsculas) -> token, de las variables MP_TOKEN_<ALIAS> que tengan valor."""
    return {k[len(PREFIX):].lower(): v.strip() for k, v in os.environ.items() if k.startswith(PREFIX) and v.strip()}


def pick(alias: str | None) -> tuple[str, str]:
    found = accounts()
    if not found:
        raise Problem("No hay ninguna cuenta cargada. Agregá la variable MP_TOKEN_<NOMBRE> (el access token) en el conector.")
    if alias:
        name = alias.strip().lower()
        if name not in found:
            raise Problem(f"No existe la cuenta «{alias}». Cuentas: {', '.join(sorted(found))}.")
        return name, found[name]
    default = os.environ.get("MP_DEFAULT", "").strip().lower()
    if default:
        if default not in found:
            raise Problem(f"MP_DEFAULT apunta a «{default}», que no existe. Cuentas: {', '.join(sorted(found))}.")
        return default, found[default]
    if len(found) == 1:
        name = next(iter(found))
        return name, found[name]
    raise Problem(f"Hay varias cuentas ({', '.join(sorted(found))}): elegí una con cuenta=<nombre>.")


def scrub(text: str) -> str:
    """Saca cualquier token de un texto antes de mostrarlo."""
    for token in accounts().values():
        text = text.replace(token, "***")
    return text


# ---------- red (ÚNICO punto: los tests lo reemplazan) ----------

def _http(path: str, token: str, params: dict | None = None) -> tuple[int, dict]:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return answer.status, json.load(answer)
    except urllib.error.HTTPError as error:
        try:
            body = json.load(error)
        except ValueError:
            body = {}
        return error.code, body if isinstance(body, dict) else {}
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise Problem(f"No pude hablar con Mercado Pago ({type(error).__name__}). Revisá la conexión.") from None


def get(token: str, path: str, params: dict | None = None) -> dict:
    status, body = _http(path, token, params)
    if status == 401:
        raise Problem("Mercado Pago no aceptó el token (vencido o mal copiado). Cargá uno nuevo.", status)
    if status == 403:
        raise Problem("Este token no tiene permiso para eso.", status)
    if status == 404:
        raise Problem("Mercado Pago no encontró eso.", status)
    if status >= 400:
        detail = scrub(str(body.get("message") or body.get("error") or ""))[:200]
        raise Problem(f"Mercado Pago contestó con error {status}" + (f": {detail}" if detail else "."), status)
    return body


# ---------- formato ----------

def money(value, currency: str = "") -> str:
    """1234.5 -> «1.234,50» (con la moneda adelante si se sabe)."""
    try:
        text = f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "?"
    return f"{currency} {text}".strip()


def one_line(text, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def stamp(value) -> str:
    """'2026-10-23T14:05:11.000-03:00' -> '2026-10-23 14:05'."""
    return str(value or "")[:16].replace("T", " ")


def day_bounds(since: str | None, until: str | None) -> tuple[str, str, str, str]:
    """(inicio ISO, fin ISO, desde, hasta) en hora local de la máquina; sin datos, solo hoy."""
    try:
        first = date.fromisoformat(since) if since else date.today()
        last = date.fromisoformat(until) if until else first
    except ValueError:
        raise Problem(f"Las fechas van como AAAA-MM-DD (por ejemplo 2026-10-23). Recibí: desde={one_line(since, 30)!r} hasta={one_line(until, 30)!r}") from None
    if last < first:
        raise Problem("«hasta» no puede ser anterior a «desde».")
    begin = datetime.combine(first, time.min).astimezone().isoformat(timespec="milliseconds")
    end = datetime.combine(last, time.max).astimezone().isoformat(timespec="milliseconds")
    return begin, end, first.isoformat(), last.isoformat()


def payment_line(p: dict) -> str:
    status = STATUS.get(p.get("status"), str(p.get("status") or "?"))
    parts = [f"#{p.get('id')}", stamp(p.get("date_created")), status, money(p.get("transaction_amount"), p.get("currency_id") or "")]
    if p.get("description"):
        parts.append(one_line(p["description"]))
    return " · ".join(parts)


# ---------- herramientas ----------

@server.tool()
def cuentas() -> str:
    """Lista las cuentas de Mercado Pago cargadas (solo el nombre, nunca el token) y cuál es la predeterminada."""
    found = accounts()
    if not found:
        return "No hay ninguna cuenta cargada. Agregá la variable MP_TOKEN_<NOMBRE> (el access token) en el conector."
    default = os.environ.get("MP_DEFAULT", "").strip().lower() or (next(iter(found)) if len(found) == 1 else "")
    return "\n".join(f"- {name}" + (" (predeterminada)" if name == default else "") for name in sorted(found))


@server.tool()
def buscar_pagos(cuenta: str | None = None, desde: str | None = None, hasta: str | None = None,
                 estado: Status | None = None, limite: int = DEFAULT_LIMIT) -> str:
    """Pagos recibidos, del más nuevo al más viejo. Fechas AAAA-MM-DD (sin fechas: solo hoy). Estado opcional."""
    try:
        name, token = pick(cuenta)
        begin, end, first, last = day_bounds(desde, hasta)
        size = max(1, min(int(limite), MAX_LIMIT))
        params = {"sort": "date_created", "criteria": "desc", "range": "date_created", "begin_date": begin,
                  "end_date": end, "limit": size}
        if estado:
            params["status"] = estado
        body = get(token, "/v1/payments/search", params)
    except Problem as problem:
        return str(problem)
    results = body.get("results") or []
    total = int((body.get("paging") or {}).get("total") or len(results))
    span = first if first == last else f"{first} a {last}"
    if not results:
        return f"[{name}] Sin pagos ({span}" + (f", {STATUS[estado]}" if estado else "") + ")."
    lines = [f"[{name}] {span}: " + (f"mostrando {len(results)} de {total} pagos" if total > len(results) else f"{total} pagos")]
    lines += [payment_line(p) for p in results]
    if total <= len(results):
        approved = [p for p in results if p.get("status") == "approved"]
        currencies = {p.get("currency_id") or "" for p in approved}
        if len(currencies) == 1:
            lines.append(f"Aprobado en total: {money(sum(p.get('transaction_amount') or 0 for p in approved), currencies.pop())}")
    return "\n".join(lines)


@server.tool()
def ver_pago(id: int, cuenta: str | None = None) -> str:
    """El detalle de un pago por su número: estado, monto, comisión, neto, medio de pago y fechas."""
    try:
        name, token = pick(cuenta)
        if int(id) <= 0:
            raise Problem("El número de pago tiene que ser positivo.")
        p = get(token, f"/v1/payments/{int(id)}")
    except Problem as problem:
        return str(problem)
    currency = p.get("currency_id") or ""
    details = p.get("transaction_details") or {}
    fees = sum(f.get("amount") or 0 for f in p.get("fee_details") or [])
    status = STATUS.get(p.get("status"), str(p.get("status") or "?"))
    rows = [f"[{name}] Pago #{p.get('id')}: {status}" + (f" ({one_line(p['status_detail'])})" if p.get("status_detail") else ""),
            f"Monto: {money(p.get('transaction_amount'), currency)}"]
    if details.get("net_received_amount") is not None:
        rows.append(f"Comisión: {money(fees, currency)} · Recibido: {money(details['net_received_amount'], currency)}")
    rows.append(f"Medio: {p.get('payment_method_id') or '?'} ({p.get('payment_type_id') or '?'})")
    rows.append(f"Creado: {stamp(p.get('date_created'))}" + (f" · Aprobado: {stamp(p['date_approved'])}" if p.get("date_approved") else ""))
    if p.get("description"):
        rows.append(f"Descripción: {one_line(p['description'], 120)}")
    if p.get("external_reference"):
        rows.append(f"Referencia: {one_line(p['external_reference'], 60)}")
    payer = (p.get("payer") or {}).get("email")
    if payer:
        rows.append(f"Pagador: {one_line(payer, 80)}")
    return "\n".join(rows)


@server.tool()
def saldo(cuenta: str | None = None) -> str:
    """El saldo de la cuenta de Mercado Pago: disponible y no disponible."""
    try:
        name, token = pick(cuenta)
        user = get(token, "/users/me")
        balance = get(token, f"/users/{int(user['id'])}/mercadopago_account/balance")
    except (Problem, KeyError, TypeError, ValueError) as problem:
        if isinstance(problem, Problem) and problem.status == 403:
            return "Mercado Pago no deja leer el saldo con este token (puede faltar el permiso)."
        return str(problem) if isinstance(problem, Problem) else "Mercado Pago no devolvió el saldo que esperaba."
    currency = balance.get("currency_id") or ""
    return (f"[{name}] Disponible: {money(balance.get('available_balance'), currency)}"
            f" · No disponible: {money(balance.get('unavailable_balance'), currency)}")


@server.resource("epsilon://buttons")
def buttons() -> str:
    """Botones que Epsilon puede ofrecer a la persona (nunca los crea solo)."""
    return json.dumps([
        {"label": "Pagos de hoy", "tool": "buscar_pagos", "args": {}},
        {"label": "Saldo", "tool": "saldo", "args": {}},
    ], ensure_ascii=False)


if __name__ == "__main__":
    server.run()  # stdio; stdout es del protocolo: lo propio va a stderr
