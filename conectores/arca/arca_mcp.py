# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2.3.0", "cryptography>=43"]
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
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import time
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
from mcp.server.mcpserver import MCPServer

TIMEOUT = 30  # segundos por pedido
REFRESH_BEFORE = 600  # se pide un ticket nuevo cuando faltan menos de 10 min
ALIAS_RE = re.compile(r"[a-z][a-z0-9_]{1,19}")
ENVIRONMENTS = ("homologacion", "produccion")
# Direcciones oficiales. Se pueden cambiar por cuenta con "urls": {"wsaa": ..., "wsfe": ...} si ARCA cambia de dominio.
URLS = {
    "homologacion": {"wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
                     "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx"},
    "produccion": {"wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
                   "wsfe": "https://servicios1.afip.gov.ar/wsfev1/service.asmx"},
}
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
        except (urllib.error.URLError, TimeoutError) as error:
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


def login(account: dict) -> dict:
    cert, key = read_pair(account)
    now = datetime.now(AR_TZ)
    stamp = lambda moment: moment.isoformat(timespec="seconds")  # noqa: E731
    request = ('<?xml version="1.0" encoding="UTF-8"?><loginTicketRequest version="1.0"><header>'
               f"<uniqueId>{int(time.time())}</uniqueId><generationTime>{stamp(now - timedelta(minutes=10))}</generationTime>"
               f"<expirationTime>{stamp(now + timedelta(minutes=10))}</expirationTime></header><service>wsfe</service></loginTicketRequest>")
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


def ticket(account: dict) -> tuple[str, str]:
    """(token, sign) vigentes. ARCA no da un ticket nuevo mientras haya uno vivo, así que se guarda en disco."""
    cache = home() / "cache" / f"{account['alias']}-{account['ambiente']}.json"
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved["expires"] - time.time() > REFRESH_BEFORE:
            return saved["token"], saved["sign"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    data = login(account)
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


NO_RESULTS = "602"  # ARCA contesta «Sin Resultados» como un error cuando una lista está vacía


def sale_points(account: dict) -> list[ET.Element]:
    """Los puntos de venta para web services; sin ninguno, ARCA da el error 602 y acá es una lista vacía."""
    try:
        return find(wsfe(account, "FEParamGetPtosVenta"), "PtoVenta")
    except Problem as problem:
        if NO_RESULTS in problem.codes:
            return []
        raise


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
def emitir_factura(importe: float, concepto: str | None = None, documento: str | None = None, tipo_documento: str | None = None,
                   condicion_receptor: str | None = None, descripcion: str | None = None, fecha: str | None = None,
                   servicio_desde: str | None = None, servicio_hasta: str | None = None, vencimiento_pago: str | None = None,
                   punto_venta: int | None = None, cuenta: str | None = None, confirmar: str | None = None) -> str:
    """Emite una Factura C. SIN `confirmar` solo SIMULA (no emite nada) y da un código; para emitir, repetir el mismo pedido con confirmar=<código>. concepto: productos, servicios o productos_y_servicios. Sin documento es consumidor final sin identificar."""
    try:
        account = pick(cuenta)
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
        number = last_number(account, point) + 1
        request = build_invoice(account, point, number, concept, (doc_type, doc_number), issued, amount, condition, service)
        what = descripcion if descripcion is not None else account.get("descripcion")
        code = confirmation_code(account["alias"], account["ambiente"], account["cuit"], request)
        summary = [f"[{account['alias']}] {banner(account)}",
                   f"Factura C {number_text(point, number)} · {issued.isoformat()}",
                   f"Total: {money(amount, 'ARS')} · Concepto: {concept_name.replace('_', ' ')}",
                   f"Receptor: {receiver} · condición {wanted}"]
        if what:
            summary.append(f"Descripción: {one_line(what, 120)} (solo queda en el registro local; ARCA no la guarda)")
        if confirmar is None:
            return "\n".join(summary + [f"No se emitió nada. Para emitir, repetí el mismo pedido agregando confirmar={code}"])
        if confirmar.strip().lower() != code:
            return "\n".join(["El código no coincide con este comprobante: algo cambió (el número que le toca, algún dato) o ya se emitió.",
                              "No se emitió nada. Esto es lo que se emitiría ahora:"] + summary + [f"Para emitir, agregá confirmar={code}"])
        if not may_issue(account):
            raise Problem(f"La cuenta «{account['alias']}» es de PRODUCCIÓN y está en solo lectura. Para habilitar la emisión agregá "
                          f'"emision": true a esa cuenta en {home() / "cuentas.json"} (a propósito, y solo si querés emitir de verdad).')
        entry = {"fecha_registro": datetime.now(AR_TZ).isoformat(timespec="seconds"), "cuenta": account["alias"], "ambiente": account["ambiente"],
                 "punto_venta": point, "tipo": INVOICE_C, "numero": number, "fecha": issued.isoformat(), "importe": f"{amount:.2f}",
                 "receptor": f"{doc_type}:{doc_number}", "concepto": concept_name, "descripcion": what or ""}
    except Problem as problem:
        return str(problem)
    try:
        root = wsfe(account, "FECAESolicitar", request)
    except Problem as problem:
        if problem.maybe_sent:
            return settle_after_silence(account, point, number, problem, entry)
        entry.update(resultado="error", error=one_line(problem, 300))
        return f"✗ {problem}\nNo se emitió nada." + record(entry)
    detail = find(root, "FEDetResponse")
    outcome = text_of(detail[0], "Resultado") if detail else ""
    notes = observations(detail[0]) if detail else []
    if outcome != "A":
        entry.update(resultado="rechazada", observaciones=notes)
        reasons = "; ".join(notes) or "sin detalle"
        return f"✗ ARCA rechazó el comprobante: {reasons}\nNo se emitió nada." + record(entry)
    cae, expires = text_of(detail[0], "CAE"), day(text_of(detail[0], "CAEFchVto"))
    entry.update(resultado="emitida", cae=cae, cae_vencimiento=expires, observaciones=notes)
    done = [f"✓ [{account['alias']}] {banner(account)}", f"Factura C {number_text(point, number)} emitida · {issued.isoformat()}",
            f"Total: {money(amount, 'ARS')} · Receptor: {receiver}", f"CAE: {cae} · vence {expires}"]
    if notes:
        done.append("Observaciones de ARCA: " + "; ".join(notes))
    return "\n".join(done) + record(entry)


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
    accounts[alias] = {"cuit": cuit, "ambiente": environment, "condicion": condition}
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
