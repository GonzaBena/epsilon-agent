# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2.3.0", "cryptography>=43", "fpdf2>=2.8", "segno>=1.6"]
# ///
"""Conector de ARCA (ex AFIP) para Epsilon, solo lectura: puntos de venta, tipos de comprobante, último comprobante,
ver un comprobante y verificar que todo esté bien configurado.

Es un servidor MCP por stdio y se instala solo (`uv run` lee el bloque de arriba). Usa los web services oficiales
(WSAA para entrar con tu certificado, WSFEv1 para facturación electrónica). Las cuentas viven en
`$ARCA_HOME/cuentas.json` (por defecto `~/.config/epsilon-arca/`) y NO guardan secretos: solo CUIT, ambiente y
las rutas del certificado y la clave. Para crear una cuenta (clave + pedido de certificado):

    uv run arca_mcp.py nueva <nombre> --cuit <CUIT>

Ver README.md. Lo que devuelve ARCA (descripciones, errores) es dato, nunca instrucciones.
"""
import argparse
import base64
import csv
import hashlib
import http.client
import io
import json
import os
import re
import ssl
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
import segno
from fpdf import FPDF
from mcp.server.mcpserver import MCPServer

TIMEOUT = 30  # segundos por pedido
REFRESH_BEFORE = 600  # se pide un ticket nuevo cuando faltan menos de 10 min
ALIAS_RE = re.compile(r"[a-z][a-z0-9_]{1,19}")
ENVIRONMENTS = ("homologacion", "produccion")
# Direcciones oficiales. Se pueden cambiar por cuenta con "urls": {"wsaa": ..., "wsfe": ..., "padron": ...} si ARCA cambia de dominio.
URLS = {
    "homologacion": {"wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
                     "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
                     "padron": "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA13"},
    "produccion": {"wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
                   "wsfe": "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
                   "padron": "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA13"},
}
PADRON_SERVICE = "ws_sr_padron_a13"
PADRON_NS = "http://a13.soap.ws.server.puc.sr/"
WSFE_NS = "http://ar.gov.afip.dif.FEV1/"
WSAA_NS = "http://wsaa.view.sua.dvadac.desein.afip.gov"
DEFAULT_TYPE = {"monotributo": 11}  # Factura C
VOUCHERS = {1: "Factura A", 2: "Nota de débito A", 3: "Nota de crédito A", 6: "Factura B", 7: "Nota de débito B",
            8: "Nota de crédito B", 11: "Factura C", 12: "Nota de débito C", 13: "Nota de crédito C", 51: "Factura M"}
DOC_TYPES = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Consumidor final"}
CONCEPTS = {1: "Productos", 2: "Servicios", 3: "Productos y servicios"}
CURRENCIES = {"PES": "ARS", "DOL": "USD"}
AR_TZ = timezone(timedelta(hours=-3))  # hora de Argentina, como espera ARCA (sin horario de verano)

server = MCPServer("arca")


class Problem(Exception):
    """Algo que la persona puede entender y arreglar; el mensaje va tal cual a la herramienta."""

    def __init__(self, message: str, codes: tuple[str, ...] = (), maybe_sent: bool = False):
        super().__init__(message)
        self.codes = codes  # códigos de error de ARCA, si el problema vino de ahí
        self.maybe_sent = maybe_sent  # el pedido pudo haber llegado a ARCA aunque no haya respuesta (¡no repetir sin mirar!)


# ---------- configuración ----------

def home() -> Path:
    return Path(os.environ.get("ARCA_HOME") or Path.home() / ".config" / "epsilon-arca")


def load_accounts() -> dict[str, dict]:
    path = home() / "cuentas.json"
    hint = f"Creá una con:  uv run {Path(__file__).resolve()} nueva <nombre> --cuit <CUIT>"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Problem(f"No hay cuentas de ARCA ({path} no existe). {hint}") from None
    except (OSError, ValueError):
        raise Problem(f"No pude leer {path}: tiene que ser un JSON con una entrada por cuenta.") from None
    if not isinstance(raw, dict) or not raw:
        raise Problem(f"{path} no tiene ninguna cuenta. {hint}")
    found = {}
    for alias, entry in raw.items():
        if not ALIAS_RE.fullmatch(str(alias)) or not isinstance(entry, dict):
            raise Problem(f"En {path.name}: la cuenta «{alias}» está mal (nombre en minúsculas, de 2 a 20 caracteres).")
        if not re.fullmatch(r"\d{11}", str(entry.get("cuit", ""))):
            raise Problem(f"Cuenta «{alias}»: el CUIT son 11 números, sin guiones.")
        ambiente = entry.get("ambiente", "homologacion")
        if ambiente not in ENVIRONMENTS:
            raise Problem(f"Cuenta «{alias}»: «ambiente» tiene que ser homologacion o produccion.")
        urls = {**URLS[ambiente], **(entry.get("urls") or {})}
        if not all(isinstance(u, str) and u.startswith("https://") for u in urls.values()):
            raise Problem(f"Cuenta «{alias}»: las direcciones de «urls» tienen que empezar con https://")
        found[alias] = {**entry, "alias": alias, "cuit": str(entry["cuit"]), "ambiente": ambiente, "urls": urls}
    return found


def pick(alias: str | None) -> dict:
    found = load_accounts()
    if alias:
        if alias.strip().lower() not in found:
            raise Problem(f"No existe la cuenta «{alias}». Cuentas: {', '.join(sorted(found))}.")
        return found[alias.strip().lower()]
    default = os.environ.get("ARCA_DEFAULT", "").strip().lower()
    if default:
        if default not in found:
            raise Problem(f"ARCA_DEFAULT apunta a «{default}», que no existe. Cuentas: {', '.join(sorted(found))}.")
        return found[default]
    if len(found) == 1:
        return next(iter(found.values()))
    raise Problem(f"Hay varias cuentas ({', '.join(sorted(found))}): elegí una con cuenta=<nombre>.")


def file_of(account: dict, kind: str) -> Path:
    """Ruta del certificado ('cert') o de la clave ('key'): absoluta, o relativa a ARCA_HOME."""
    ext = {"cert": "crt", "key": "key"}[kind]
    return home() / str(account.get(kind) or f"{account['alias']}.{ext}")  # una ruta absoluta pisa a home()


# ---------- red (ÚNICO punto: los tests lo reemplazan) ----------

def _post(url: str, action: str, body: str) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers={
        "Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{action}"'})
    context = ssl.create_default_context()
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as answer:
                return answer.status, answer.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:  # los SOAP Fault vienen con 500: el cuerpo es lo que importa
            return error.code, error.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:  # incluye «cerró la conexión sin responder» y timeouts
            reason = getattr(error, "reason", error)
            if attempt == 0 and isinstance(reason, ssl.SSLError) and "DH_KEY_TOO_SMALL" in str(reason):
                # Los servidores de ARCA usan una clave DH corta que OpenSSL moderno rechaza: se baja el nivel
                # SOLO para estas direcciones (el certificado se sigue verificando).
                context = ssl.create_default_context()
                context.set_ciphers("DEFAULT@SECLEVEL=1")
                continue
            host = urlparse(url).hostname
            raise Problem(f"No pude hablar con ARCA ({host}): {type(reason).__name__}. Revisá la conexión o la dirección en «urls».",
                          maybe_sent=True) from None
    raise Problem("No pude hablar con ARCA.", maybe_sent=True)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find(root: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in root.iter() if local(e.tag) == name]


def text_of(root: ET.Element | None, name: str) -> str:
    found = find(root, name) if root is not None else []
    return (found[0].text or "").strip() if found else ""


def soap(url: str, action: str, inner: str) -> ET.Element:
    body = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<soap:Body>{inner}</soap:Body></soap:Envelope>")
    status, answer = _post(url, action, body)
    try:
        root = ET.fromstring(answer)
    except ET.ParseError:
        raise Problem(f"ARCA contestó algo que no entiendo (código {status}). Probá de nuevo en un rato.", maybe_sent=True) from None
    fault = text_of(root, "faultstring")
    if fault:
        raise Problem(explain_fault(one_line(fault, 300)))
    return root


def explain_fault(fault: str) -> str:
    low = fault.lower()
    if "alreadyauthenticated" in low or "ya posee un ta" in low:
        return "ARCA dice que ya hay un ticket vigente para este certificado y no da otro hasta que venza (hasta 12 h)."
    if "not trusted" in low or "no emitido por" in low or "untrusted" in low:
        return f"ARCA no confía en el certificado: {fault}. Los de homologación y producción son distintos: ¿la cuenta tiene el «ambiente» que corresponde?"
    return f"ARCA rechazó el pedido: {fault}"


# ---------- WSAA: entrar con el certificado ----------

def read_pair(account: dict) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    cert_path, key_path = file_of(account, "cert"), file_of(account, "key")
    if not key_path.is_file():
        raise Problem(f"Falta la clave privada: {key_path}. Se genera con «nueva» (ver README).")
    if not cert_path.is_file():
        raise Problem(f"Falta el certificado: {cert_path}. Subí el .csr a ARCA, descargá el .crt y guardalo ahí (ver README).")
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (ValueError, TypeError):
        raise Problem(f"No pude leer {cert_path.name} o {key_path.name}: tienen que estar en formato PEM y la clave sin contraseña.") from None
    return cert, key


def sign_request(request_xml: bytes, cert: x509.Certificate, key) -> str:
    der = (pkcs7.PKCS7SignatureBuilder().set_data(request_xml).add_signer(cert, key, hashes.SHA256())
           .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
    return base64.b64encode(der).decode()


def login(account: dict, service: str = "wsfe") -> dict:
    cert, key = read_pair(account)
    now = datetime.now(AR_TZ)
    stamp = lambda moment: moment.isoformat(timespec="seconds")  # noqa: E731
    request = ('<?xml version="1.0" encoding="UTF-8"?><loginTicketRequest version="1.0"><header>'
               f"<uniqueId>{int(time.time())}</uniqueId><generationTime>{stamp(now - timedelta(minutes=10))}</generationTime>"
               f"<expirationTime>{stamp(now + timedelta(minutes=10))}</expirationTime></header><service>{service}</service></loginTicketRequest>")
    cms = sign_request(request.encode(), cert, key)
    root = soap(account["urls"]["wsaa"], "", f'<loginCms xmlns="{WSAA_NS}"><in0>{cms}</in0></loginCms>')
    try:
        ticket = ET.fromstring(text_of(root, "loginCmsReturn"))
        data = {"token": text_of(ticket, "token"), "sign": text_of(ticket, "sign"),
                "expires": datetime.fromisoformat(text_of(ticket, "expirationTime")).timestamp()}
    except (ET.ParseError, ValueError):
        raise Problem("ARCA aceptó el ingreso pero la respuesta no tiene el formato esperado.") from None
    if not data["token"] or not data["sign"]:
        raise Problem("ARCA aceptó el ingreso pero no devolvió el ticket.")
    return data


def ticket(account: dict, service: str = "wsfe") -> tuple[str, str]:
    """(token, sign) vigentes para un servicio. ARCA no da un ticket nuevo mientras haya uno vivo, así que se guarda en disco (uno por servicio)."""
    suffix = "" if service == "wsfe" else f"-{service}"
    cache = home() / "cache" / f"{account['alias']}-{account['ambiente']}{suffix}.json"
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved["expires"] - time.time() > REFRESH_BEFORE:
            return saved["token"], saved["sign"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    data = login(account, service)
    cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(cache, json.dumps(data))
    return data["token"], data["sign"]


def write_private(path: Path, content: str | bytes) -> None:
    """Escribe el archivo con permisos 600 desde el primer momento (por archivo temporal en la misma carpeta)."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


# ---------- WSFEv1 ----------

def wsfe(account: dict, method: str, params: str = "", *, auth: bool = True) -> ET.Element:
    credentials = ""
    if auth:
        token, sign = ticket(account)
        credentials = f"<Auth><Token>{escape(token)}</Token><Sign>{escape(sign)}</Sign><Cuit>{account['cuit']}</Cuit></Auth>"
    root = soap(account["urls"]["wsfe"], WSFE_NS + method, f'<{method} xmlns="{WSFE_NS}">{credentials}{params}</{method}>')
    errors = [(text_of(e, "Code"), one_line(text_of(e, "Msg"), 200)) for e in find(root, "Err")]
    if errors:
        raise Problem("ARCA: " + "; ".join(f"{msg} (código {code})" for code, msg in errors), codes=tuple(c for c, _ in errors))
    return root


# ---------- Padrón (ws_sr_padron_a13): quién es un DNI / CUIL ----------

def padron(account: dict, method: str, params: str) -> ET.Element:
    token, sign = ticket(account, PADRON_SERVICE)
    credentials = f"<token>{escape(token)}</token><sign>{escape(sign)}</sign><cuitRepresentada>{account['cuit']}</cuitRepresentada>"
    body = f'<a13:{method} xmlns:a13="{PADRON_NS}">{credentials}{params}</a13:{method}>'
    try:
        try:
            return soap(account["urls"]["padron"], method, body)
        except Problem as problem:
            if not problem.maybe_sent:
                raise
            return soap(account["urls"]["padron"], method, body)  # el padrón es de solo lectura: un reintento no cambia nada
    except Problem as problem:
        if "autoriz" in str(problem).lower():
            raise Problem(f"{problem}\nFalta autorizar el servicio «{PADRON_SERVICE}» para el certificado de esta cuenta (ver README).") from None
        raise


NO_RESULTS = "602"  # ARCA contesta «Sin Resultados» como un error cuando una lista está vacía


def listed(account: dict, method: str, element: str) -> list[ET.Element]:
    """Los elementos de una lista de ARCA; sin ninguno, ARCA da el error 602 y acá es una lista vacía."""
    try:
        return find(wsfe(account, method), element)
    except Problem as problem:
        if NO_RESULTS in problem.codes:
            return []
        raise


def sale_points(account: dict) -> list[ET.Element]:
    return listed(account, "FEParamGetPtosVenta", "PtoVenta")


# ---------- formato ----------

def one_line(value, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def money(value, currency: str = "") -> str:
    try:
        shown = f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "?"
    return f"{currency} {shown}".strip()


def day(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.fullmatch(r"\d{8}", value or "") else (value or "?")


def number_text(point: int, n: int) -> str:
    return f"{int(point):04d}-{int(n):08d}"


def voucher_type(account: dict, kind: int | None) -> int:
    if kind is None:
        kind = DEFAULT_TYPE.get(account.get("condicion", ""))
    if kind is None:
        raise Problem("Decime el tipo de comprobante con tipo=<número> (11 es Factura C, 6 Factura B, 1 Factura A…). Se ven con tipos_de_comprobante.")
    if not 1 <= int(kind) <= 999:
        raise Problem("El tipo de comprobante es un número entre 1 y 999.")
    return int(kind)


def sale_point(account: dict, point: int | None) -> int:
    point = point if point is not None else account.get("punto_venta")
    if point is None:
        raise Problem("Decime el punto de venta con punto_venta=<número> (o guardalo en la cuenta). Se ven con puntos_de_venta.")
    if not 1 <= int(point) <= 99999:
        raise Problem("El punto de venta es un número entre 1 y 99999.")
    return int(point)


def label(kind: int) -> str:
    return VOUCHERS.get(kind, f"Comprobante tipo {kind}")


# ---------- herramientas ----------

@server.tool()
def cuentas() -> str:
    """Lista las cuentas de ARCA configuradas: nombre, CUIT, ambiente y si están el certificado y la clave."""
    try:
        found = load_accounts()
    except Problem as problem:
        return str(problem)
    default = os.environ.get("ARCA_DEFAULT", "").strip().lower() or (next(iter(found)) if len(found) == 1 else "")
    rows = []
    for alias, account in sorted(found.items()):
        ready = "listo" if file_of(account, "cert").is_file() and file_of(account, "key").is_file() else "falta el certificado o la clave"
        note = " · solo lectura" if account["ambiente"] == "produccion" and not may_issue(account) else ""
        rows.append(f"- {alias}{' (predeterminada)' if alias == default else ''}: CUIT {account['cuit']} · {account['ambiente']} · {ready}{note}")
    return "\n".join(rows)


@server.tool()
def verificar(cuenta: str | None = None) -> str:
    """Revisa paso a paso que la cuenta esté lista: archivos, certificado, ingreso a ARCA, servidores y puntos de venta."""
    lines = []

    def step(ok: bool, message: str) -> bool:
        lines.append(("✓ " if ok else "✗ ") + message)
        return ok

    try:
        account = pick(cuenta)
        lines.append(f"Cuenta {account['alias']} · CUIT {account['cuit']} · {account['ambiente']}")
        cert, key = read_pair(account)
        step(True, "Certificado y clave encontrados y legibles")
        left = (cert.not_valid_after_utc - datetime.now(timezone.utc)).days
        step(left > 0, f"Certificado vigente (vence en {left} días)" if left > 0 else "El certificado venció: pedí uno nuevo")
        subject = cert.subject.rfc4514_string()
        step(account["cuit"] in subject, "El CUIT del certificado coincide" if account["cuit"] in subject
             else f"El certificado no es de este CUIT ({one_line(subject, 80)})")
        step(cert.public_key().public_numbers() == key.public_key().public_numbers(),
             "La clave corresponde al certificado" if cert.public_key().public_numbers() == key.public_key().public_numbers()
             else "La clave NO corresponde a este certificado (¿mezclaste archivos de otro pedido?)")
        token, _ = ticket(account)
        step(True, "ARCA aceptó el ingreso (WSAA)")
        status = wsfe(account, "FEDummy", auth=False)
        servers = {name: text_of(status, name) for name in ("AppServer", "DbServer", "AuthServer")}
        step(all(v == "OK" for v in servers.values()), "Servidores de facturación: " + ", ".join(f"{k} {v or '?'}" for k, v in servers.items()))
        points = sale_points(account)
        step(bool(points), f"{len(points)} punto(s) de venta para web services" if points else
             "No hay puntos de venta para web services: creá uno de tipo «RECE para aplicativo y web services» en ARCA")
        if account.get("actividad"):
            ids = [text_of(a, "Id") for a in listed(account, "FEParamGetActividades", "ActividadesTipo")]
            wanted = str(account["actividad"])
            step(wanted in ids, f"La actividad {wanted} de la cuenta está habilitada para el emisor" if wanted in ids else
                 f"La actividad {wanted} de la cuenta NO está entre las del emisor ({', '.join(ids) or 'ninguna'}): "
                 "cambiala o quitá «actividad» de cuentas.json (en homologación suele no haber ninguna)")
    except Problem as problem:
        lines.append(f"✗ {problem}")
    return "\n".join(lines)


@server.tool()
def puntos_de_venta(cuenta: str | None = None) -> str:
    """Los puntos de venta habilitados para facturar por web service."""
    try:
        account = pick(cuenta)
        points = sale_points(account)
    except Problem as problem:
        return str(problem)
    if not points:
        return f"[{account['alias']}] No hay puntos de venta para web services. Creá uno de tipo «RECE para aplicativo y web services» en ARCA."
    rows = []
    for p in points:
        state = "bloqueado" if text_of(p, "Bloqueado") == "S" else "activo"
        if text_of(p, "FchBaja") not in ("", "NULL"):
            state = f"dado de baja ({day(text_of(p, 'FchBaja'))})"
        rows.append(f"- {text_of(p, 'Nro')} · {one_line(text_of(p, 'EmisionTipo'))} · {state}")
    return f"[{account['alias']}] Puntos de venta:\n" + "\n".join(rows)


@server.tool()
def tipos_de_comprobante(cuenta: str | None = None) -> str:
    """Los tipos de comprobante que ARCA permite emitir (el número es el que se usa en tipo=)."""
    try:
        account = pick(cuenta)
        kinds = find(wsfe(account, "FEParamGetTiposCbte"), "CbteTipo")
    except Problem as problem:
        return str(problem)
    return f"[{account['alias']}] Tipos de comprobante:\n" + "\n".join(f"- {text_of(k, 'Id')} · {one_line(text_of(k, 'Desc'))}" for k in kinds)


@server.tool()
def ultimo_comprobante(tipo: int | None = None, punto_venta: int | None = None, cuenta: str | None = None) -> str:
    """El número del último comprobante autorizado. Sin tipo usa el de la cuenta (monotributo: Factura C) y sin punto_venta el guardado en la cuenta."""
    try:
        account = pick(cuenta)
        kind, point = voucher_type(account, tipo), sale_point(account, punto_venta)
        root = wsfe(account, "FECompUltimoAutorizado", f"<PtoVta>{point}</PtoVta><CbteTipo>{kind}</CbteTipo>")
        last = int(text_of(root, "CbteNro") or 0)
    except (Problem, ValueError) as problem:
        return str(problem) if isinstance(problem, Problem) else "ARCA no devolvió un número válido."
    if last == 0:
        return f"[{account['alias']}] {label(kind)}, punto de venta {point}: todavía no hay comprobantes."
    return f"[{account['alias']}] Último {label(kind)}: {number_text(point, last)}"


@server.tool()
def ver_comprobante(numero: int, tipo: int | None = None, punto_venta: int | None = None, cuenta: str | None = None) -> str:
    """El detalle de un comprobante ya autorizado: fecha, importes, receptor y CAE."""
    try:
        account = pick(cuenta)
        kind, point = voucher_type(account, tipo), sale_point(account, punto_venta)
        if not 1 <= int(numero) <= 99999999:
            raise Problem("El número de comprobante es un entero positivo (hasta 8 cifras).")
        root = wsfe(account, "FECompConsultar", f"<FeCompConsReq><CbteTipo>{kind}</CbteTipo><CbteNro>{int(numero)}</CbteNro><PtoVta>{point}</PtoVta></FeCompConsReq>")
    except Problem as problem:
        return str(problem)
    coin = CURRENCIES.get(text_of(root, "MonId"), text_of(root, "MonId"))
    doc = int(text_of(root, "DocTipo") or 0)
    receiver = DOC_TYPES.get(doc, f"documento tipo {doc}") + ("" if doc == 99 else f" {text_of(root, 'DocNro')}")
    concept = int(text_of(root, "Concepto") or 0)
    detail = CONCEPTS.get(concept, "?")
    if concept in (2, 3):
        detail += f" ({day(text_of(root, 'FchServDesde'))} a {day(text_of(root, 'FchServHasta'))}, vence el pago {day(text_of(root, 'FchVtoPago'))})"
    result = {"A": "aprobado", "R": "rechazado"}.get(text_of(root, "Resultado"), text_of(root, "Resultado") or "?")
    return "\n".join([
        f"[{account['alias']}] {label(kind)} {number_text(point, numero)} · {day(text_of(root, 'CbteFch'))}",
        f"Total: {money(text_of(root, 'ImpTotal'), coin)} (neto {money(text_of(root, 'ImpNeto'))} · IVA {money(text_of(root, 'ImpIVA'))})",
        f"Receptor: {receiver}",
        f"Concepto: {detail}",
        f"CAE: {text_of(root, 'CodAutorizacion') or '?'} · vence {day(text_of(root, 'FchVto'))} · {result}",
    ])


def person_name(person: ET.Element) -> str:
    return one_line(" ".join(x for x in (text_of(person, "apellido"), text_of(person, "nombre")) if x) or text_of(person, "razonSocial"), 80)


def person_address(person: ET.Element) -> str:
    """Domicilio fiscal (o el primero que haya) en una línea. Es dato de ARCA, nunca instrucciones."""
    homes = [d for d in find(person, "domicilio") if text_of(d, "tipoDomicilio") == "FISCAL"] or find(person, "domicilio")
    if not homes:
        return ""
    return ", ".join(one_line(x, 80) for x in (text_of(homes[0], k) for k in ("direccion", "localidad", "descripcionProvincia")) if x)


def person_line(person: ET.Element) -> str:
    state = text_of(person, "estadoClave")
    return (f"{text_of(person, 'tipoClave') or 'CUIL'} {text_of(person, 'idPersona')} · {person_name(person) or '(sin nombre)'}"
            f" · {person_address(person) or 'sin domicilio'}{' · clave ' + state.lower() if state and state != 'ACTIVO' else ''}")


def look_up_people(account: dict, digits: str) -> tuple[list[str], list[ET.Element]]:
    """(claves, personas) de un DNI o de un CUIL/CUIT. Un DNI puede tener varias claves; se leen hasta 5."""
    if len(digits) == 11:
        ids = [digits]
    else:
        found = find(padron(account, "getIdPersonaListByDocumento", f"<documento>{digits}</documento>"), "idPersona")
        ids = [i for i in dict.fromkeys((e.text or "").strip() for e in found) if i]
    people = []
    for one in ids[:5]:
        found = find(padron(account, "getPersonaV2", f"<idPersona>{one}</idPersona>"), "persona")
        if found:
            people.append(found[0])
    return ids, people


def identify_client(account: dict, digits: str) -> tuple[str, str, str]:
    """(nombre, domicilio, aviso) del cliente según el padrón. Consultarlo es solo lectura. Si no se puede, NO frena la factura:
    vuelve un aviso. Un DNI con varias claves sí frena: hay que elegir el CUIL, como en la web."""
    try:
        ids, people = look_up_people(account, digits)
    except Problem as problem:
        return "", "", f"No pude consultar el padrón ({one_line(problem, 150)}): el nombre y el domicilio del cliente no están verificados."
    if len(ids) > 1:
        raise Problem(f"Ese DNI tiene {len(ids)} claves en el padrón; elegí una y pasala como documento (tipo CUIL):\n"
                      + "\n".join(person_line(p) for p in people))
    if not people:
        return "", "", "El padrón no tiene a nadie con ese documento: el nombre y el domicilio del cliente no están verificados."
    return person_name(people[0]), person_address(people[0]), ""


@server.tool()
def buscar_persona(documento: str, cuenta: str | None = None) -> str:
    """Busca en el padrón de ARCA el nombre y el domicilio de un DNI, CUIL o CUIT. Con un DNI muestra todas las claves que le corresponden (si hay más de una, hay que elegir el CUIL)."""
    try:
        account = pick(cuenta)
        digits = re.sub(r"[.\-\s]", "", str(documento))
        if not digits.isdigit() or not 6 <= len(digits) <= 11:
            raise Problem("documento: un DNI (hasta 8 números) o un CUIL/CUIT (11 números).")
        if len(digits) == 11 and not valid_cuit(digits):
            raise Problem("Ese CUIL/CUIT no es válido: revisá el dígito verificador.")
        ids, people = look_up_people(account, digits)
        if not ids:
            test = " El padrón de pruebas tiene solo personas de prueba, no las reales: probá en producción o con un CUIL de ejemplo." \
                if account["ambiente"] == "homologacion" else ""
            return f"[{account['alias']}] El padrón no tiene a nadie con el documento {digits}.{test}"
        lines = [person_line(p) for p in people] or [f"{ids[0]} · sin datos"]
        more = f"\n(Hay {len(ids)} claves; muestro las primeras 5.)" if len(ids) > 5 else ""
        pick_one = "\nHay más de una clave para ese DNI: usá el CUIL que corresponda como documento." if len(ids) > 1 else ""
        return f"[{account['alias']}] {banner(account)}\n" + "\n".join(lines) + more + pick_one
    except Problem as problem:
        return str(problem)


@server.tool()
def actividades(cuenta: str | None = None) -> str:
    """Las actividades del emisor que ARCA permite asociar a un comprobante (el código es el de «actividad» en la cuenta)."""
    try:
        account = pick(cuenta)
        found = listed(account, "FEParamGetActividades", "ActividadesTipo")
    except Problem as problem:
        return str(problem)
    if not found:
        return (f"[{account['alias']}] ARCA no tiene actividades habilitadas para este emisor ({account['ambiente']}). "
                "Si la cuenta tiene «actividad», quitala de cuentas.json: ARCA rechazaría el comprobante.")
    return f"[{account['alias']}] Actividades del emisor:\n" + "\n".join(
        f"- {text_of(a, 'Id')} · {one_line(text_of(a, 'Desc'), 120)}" + (" (principal)" if text_of(a, "Orden") == "1" else "") for a in found)


@server.tool()
def condiciones_iva_receptor(cuenta: str | None = None) -> str:
    """Las condiciones frente al IVA del receptor que ARCA acepta en una Factura C (el número es el que usa la emisión)."""
    try:
        account = pick(cuenta)
        found = find(wsfe(account, "FEParamGetCondicionIvaReceptor", "<ClaseCmp>C</ClaseCmp>"), "CondicionIvaReceptor")
    except Problem as problem:
        return str(problem)
    return f"[{account['alias']}] Condición del receptor en Factura C:\n" + "\n".join(
        f"- {text_of(c, 'Id')} · {one_line(text_of(c, 'Desc'))}" for c in found)


# ---------- emitir una Factura C ----------
# Solo Factura C (monotributo, sin IVA). Productos, servicios o ambos; una sola línea de detalle. A ARCA viajan los
# importes, el receptor y las fechas; la descripción, el domicilio y la forma de pago NO (solo existen en el PDF).

INVOICE_C = 11
CONCEPT_IDS = {"productos": 1, "servicios": 2, "productos_y_servicios": 3}
RECEIVER_CONDITIONS = {"consumidor_final": 5, "responsable_inscripto": 1, "exento": 4, "monotributo": 6,
                       "no_categorizado": 7, "monotributo_social": 13}
DOC_BY_NAME = {"dni": 96, "cuil": 86, "cuit": 80}
MAX_AMOUNT = Decimal("9999999999.99")
DATE_WINDOW = {1: 5, 2: 10, 3: 10}  # días antes o después de hoy que ARCA acepta, según el concepto


def may_issue(account: dict) -> bool:
    """Homologación siempre; producción solo si la cuenta lo habilita a propósito (`"emision": true`)."""
    return account["ambiente"] == "homologacion" or account.get("emision") is True


def banner(account: dict) -> str:
    return "HOMOLOGACIÓN (pruebas, sin validez fiscal)" if account["ambiente"] == "homologacion" else "PRODUCCIÓN (comprobante real)"


def valid_cuit(number: str) -> bool:
    """Dígito verificador de un CUIT/CUIL (módulo 11)."""
    if not re.fullmatch(r"\d{11}", number):
        return False
    total = sum(int(d) * w for d, w in zip(number[:10], (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)))
    check = {11: 0, 10: 9}.get(11 - total % 11, 11 - total % 11)
    return check == int(number[10])


def parse_amount(value) -> Decimal:
    try:
        exact = Decimal(str(value))
        if not exact.is_finite():  # NaN no falla al redondear: se rechaza antes
            raise InvalidOperation
        amount = exact.quantize(Decimal("0.01"), ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise Problem("El importe no es un número válido.") from None
    if amount != exact:
        raise Problem("El importe lleva como máximo 2 decimales (por ejemplo 1500.50). No redondeo por mi cuenta.")
    if not Decimal("0.01") <= amount <= MAX_AMOUNT:
        raise Problem("El importe tiene que ser mayor a 0.")
    return amount


def parse_receiver(kind: str | None, number: str | None) -> tuple[int, int, str]:
    """(tipo de documento de ARCA, número, texto para mostrar). Sin documento: consumidor final sin identificar."""
    digits = re.sub(r"[.\-\s]", "", str(number)) if number not in (None, "") else ""
    if not digits:
        if kind:
            raise Problem("Indicaste el tipo de documento pero falta el número (documento=…).")
        return 99, 0, "Consumidor final (sin identificar)"
    if not digits.isdigit():
        raise Problem("El documento lleva solo números.")
    if kind is None:  # 8 cifras o menos: DNI; 11: CUIL de persona (20/23/24/27) o CUIT de empresa
        kind = "dni" if len(digits) <= 8 else ("cuil" if digits[:2] in ("20", "23", "24", "27") else "cuit")
    kind = kind.strip().lower()
    if kind not in DOC_BY_NAME:
        raise Problem("tipo_documento tiene que ser dni, cuil o cuit.")
    if kind == "dni":
        if len(digits) > 8 or int(digits) == 0:
            raise Problem("Un DNI tiene hasta 8 números.")
    elif not valid_cuit(digits):
        raise Problem(f"El {kind.upper()} {digits} no es válido: revisá los números (el último es un dígito de control).")
    return DOC_BY_NAME[kind], int(digits), f"{kind.upper()} {digits}"


def parse_day(value: str | None, name: str, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise Problem(f"«{name}» va como AAAA-MM-DD. Recibí: {one_line(value, 30)!r}") from None


def build_invoice(account: dict, point: int, number: int, concept: int, doc: tuple[int, int], issued: date,
                  amount: Decimal, condition: int, service: tuple[date, date, date]) -> str:
    """El pedido FECAESolicitar. ARCA valida el XML EN ORDEN: es el del manual (ver docs/plan-facturas-pagos.md)."""
    day_text = lambda d: d.strftime("%Y%m%d")  # noqa: E731
    total = f"{amount:.2f}"
    fields = [("Concepto", concept), ("DocTipo", doc[0]), ("DocNro", doc[1]), ("CbteDesde", number), ("CbteHasta", number),
              ("CbteFch", day_text(issued)), ("ImpTotal", total), ("ImpTotConc", "0.00"), ("ImpNeto", total),
              ("ImpOpEx", "0.00"), ("ImpTrib", "0.00"), ("ImpIVA", "0.00")]  # tipo C: el neto es el subtotal y no hay IVA
    if concept in (2, 3):
        fields += [("FchServDesde", day_text(service[0])), ("FchServHasta", day_text(service[1])), ("FchVtoPago", day_text(service[2]))]
    fields += [("MonId", "PES"), ("MonCotiz", "1"), ("CondicionIVAReceptorId", condition)]
    detail = "".join(f"<{name}>{escape(str(value))}</{name}>" for name, value in fields)
    activity = str(account.get("actividad") or "")
    if activity:
        if not re.fullmatch(r"\d{1,6}", activity):
            raise Problem(f"Cuenta «{account['alias']}»: «actividad» son hasta 6 números (por ejemplo 479900).")
        detail += f"<Actividades><Actividad><Id>{activity}</Id></Actividad></Actividades>"
    return (f"<FeCAEReq><FeCabReq><CantReg>1</CantReg><PtoVta>{point}</PtoVta><CbteTipo>{INVOICE_C}</CbteTipo></FeCabReq>"
            f"<FeDetReq><FECAEDetRequest>{detail}</FECAEDetRequest></FeDetReq></FeCAEReq>")


def last_number(account: dict, point: int) -> int:
    root = wsfe(account, "FECompUltimoAutorizado", f"<PtoVta>{point}</PtoVta><CbteTipo>{INVOICE_C}</CbteTipo>")
    try:
        return int(text_of(root, "CbteNro") or 0)
    except ValueError:
        raise Problem("ARCA no devolvió un número de comprobante válido.") from None


REQUIRABLE = ("documento", "descripcion", "localidad")  # lo que una cuenta puede exigir además de lo que exige ARCA


def required_fields(account: dict) -> set[str]:
    """Datos que ESTA cuenta exige al emitir (`obligatorios` en cuentas.json). Sin la clave: solo lo que pide ARCA."""
    wanted = account.get("obligatorios", [])
    if not isinstance(wanted, list) or any(w not in REQUIRABLE for w in wanted):
        raise Problem(f"«obligatorios» en la cuenta «{account['alias']}» tiene que ser una lista con estos nombres: {', '.join(REQUIRABLE)}.")
    return set(wanted)


def confirmation_code(*parts) -> str:
    """Código corto atado a TODO lo que se va a emitir, incluido el número que le toca: no sirve dos veces."""
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:6]


def record(entry: dict) -> str:
    """Anota el intento en el registro local (solo se agrega, permisos 600). Devuelve un aviso si no pudo."""
    path = home() / "emitidas.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return f"\n(No pude anotarlo en el registro local {path}: guardá estos datos a mano.)"
    return ""


# ---------- PDF del comprobante ----------

QR_URL = "https://www.afip.gob.ar/fe/qr/?p="
RECEIVER_LABEL = {"consumidor_final": "Consumidor Final", "responsable_inscripto": "IVA Responsable Inscripto", "exento": "IVA Sujeto Exento",
                  "monotributo": "Responsable Monotributo", "no_categorizado": "Sujeto No Categorizado", "monotributo_social": "Monotributista Social"}
ISSUER_LABEL = {"monotributo": "Responsable Monotributo", "responsable_inscripto": "IVA Responsable Inscripto", "exento": "IVA Sujeto Exento"}


def pdf_folder(account: dict) -> Path:
    return Path(os.environ.get("ARCA_PDF_DIR") or account.get("pdf_dir") or home() / "comprobantes").expanduser()


def qr_link(account: dict, entry: dict) -> str:
    """El enlace del QR que ARCA pide en las facturas: sus datos en JSON, en base64, tras una dirección fija."""
    doc_type, doc_number = (int(x) for x in str(entry["receptor"]).split(":"))
    data = {"ver": 1, "fecha": entry["fecha"], "cuit": int(account["cuit"]), "ptoVta": entry["punto_venta"], "tipoCmp": entry["tipo"],
            "nroCmp": entry["numero"], "importe": float(entry["importe"]), "moneda": "PES", "ctz": 1, "tipoDocRec": doc_type,
            "nroDocRec": doc_number, "tipoCodAut": "E", "codAut": int(entry["cae"])}
    return QR_URL + base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def latin(value) -> str:
    """El PDF usa la letra estándar (latin-1): lo que no entra se cambia por «?» en vez de romper."""
    return str(value).encode("latin-1", "replace").decode("latin-1")


def render_pdf(account: dict, entry: dict) -> Path:
    """Arma el comprobante simple (A4) de una factura ya emitida, con su QR. Lo que viene de ARCA o del padrón es dato, solo se dibuja."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    point, number = int(entry["punto_venta"]), int(entry["numero"])
    test = entry.get("ambiente") == "homologacion"

    if test:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(190, 30, 30)
        pdf.cell(0, 6, latin("HOMOLOGACIÓN · COMPROBANTE DE PRUEBA, SIN VALIDEZ FISCAL"), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    top = pdf.get_y() + 2
    pdf.rect(15, top, 180, 38)
    pdf.line(105, top + 14, 105, top + 38)  # separa emisor y datos del comprobante
    pdf.rect(97, top, 16, 14)  # la letra
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_xy(97, top + 1)
    pdf.cell(16, 8, "C", align="C")
    pdf.set_font("Helvetica", "", 7)
    pdf.set_xy(97, top + 9)
    pdf.cell(16, 4, "COD. 011", align="C")

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_xy(18, top + 4)
    pdf.cell(76, 6, latin(one_line(account.get("nombre") or f"CUIT {account['cuit']}", 40)))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(18, top + 18)
    pdf.multi_cell(84, 5, latin(f"{one_line(account.get('domicilio_emisor') or '', 70)}\nCUIT: {account['cuit']}\n"
                                f"Condición frente al IVA: {ISSUER_LABEL.get(account.get('condicion', 'monotributo'), 'Monotributo')}"))
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_xy(116, top + 3)
    pdf.cell(76, 8, "FACTURA")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(116, top + 18)
    pdf.multi_cell(76, 5.5, latin(f"Nº {number_text(point, number)}\nFecha de emisión: {entry['fecha']}"))

    y = top + 44
    pdf.set_xy(15, y)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Cliente", new_x="LMARGIN", new_y="NEXT")
    doc_type, doc_number = (int(x) for x in str(entry["receptor"]).split(":"))
    who = entry.get("cliente") or ("Consumidor final" if doc_type == 99 else "")
    where = entry.get("domicilio") or entry.get("localidad") or ""
    pdf.set_font("Helvetica", "", 10)
    lines = [f"Nombre: {who or '(sin dato)'}"]
    if doc_type != 99:
        lines.append(f"{DOC_TYPES.get(doc_type, 'Documento')}: {doc_number}")
    lines += [f"Domicilio: {where or '(sin dato)'}",
              f"Condición frente al IVA: {RECEIVER_LABEL.get(entry.get('condicion_receptor', 'consumidor_final'), 'Consumidor Final')}",
              f"Condición de venta: {entry.get('forma_pago') or 'Contado'}"]
    for line in lines:
        pdf.set_x(15)
        pdf.cell(0, 5.5, latin(one_line(line, 110)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(pdf.get_y() + 6)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(235, 235, 235)
    for width, text, align in ((100, "Descripción", "L"), (20, "Cant.", "R"), (30, "Precio unit.", "R"), (30, "Subtotal", "R")):
        pdf.cell(width, 7, text, border=1, align=align, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    amount = money(entry["importe"])
    start = pdf.get_y()
    pdf.set_xy(15, start)
    pdf.multi_cell(100, 6, latin(one_line(entry.get("descripcion") or "", 300)), border=1, new_x="RIGHT", new_y="TOP", max_line_height=6)
    height = pdf.get_y() - start if pdf.get_y() > start else 6
    pdf.set_xy(115, start)
    for width, text in ((20, "1"), (30, amount), (30, amount)):
        pdf.cell(width, max(height, 6), text, border=1, align="R")
    pdf.ln(max(height, 6) + 4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, latin(f"Importe total: $ {amount}"), align="R", new_x="LMARGIN", new_y="NEXT")

    base = 232
    code = segno.make(qr_link(account, entry), error="m")
    cell = 34 / (len(code.matrix) + 8)  # 34 mm con 4 módulos de margen a cada lado
    for row_number, row in enumerate(code.matrix):
        for column_number, dark in enumerate(row):
            if dark:
                pdf.rect(15 + (column_number + 4) * cell, base + (row_number + 4) * cell, cell, cell, style="F")
    pdf.set_xy(55, base + 8)
    pdf.set_font("Helvetica", "B", 10)
    pdf.multi_cell(0, 6, latin(f"CAE Nº: {entry['cae']}\nFecha de vencimiento del CAE: {entry.get('cae_vencimiento') or '?'}"))
    pdf.set_x(55)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4.5, latin("Comprobante autorizado por ARCA. Escaneá el QR para verificarlo."
                                 + (" Es de PRUEBA: no tiene validez fiscal." if test else "")))

    folder = pdf_folder(account)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"{account['alias']}-{number_text(point, number)}.pdf"
    write_private(path, bytes(pdf.output()))
    return path


def observations(detail: ET.Element) -> list[str]:
    """Observaciones de un comprobante. El manual las muestra como Obs/Observaciones y ARCA las manda al revés: vale cualquiera."""
    found = [o for o in detail.iter() if local(o.tag) in ("Obs", "Observaciones") and any(local(c.tag) == "Code" for c in o)]
    return [f"{one_line(text_of(o, 'Msg'), 200)} (código {text_of(o, 'Code')})" for o in found]


def settle_after_silence(account: dict, point: int, number: int, problem: Problem, entry: dict) -> str:
    """El pedido salió pero no hubo respuesta: no se sabe si ARCA emitió. Se mira antes de decir nada, y NUNCA se repite solo."""
    try:
        now_last = last_number(account, point)
    except Problem:
        entry["resultado"] = "sin_confirmar"
        return (f"✗ {problem}\nNo pude comprobar si ARCA emitió el comprobante {number_text(point, number)}. **No repitas el comando**: "
                f"mirá primero con ultimo_comprobante (si el último es {number_text(point, number)}, ya se emitió).") + record(entry)
    if now_last >= number:
        entry["resultado"] = "emitida_sin_respuesta"
        return (f"⚠ ARCA SÍ emitió el comprobante {number_text(point, number)}, aunque la respuesta no llegó ({problem}). "
                f"Mirá el detalle con ver_comprobante numero={number}. No lo repitas.") + record(entry)
    entry["resultado"] = "no_emitida"
    return f"✗ {problem}\nARCA no emitió nada (el último comprobante sigue siendo el {now_last}). Podés volver a simular y confirmar." + record(entry)


@server.tool()
def pdf_comprobante(numero: int, punto_venta: int | None = None, cuenta: str | None = None) -> str:
    """Arma (o rehace) el PDF de una factura C que se emitió desde acá, con los datos del registro local. Sirve si al emitir falló el PDF."""
    try:
        account = pick(cuenta)
        point = sale_point(account, punto_venta)
        path = home() / "emitidas.jsonl"
        found = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    one = json.loads(line)
                except ValueError:
                    continue
                if (one.get("cuenta"), one.get("ambiente"), one.get("punto_venta"), one.get("numero"), one.get("resultado")) == (
                        account["alias"], account["ambiente"], point, int(numero), "emitida"):
                    found = one
        except OSError:
            pass
        if not found:
            return (f"No tengo en el registro local la factura {number_text(point, int(numero))} de «{account['alias']}»: "
                    "solo puedo armar el PDF de las que se emitieron con emitir_factura en esta máquina.")
        note = ""
        doc_type, doc_number = (int(x) for x in str(found["receptor"]).split(":"))
        if not found.get("cliente") and doc_type != 99:  # salió sin el padrón: se intenta ahora (el registro no se toca)
            name, address, warning = identify_client(account, str(doc_number))
            if name:
                found = {**found, "cliente": name, "domicilio": address}
            else:
                note = f"\n⚠ {warning}"
        return f"PDF: {render_pdf(account, found)}{note}"
    except Problem as problem:
        return str(problem)
    except Exception as error:
        return f"No pude armar el PDF: {one_line(error, 200)}"


def plan_invoice(account: dict, *, importe, concepto, documento, tipo_documento, condicion_receptor, descripcion, fecha, servicio_desde,
                 servicio_hasta, vencimiento_pago, punto_venta, localidad, sin_identificar, referencia: str = "") -> dict:
    """Todo lo que se decide ANTES de hablar con ARCA para emitir: valida el pedido con las reglas de la cuenta y consulta el padrón
    (solo lectura). Levanta Problem si algo no está bien. El número de comprobante se asigna después."""
    what = descripcion if descripcion is not None else account.get("descripcion")
    required = required_fields(account)
    if "descripcion" in required and not (what and str(what).strip()):
        raise Problem("Falta la descripción de lo vendido: descripcion=«Repuestos y reparaciones» (o guardala en la cuenta con «descripcion»). "
                      "Esta cuenta la exige («obligatorios» en cuentas.json).")
    if sin_identificar and documento:
        raise Problem("Pediste «sin_identificar» y también un documento: dejá solo uno.")
    if "documento" in required and not sin_identificar and not (documento and str(documento).strip()):
        raise Problem("Falta el documento del cliente: documento=<DNI, CUIL o CUIT>. Si de verdad es un consumidor final sin identificar, "
                      "agregá sin_identificar=true. Esta cuenta lo exige («obligatorios» en cuentas.json).")
    place = str(localidad if localidad is not None else account.get("localidad") or "").strip()
    if "localidad" in required and not place:
        raise Problem("Falta la localidad: localidad=CABA (o guardala en la cuenta con «localidad»). Esta cuenta la exige («obligatorios» en cuentas.json).")
    point = sale_point(account, punto_venta)
    concept_name = (concepto or account.get("concepto") or "").strip().lower()
    if concept_name not in CONCEPT_IDS:
        raise Problem("Decime el concepto: concepto=productos, servicios o productos_y_servicios (o guardalo en la cuenta con «concepto»).")
    concept = CONCEPT_IDS[concept_name]
    amount = parse_amount(importe)
    doc_type, doc_number, receiver = parse_receiver(tipo_documento, documento)
    wanted = str(condicion_receptor or "consumidor_final").strip().lower()
    condition = int(wanted) if wanted.isdigit() else RECEIVER_CONDITIONS.get(wanted)
    if condition is None:
        raise Problem(f"condicion_receptor: uno de {', '.join(RECEIVER_CONDITIONS)} (o el número, ver condiciones_iva_receptor).")
    today = datetime.now(AR_TZ).date()
    issued = parse_day(fecha, "fecha", today)
    if abs((issued - today).days) > DATE_WINDOW[concept]:
        raise Problem(f"Con {concept_name.replace('_', ' ')}, la fecha puede ser hasta {DATE_WINDOW[concept]} días antes o después de hoy ({today}).")
    service = (parse_day(servicio_desde, "servicio_desde", issued), parse_day(servicio_hasta, "servicio_hasta", issued),
               parse_day(vencimiento_pago, "vencimiento_pago", issued))
    if service[1] < service[0]:
        raise Problem("«servicio_hasta» no puede ser anterior a «servicio_desde».")
    if service[2] < issued:
        raise Problem("«vencimiento_pago» no puede ser anterior a la fecha del comprobante.")
    client = identify_client(account, str(doc_number)) if doc_type != 99 else ("", "", "")  # (nombre, domicilio, aviso)
    return {"point": point, "concept": concept, "concept_name": concept_name, "amount": amount, "doc_type": doc_type, "doc_number": doc_number,
            "receiver": receiver, "wanted": wanted, "condition": condition, "issued": issued, "service": service, "what": what, "place": place,
            "client": client, "documento": documento, "referencia": referencia}


def invoice_request(account: dict, plan: dict, number: int) -> str:
    return build_invoice(account, plan["point"], number, plan["concept"], (plan["doc_type"], plan["doc_number"]), plan["issued"], plan["amount"],
                         plan["condition"], plan["service"])


def invoice_entry(account: dict, plan: dict, number: int) -> dict:
    """La línea del registro local de esta emisión (se completa con el resultado)."""
    entry = {"fecha_registro": datetime.now(AR_TZ).isoformat(timespec="seconds"), "cuenta": account["alias"], "ambiente": account["ambiente"],
             "punto_venta": plan["point"], "tipo": INVOICE_C, "numero": number, "fecha": plan["issued"].isoformat(), "importe": f"{plan['amount']:.2f}",
             "receptor": f"{plan['doc_type']}:{plan['doc_number']}", "concepto": plan["concept_name"], "descripcion": plan["what"] or "",
             "forma_pago": "Contado", "localidad": plan["place"], "cliente": plan["client"][0], "domicilio": plan["client"][1],
             "condicion_receptor": plan["wanted"]}
    if plan["referencia"]:
        entry["referencia"] = plan["referencia"]
    return entry


def refuse_production_without_emission(account: dict) -> None:
    if not may_issue(account):
        raise Problem(f"La cuenta «{account['alias']}» es de PRODUCCIÓN y está en solo lectura. Para habilitar la emisión agregá "
                      f'"emision": true a esa cuenta en {home() / "cuentas.json"} (a propósito, y solo si querés emitir de verdad).')


def send_invoice(account: dict, plan: dict, number: int, request: str, entry: dict, make_pdf: bool) -> str:
    """Manda el pedido a ARCA y cuenta qué pasó; la línea del registro queda con su `resultado`. NUNCA repite un pedido enviado."""
    point = plan["point"]
    try:
        root = wsfe(account, "FECAESolicitar", request)
    except Problem as problem:
        if problem.maybe_sent:
            return settle_after_silence(account, point, number, problem, entry)
        entry.update(resultado="error", error=one_line(problem, 300))
        return f"✗ {problem}\nNo se emitió nada." + record(entry)
    raw = "; ".join(f"{local(e.tag)}={one_line(e.text, 80)}" for e in root.iter() if e.text and e.text.strip())[:1500]  # para diagnosticar
    detail = find(root, "FECAEDetResponse") or find(root, "FEDetResponse")  # el manual dice una cosa y ARCA manda la otra: vale cualquiera
    outcome = text_of(detail[0], "Resultado") if detail else ""
    if outcome not in ("A", "R"):
        # Contestó pero no sé leerlo: NO puedo decir que no emitió. Se mira el último número, como cuando no hay respuesta.
        entry["respuesta"] = raw
        return settle_after_silence(account, point, number, Problem("ARCA contestó con una respuesta que no sé leer", maybe_sent=True), entry)
    notes = observations(detail[0]) + [f"{one_line(text_of(e, 'Msg'), 200)} (aviso {text_of(e, 'Code')})" for e in find(root, "Evt")]
    if outcome == "R":
        entry.update(resultado="rechazada", observaciones=notes, respuesta=raw)
        reasons = "; ".join(notes) or "sin más detalle (ver «respuesta» en emitidas.jsonl)"
        if any(f"código {c}" in reasons for c in ("10220", "10223")):  # actividad no habilitada: se dice cómo seguir
            reasons += ". Mirá las válidas con la herramienta «actividades» o quitá «actividad» de esta cuenta en cuentas.json"
        return f"✗ ARCA rechazó el comprobante: {reasons}\nNo se emitió nada." + record(entry)
    cae, expires = text_of(detail[0], "CAE"), day(text_of(detail[0], "CAEFchVto"))
    entry.update(resultado="emitida", cae=cae, cae_vencimiento=expires, observaciones=notes)
    client, what = plan["client"], plan["what"]
    done = [f"✓ [{account['alias']}] {banner(account)}", f"Factura C {number_text(point, number)} emitida · {plan['issued'].isoformat()}",
            f"Total: {money(plan['amount'], 'ARS')} · Receptor: {plan['receiver']}" + (f" · {client[0]}" if client[0] else ""), f"CAE: {cae} · vence {expires}"]
    done.append(f"Descripción: {one_line(what, 120) if what else '(ninguna)'} · Pago: Contado")
    if make_pdf:
        try:
            entry["pdf"] = str(render_pdf(account, entry))
            done.append(f"PDF: {entry['pdf']}")
        except Exception as error:  # la factura YA está emitida: un problema con el PDF nunca la esconde
            done.append(f"⚠ La factura se emitió, pero no pude armar el PDF ({one_line(error, 150)}). Probá de nuevo con pdf_comprobante numero={number}.")
    if notes:
        done.append("Observaciones de ARCA: " + "; ".join(notes))
    return "\n".join(done) + record(entry)


@server.tool()
def emitir_factura(importe: float, concepto: str | None = None, documento: str | None = None, tipo_documento: str | None = None,
                   condicion_receptor: str | None = None, descripcion: str | None = None, fecha: str | None = None,
                   servicio_desde: str | None = None, servicio_hasta: str | None = None, vencimiento_pago: str | None = None,
                   punto_venta: int | None = None, localidad: str | None = None, sin_identificar: bool = False,
                   pdf: bool | None = None, cuenta: str | None = None, confirmar: str | None = None) -> str:
    """Emite una Factura C. SIN `confirmar` solo SIMULA (no emite nada) y da un código; para emitir, repetir el mismo pedido con confirmar=<código>. concepto: productos, servicios o productos_y_servicios. documento (DNI, CUIL o CUIT), descripcion y localidad son opcionales salvo que la cuenta los exija; con sin_identificar=true se confirma un consumidor final sin documento. El pago es siempre contado. Al emitir genera el PDF del comprobante (pdf=false para no hacerlo)."""
    try:
        account = pick(cuenta)
        plan = plan_invoice(account, importe=importe, concepto=concepto, documento=documento, tipo_documento=tipo_documento,
                            condicion_receptor=condicion_receptor, descripcion=descripcion, fecha=fecha, servicio_desde=servicio_desde,
                            servicio_hasta=servicio_hasta, vencimiento_pago=vencimiento_pago, punto_venta=punto_venta, localidad=localidad,
                            sin_identificar=sin_identificar)
        point, what, place, client = plan["point"], plan["what"], plan["place"], plan["client"]
        number = last_number(account, point) + 1
        request = invoice_request(account, plan, number)
        code = confirmation_code(account["alias"], account["ambiente"], account["cuit"], request, what, place)
        summary = [f"[{account['alias']}] {banner(account)}",
                   f"Factura C {number_text(point, number)} · {plan['issued'].isoformat()}",
                   f"Total: {money(plan['amount'], 'ARS')} · Concepto: {plan['concept_name'].replace('_', ' ')}",
                   f"Receptor: {plan['receiver']} · condición {plan['wanted']}"]
        if client[0]:
            summary.append(f"Cliente (padrón): {client[0]} · {client[1] or 'sin domicilio'}")
        elif client[2]:
            summary.append(f"⚠ {client[2]}")
        summary.append(f"Descripción: {one_line(what, 120) if what else '(ninguna)'} · Pago: Contado" + (f" · Localidad: {one_line(place, 60)}" if place else ""))
        missing = [name for name, value in (("descripción", what), ("documento del cliente", documento)) if not (value and str(value).strip())]
        if missing:
            summary.append("⚠ Sin " + " ni ".join(missing) + ": ARCA lo acepta, pero el comprobante que entregues quedaría incompleto.")
        make_pdf = pdf if pdf is not None else account.get("pdf", True)
        summary.append("ARCA no guarda la descripción, el nombre, el domicilio ni la forma de pago: quedan en el registro local y "
                       + (f"en el PDF ({pdf_folder(account)})." if make_pdf else "NO habrá PDF (pdf=false): no tendrás el comprobante para entregar."))
        if confirmar is None:
            return "\n".join(summary + [f"No se emitió nada. Para emitir, repetí el mismo pedido agregando confirmar={code}"])
        if confirmar.strip().lower() != code:
            return "\n".join(["El código no coincide con este comprobante: algo cambió (el número que le toca, algún dato) o ya se emitió.",
                              "No se emitió nada. Esto es lo que se emitiría ahora:"] + summary + [f"Para emitir, agregá confirmar={code}"])
        refuse_production_without_emission(account)
        entry = invoice_entry(account, plan, number)
    except Problem as problem:
        return str(problem)
    return send_invoice(account, plan, number, request, entry, make_pdf)


# ---------- Lotes: facturas desde un CSV ----------

BATCH_COLUMNS = {
    "id": ("id", "referencia", "ref"),
    "nombre": ("nombre", "cliente", "razon social", "nombre y apellido"),
    "documento": ("dni", "cuil", "cuit", "documento", "dni/cuil/cuit", "dni/cuil", "cuil/cuit", "cuit/cuil", "dni cuil cuit"),
    "importe": ("precio", "importe", "monto", "total"),
    "localidad": ("localidad",), "descripcion": ("descripcion",), "concepto": ("concepto",), "fecha": ("fecha",),
}
BATCH_REQUIRED = ("id", "nombre", "documento", "importe")
BATCH_MAX_BYTES = 1_000_000
BATCH_LIMIT = 50  # filas por lote; la cuenta lo cambia con «max_lote»
UNSURE = ("sin_confirmar", "emitida_sin_respuesta")  # intentos anteriores de los que no se sabe si ARCA emitió


def plain(text) -> str:
    """Minúsculas y sin acentos, para comparar títulos y nombres."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)).lower().strip()


def name_words(text) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", plain(text)))


def same_person(written: str, registered: str) -> bool:
    """El nombre escrito coincide si todas sus palabras están en el del padrón (sin importar orden, mayúsculas ni acentos)."""
    wanted = name_words(written)
    return bool(wanted) and wanted <= name_words(registered)


def money_cell(text: str) -> str:
    """El precio como lo escribe una planilla argentina: «15.000,50», «$ 15000.50» o «8200»."""
    cleaned = re.sub(r"[\s$]", "", str(text))
    if "," in cleaned:
        return cleaned.replace(".", "").replace(",", ".")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned):  # «1.500» son mil quinientos
        return cleaned.replace(".", "")
    return cleaned


def read_batch(path_text: str) -> list[dict]:
    """Las filas del CSV como dicts con las columnas conocidas (más «line», el número de línea del archivo)."""
    path = Path(str(path_text)).expanduser()
    if not path.is_file():
        raise Problem(f"No encuentro el archivo {path}. Pasá la ruta completa: archivo=/ruta/facturas.csv")
    if path.stat().st_size > BATCH_MAX_BYTES:
        raise Problem("El archivo es demasiado grande para un lote (más de 1 MB).")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")  # el CSV que guarda Excel en Windows
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    table = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not table:
        raise Problem("El archivo está vacío.")
    header = [plain(h) for h in table[0]]
    index = {}
    for name, aliases in BATCH_COLUMNS.items():
        for position, title in enumerate(header):
            if title in aliases and name not in index:
                index[name] = position
    missing = [name for name in BATCH_REQUIRED if name not in index]
    if missing:
        raise Problem(f"Al CSV le faltan columnas: {', '.join(missing)}. Las que encontré: {', '.join(h for h in table[0] if h.strip()) or '(ninguna)'}. "
                      "Hacen falta: ID, nombre, DNI (o CUIL/CUIT), precio; opcionales: localidad, descripcion, concepto, fecha.")
    rows = []
    for number, cells in enumerate(table[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        rows.append({"line": number, **{name: (cells[i].strip() if i < len(cells) else "") for name, i in index.items()}})
    if not rows:
        raise Problem("El archivo no tiene filas con datos.")
    return rows


def earlier_attempts(account: dict) -> dict[str, tuple[str, str]]:
    """ID (referencia) -> (resultado, número) del último intento anotado en el registro local para esa cuenta y ambiente."""
    found = {}
    try:
        for line in (home() / "emitidas.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                one = json.loads(line)
            except ValueError:
                continue
            if one.get("referencia") and one.get("cuenta") == account["alias"] and one.get("ambiente") == account["ambiente"]:
                found[str(one["referencia"])] = (one.get("resultado", ""), number_text(one.get("punto_venta", 0), one.get("numero", 0)))
    except OSError:
        pass
    return found


def batch_limit(account: dict) -> int:
    wanted = account.get("max_lote", BATCH_LIMIT)
    if not isinstance(wanted, int) or isinstance(wanted, bool) or not 1 <= wanted <= 500:
        raise Problem(f"«max_lote» de la cuenta «{account['alias']}» tiene que ser un número entero entre 1 y 500.")
    return wanted


def judge_row(account: dict, row: dict, seen: set, before: dict) -> dict:
    """Valida una fila con las mismas reglas que emitir_factura y compara el nombre con el padrón. Estados: OK, REVISAR, ERROR, YA EMITIDA."""
    item = {"line": row["line"], "id": row["id"], "name": row["nombre"], "status": "OK", "why": "", "plan": None}
    try:
        if not row["id"]:
            raise Problem("falta el ID")
        if row["id"] in seen:
            raise Problem("el ID está repetido en el archivo")
        seen.add(row["id"])
        tried = before.get(row["id"])
        if tried and tried[0] == "emitida":
            item.update(status="YA EMITIDA", why=f"ya salió como {tried[1]}")
            return item
        if tried and tried[0] in UNSURE:
            raise Problem(f"un intento anterior ({tried[1]}) quedó sin confirmar: mirá con ultimo_comprobante si ARCA lo emitió antes de seguir")
        if not row["documento"]:
            raise Problem("falta el DNI/CUIL/CUIT")
        plan = plan_invoice(account, importe=money_cell(row["importe"]), concepto=row.get("concepto") or None, documento=row["documento"],
                            tipo_documento=None, condicion_receptor=None, descripcion=row.get("descripcion") or None, fecha=row.get("fecha") or None,
                            servicio_desde=None, servicio_hasta=None, vencimiento_pago=None, punto_venta=None,
                            localidad=row.get("localidad") or account.get("localidad") or "CABA", sin_identificar=False, referencia=row["id"])
        item["plan"] = plan
        registered, _, warning = plan["client"]
        if not row["nombre"]:
            item.update(status="REVISAR", why="el archivo no trae nombre" + (f"; el padrón dice {registered}" if registered else ""))
        elif registered and not same_person(row["nombre"], registered):
            item.update(status="REVISAR", why=f"el nombre no coincide: el padrón dice «{registered}»")
        elif not registered:
            item.update(status="REVISAR", why="el padrón no respondió: nombre sin verificar" if warning.startswith("No pude consultar")
                        else "el padrón no conoce ese documento")
    except Problem as problem:
        item.update(status="ERROR", why=one_line(problem, 200))
    return item


MARKS = {"OK": "✓", "REVISAR": "⚠", "ERROR": "✗", "YA EMITIDA": "↺"}


def batch_table(items: list[dict], numbers: dict[int, int], point: int, accepted: set) -> list[str]:
    """Una línea por fila (una tabla se rompe en pantallas angostas): marca, fila, ID, nombre, importe, número de factura y el motivo."""
    lines = []
    for item in items:
        plan = item["plan"]
        status = "ACEPTADA" if item["status"] == "REVISAR" and item["id"] in accepted else item["status"]
        mark = "✓" if status == "ACEPTADA" else MARKS[item["status"]]
        parts = [f"Fila {item['line']}", one_line(item["id"], 20), one_line(item["name"], 40) or "(sin nombre)"]
        if plan:
            parts.append(money(plan["amount"], "ARS"))
        if item["line"] in numbers:
            parts.append(number_text(point, numbers[item["line"]]))
        reason = f"{status}: {item['why']}" if item["why"] else status
        lines.append(f"- {mark} {' · '.join(parts)} — {reason}")
    return lines


@server.tool()
def emitir_lote(archivo: str, aceptar: str | None = None, pdf: bool | None = None, cuenta: str | None = None, confirmar: str | None = None) -> str:
    """Emite varias Facturas C desde un CSV (columnas: ID, nombre, DNI/CUIL/CUIT, precio; opcionales: localidad, descripcion, concepto, fecha). SIN `confirmar` solo SIMULA: valida cada fila, compara el nombre con el padrón y da un código. Las filas con nombre dudoso (REVISAR) solo salen si las aceptás con aceptar=ID1,ID2. Si una fila tiene ERROR no se emite nada. Se frena en la primera falla y nunca reintenta."""
    try:
        account = pick(cuenta)
        rows = read_batch(archivo)
        if len(rows) > batch_limit(account):
            raise Problem(f"El archivo tiene {len(rows)} filas y esta cuenta permite {batch_limit(account)} por lote («max_lote» en cuentas.json). Partilo en varios.")
        accepted = {a.strip() for a in re.split(r"[,;]", str(aceptar or "")) if a.strip()}
        before, seen = earlier_attempts(account), set()
        items = [judge_row(account, row, seen, before) for row in rows]
        unknown = accepted - {i["id"] for i in items if i["status"] == "REVISAR"}
        if unknown:
            raise Problem(f"aceptar menciona IDs que no están para revisar: {', '.join(sorted(unknown))}. Se aceptan solo los que figuran como REVISAR.")
        pending = [i for i in items if i["status"] in ("OK", "REVISAR")]
        point = pending[0]["plan"]["point"] if pending else sale_point(account, None)
        first = last_number(account, point) + 1 if pending else 0
        numbers = {item["line"]: first + n for n, item in enumerate(pending)}
        header = [f"[{account['alias']}] {banner(account)}", f"Lote: {Path(archivo).expanduser().name} · {len(items)} filas"]
        table = batch_table(items, numbers, point, accepted)
        count = lambda status: sum(1 for i in items if i["status"] == status)  # noqa: E731
        total = sum((i["plan"]["amount"] for i in pending), Decimal("0"))
        facts = [f"A emitir: {len(pending)} por {money(total, 'ARS')} · para revisar: {count('REVISAR')} · ya emitidas: {count('YA EMITIDA')} · con error: {count('ERROR')}"]
        if count("ERROR"):
            return "\n".join(header + table + facts + ["No se emitió nada. Arreglá las filas con ERROR en el CSV y volvé a simular."])
        if not pending:
            return "\n".join(header + table + facts + ["No hay nada para emitir."])
        make_pdf = pdf if pdf is not None else account.get("pdf", True)
        code = confirmation_code(account["alias"], account["ambiente"], account["cuit"], first, make_pdf,
                                 *[(i["id"], invoice_request(account, i["plan"], numbers[i["line"]]), i["plan"]["what"], i["plan"]["place"]) for i in pending])
        review = [i["id"] for i in pending if i["status"] == "REVISAR" and i["id"] not in accepted]
        if confirmar is None:
            how = f"confirmar={code}" + (f" aceptar={','.join(review)}" if review else "")
            note = (f"Las filas REVISAR no salen salvo que las aceptes (aceptar=ID,…): mirá bien que el nombre del archivo sea el correcto. " if review else "")
            return "\n".join(header + table + facts + [note + f"No se emitió nada. Para emitir, repetí el mismo pedido agregando {how}"])
        if confirmar.strip().lower() != code:
            return "\n".join(["El código no coincide con este lote: algo cambió (el archivo, el número que toca o una fila) o ya se emitió.",
                              "No se emitió nada. Esto es lo que se emitiría ahora:"] + header + table + facts + [f"Para emitir, agregá confirmar={code}"])
        refuse_production_without_emission(account)
        if review:
            return "\n".join(header + table + facts + [f"No se emitió nada: faltan aceptar estas filas con nombre dudoso: aceptar={','.join(review)}"])
    except Problem as problem:
        return str(problem)
    out, issued_total, stopped = header + ["Emitiendo…"], Decimal("0"), None
    for position, item in enumerate(pending):
        plan, expected = item["plan"], numbers[item["line"]]
        try:
            number = last_number(account, point) + 1
            if number != expected:
                raise Problem(f"El número que toca ahora es {number_text(point, number)} y esperaba {number_text(point, expected)}: alguien emitió en el medio.")
            request = invoice_request(account, plan, number)
        except Problem as problem:
            out.append(f"✗ Fila {item['line']} · ID {item['id']}: {problem}")
            stopped = position
            break
        entry = invoice_entry(account, plan, number)
        text = send_invoice(account, plan, number, request, entry, make_pdf)
        if entry.get("resultado") == "emitida":
            issued_total += plan["amount"]
            pdf_path = f" · PDF: {entry['pdf']}" if entry.get("pdf") else ""
            out.append(f"✓ Fila {item['line']} · ID {item['id']}: {number_text(point, number)} · CAE {entry['cae']} · {money(plan['amount'], 'ARS')}{pdf_path}")
            if "⚠" in text:
                out.append("  " + next(line for line in text.splitlines() if "⚠" in line))
            continue
        out.append(f"Fila {item['line']} · ID {item['id']}:\n{text}")
        stopped = position
        break
    done = sum(1 for line in out if line.startswith("✓"))
    if stopped is not None:
        rest = [i["id"] for i in pending[stopped + 1:]]
        out.append(f"SE FRENÓ en la fila {pending[stopped]['line']}: no se siguió con el resto"
                   + (f" ({len(rest)} sin procesar: {', '.join(rest)})" if rest else "") + ". Nada se reintentó solo.")
    out.append(f"Resultado: {done} emitidas de {len(pending)} por {money(issued_total, 'ARS')}.")
    return "\n".join(out)


@server.resource("epsilon://buttons")
def buttons() -> str:
    """Botones que Epsilon puede ofrecer a la persona (nunca los crea solo)."""
    return json.dumps([
        {"label": "Último comprobante", "tool": "ultimo_comprobante", "args": {}},
        {"label": "Puntos de venta", "tool": "puntos_de_venta", "args": {}},
        {"label": "Verificar la cuenta", "tool": "verificar", "args": {}},
    ], ensure_ascii=False)


# ---------- línea de comandos: crear una cuenta ----------

def create_account(alias: str, cuit: str, name: str, environment: str, condition: str) -> str:
    """Genera clave privada + pedido de certificado (CSR) y anota la cuenta. Nunca pisa una clave existente."""
    if not ALIAS_RE.fullmatch(alias):
        raise Problem("El nombre va en minúsculas, de 2 a 20 caracteres: letras, números y guion bajo (empieza con letra).")
    if not re.fullmatch(r"\d{11}", cuit):
        raise Problem("El CUIT son 11 números, sin guiones.")
    folder = home()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path, csr_path, accounts_path = folder / f"{alias}.key", folder / f"{alias}.csr", folder / "cuentas.json"
    accounts = json.loads(accounts_path.read_text(encoding="utf-8")) if accounts_path.is_file() else {}
    if alias in accounts or key_path.exists():
        raise Problem(f"La cuenta «{alias}» ya existe ({key_path}). No piso nada: elegí otro nombre.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, name[:60]),
        x509.NameAttribute(NameOID.COMMON_NAME, alias),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
    ])
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    write_private(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                              serialization.NoEncryption()))
    csr_path.write_bytes(csr.public_bytes(serialization.Encoding.PEM))
    accounts[alias] = {"cuit": cuit, "ambiente": environment, "condicion": condition, **({"nombre": name} if name != "Epsilon" else {})}
    write_private(accounts_path, json.dumps(accounts, indent=2, ensure_ascii=False))
    wsass = "WSASS (certificados de testing)" if environment == "homologacion" else "«Administración de Certificados Digitales»"
    return (f"Listo. Cuenta «{alias}» creada en {folder}\n  clave privada: {key_path}  (no la compartas)\n  pedido de certificado: {csr_path}\n\n"
            f"Próximos pasos (una sola vez):\n  1. En ARCA, con tu clave fiscal, entrá a {wsass} y subí el archivo .csr.\n"
            f"  2. Descargá el certificado (.crt) y guardalo como {folder / (alias + '.crt')}\n"
            f"  3. Autorizá el servicio «wsfe» (Facturación Electrónica) para ese certificado.\n"
            f"  4. Creá un punto de venta de tipo «RECE para aplicativo y web services».\n"
            f"  5. En Epsilon: /c arca verificar cuenta={alias}")


def main(argv: list[str]) -> None:
    if argv[:1] != ["nueva"]:
        server.run()  # stdio; stdout es del protocolo: lo propio va a stderr
        return
    parser = argparse.ArgumentParser(prog="arca_mcp.py nueva", description="Crea una cuenta de ARCA: clave privada y pedido de certificado.")
    parser.add_argument("alias")
    parser.add_argument("--cuit", required=True)
    parser.add_argument("--nombre", default="Epsilon", help="nombre que va en el certificado (el tuyo o el de tu negocio)")
    parser.add_argument("--ambiente", choices=ENVIRONMENTS, default="homologacion")
    parser.add_argument("--condicion", choices=("monotributo", "responsable_inscripto", "exento"), default="monotributo")
    args = parser.parse_args(argv[1:])
    try:
        print(create_account(args.alias, args.cuit, args.nombre, args.ambiente, args.condicion))
    except Problem as problem:
        sys.exit(str(problem))


if __name__ == "__main__":
    main(sys.argv[1:])
