import asyncio
import base64
import importlib.util
import json
import os
import re
import stat
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from epsilon.connectors import bridge

SCRIPT = Path(__file__).resolve().parents[2] / "conectores" / "arca" / "arca_mcp.py"
CUIT = "20123456789"


@pytest.fixture
def arca(monkeypatch, tmp_path):
    for key in [k for k in os.environ if k.startswith("ARCA_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("ARCA_HOME", str(tmp_path / "arca"))
    spec = importlib.util.spec_from_file_location("arca_mcp", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó hablar con ARCA de verdad")

    monkeypatch.setattr(module, "_post", forbidden)  # el ÚNICO punto de red
    original = module.emitir_factura

    def with_required_data(*args, **kwargs):
        """La mayoría de los tests no se ocupan de los datos obligatorios: se completan, salvo que el test los pase (aunque sea None)."""
        kwargs.setdefault("documento", "12345678")
        kwargs.setdefault("descripcion", "Repuestos y reparaciones")
        return original(*args, **kwargs)

    module.emitir_factura = with_required_data
    return module


def make_files(folder: Path, alias="negocio", cuit=CUIT, days=365, other_key=False):
    """Escribe una clave y un certificado autofirmado de mentira, como los que bajaría ARCA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, alias), x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(days=30)).not_valid_after(now + timedelta(days=days))
            .sign(key, hashes.SHA256()))
    shown = rsa.generate_private_key(public_exponent=65537, key_size=2048) if other_key else key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{alias}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / f"{alias}.key").write_bytes(shown.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))


def write_accounts(arca, **accounts):
    folder = arca.home()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cuentas.json").write_text(json.dumps(accounts))


@pytest.fixture
def ready(arca):
    """Una cuenta de monotributo en homologación con su certificado."""
    write_accounts(arca, negocio={"cuit": CUIT, "condicion": "monotributo", "punto_venta": 3})
    make_files(arca.home())
    return arca


def envelope(body: str) -> str:
    return f'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>{body}</soap:Body></soap:Envelope>'


def wsaa_answer(minutes=720):
    expires = (datetime.now(timezone(timedelta(hours=-3))) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    inner = f"<loginTicketResponse><credentials><token>TOKEN-SECRETO</token><sign>SIGN-SECRETO</sign></credentials><header><expirationTime>{expires}</expirationTime></header></loginTicketResponse>"
    return envelope(f"<loginCmsResponse><loginCmsReturn>{escape(inner)}</loginCmsReturn></loginCmsResponse>")


def wsfe_answer(method: str, result: str) -> str:
    return envelope(f'<{method}Response xmlns="http://ar.gov.afip.dif.FEV1/"><{method}Result>{result}</{method}Result></{method}Response>')


NOBODY = {"getIdPersonaListByDocumento": '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><r><idPersonaListReturn/></r></soap:Body></soap:Envelope>',
          "getPersonaV2": '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><r><personaReturn/></r></soap:Body></soap:Envelope>'}


def serve(arca, monkeypatch, wsfe: dict, wsaa=None):
    """Instala un ARCA falso: devuelve la lista de pedidos (url, acción, cuerpo)."""
    calls = []

    def fake(url, action, body):
        calls.append((url, action, body))
        if "LoginCms" in url:
            return (wsaa or (200, wsaa_answer()))
        method = action.rsplit("/", 1)[-1]
        if method not in wsfe and method in NOBODY:
            return 200, NOBODY[method]  # el padrón, si el test no dijo nada, no conoce a nadie
        answer = wsfe[method]  # un método que el test no esperaba llamar da KeyError: falla fuerte
        return 200, (answer(body) if callable(answer) else answer)

    monkeypatch.setattr(arca, "_post", fake)
    return calls


def sent(calls, method):
    """Los cuerpos de los pedidos que se mandaron a un método de WSFE."""
    return [c[2] for c in calls if c[1].endswith("/" + method)]


LAST = wsfe_answer("FECompUltimoAutorizado", "<PtoVta>3</PtoVta><CbteTipo>11</CbteTipo><CbteNro>45</CbteNro>")
DUMMY = wsfe_answer("FEDummy", "<AppServer>OK</AppServer><DbServer>OK</DbServer><AuthServer>OK</AuthServer>")
POINTS = wsfe_answer("FEParamGetPtosVenta", "<ResultGet><PtoVenta><Nro>3</Nro><EmisionTipo>CAE</EmisionTipo><Bloqueado>N</Bloqueado><FchBaja>NULL</FchBaja></PtoVenta></ResultGet>")
CONSULT = wsfe_answer("FECompConsultar", (
    "<ResultGet><Concepto>2</Concepto><DocTipo>96</DocTipo><DocNro>12345678</DocNro><CbteDesde>45</CbteDesde><CbteFch>20261023</CbteFch>"
    "<ImpTotal>15000.50</ImpTotal><ImpNeto>15000.50</ImpNeto><ImpIVA>0.00</ImpIVA><FchServDesde>20261001</FchServDesde>"
    "<FchServHasta>20261031</FchServHasta><FchVtoPago>20261110</FchVtoPago><MonId>PES</MonId><Resultado>A</Resultado>"
    "<CodAutorizacion>74123456789012</CodAutorizacion><FchVto>20261102</FchVto></ResultGet>"))


def test_script_declares_its_own_dependencies():
    head = SCRIPT.read_text().split('"""')[0]

    assert "# /// script" in head and '"mcp' in head and '"cryptography' in head


def test_server_defines_its_tools_and_the_buttons_fit_them(arca):
    found = {t.name: t.input_schema for t in asyncio.run(arca.server.list_tools())}

    assert set(found) == {"cuentas", "verificar", "puntos_de_venta", "tipos_de_comprobante", "ultimo_comprobante", "ver_comprobante",
                          "condiciones_iva_receptor", "actividades", "emitir_factura", "buscar_persona", "pdf_comprobante", "emitir_lote"}
    for button in json.loads(arca.buttons()):
        assert button["tool"] in found and not found[button["tool"]].get("required")


def test_new_account_makes_a_private_key_a_csr_with_the_cuit_and_the_entry(arca):
    text = arca.create_account("negocio", CUIT, "Mi Negocio", "homologacion", "monotributo")

    folder = arca.home()
    assert stat.S_IMODE((folder / "negocio.key").stat().st_mode) == 0o600
    csr = x509.load_pem_x509_csr((folder / "negocio.csr").read_bytes())
    assert csr.is_signature_valid
    assert csr.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)[0].value == f"CUIT {CUIT}"
    assert csr.subject.get_attributes_for_oid(NameOID.COUNTRY_NAME)[0].value == "AR"
    assert json.loads((folder / "cuentas.json").read_text()) == {"negocio": {"cuit": CUIT, "ambiente": "homologacion", "condicion": "monotributo", "nombre": "Mi Negocio"}}
    assert "WSASS" in text and "negocio.crt" in text
    key_before = (folder / "negocio.key").read_bytes()
    with pytest.raises(arca.Problem, match="ya existe"):
        arca.create_account("negocio", CUIT, "Otro", "homologacion", "monotributo")
    assert (folder / "negocio.key").read_bytes() == key_before  # nunca pisa una clave


@pytest.mark.parametrize("alias, cuit, message", [("Mal Nombre", CUIT, "minúsculas"), ("ok", "20-123", "11 números")])
def test_new_account_validates_what_it_writes(arca, alias, cuit, message):
    with pytest.raises(arca.Problem, match=message):
        arca.create_account(alias, cuit, "x", "homologacion", "monotributo")
    assert not (arca.home() / "cuentas.json").exists()


def test_accounts_are_validated_and_the_default_rules_apply(arca, monkeypatch):
    assert "No hay cuentas de ARCA" in arca.cuentas()
    write_accounts(arca, uno={"cuit": CUIT}, dos={"cuit": CUIT, "ambiente": "produccion"})
    assert arca.pick("UNO")["ambiente"] == "homologacion"  # sin decir: homologación, nunca producción por descuido
    with pytest.raises(arca.Problem, match="cuenta=<nombre>"):
        arca.pick(None)
    monkeypatch.setenv("ARCA_DEFAULT", "dos")
    assert arca.pick(None)["alias"] == "dos"
    with pytest.raises(arca.Problem, match="No existe la cuenta"):
        arca.pick("tres")
    write_accounts(arca, malo={"cuit": "123"})
    assert "11 números" in arca.cuentas()
    write_accounts(arca, malo={"cuit": CUIT, "ambiente": "otro"})
    assert "homologacion o produccion" in arca.cuentas()
    write_accounts(arca, malo={"cuit": CUIT, "urls": {"wsaa": "http://inseguro"}})
    assert "https://" in arca.cuentas()


def test_cuentas_says_whether_the_certificate_is_there_and_never_shows_paths_or_keys(ready):
    assert "negocio (predeterminada): CUIT 20123456789 · homologacion · listo" in ready.cuentas()
    (ready.home() / "negocio.crt").unlink()
    assert "falta el certificado o la clave" in ready.cuentas()


def test_login_signs_a_valid_cms_calls_wsaa_and_caches_the_ticket_with_private_permissions(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})

    first = ready.ultimo_comprobante()
    second = ready.ultimo_comprobante()

    assert first == second == "[negocio] Último Factura C: 0003-00000045"
    logins = [c for c in calls if "LoginCms" in c[0]]
    assert len(logins) == 1  # el segundo pedido usa el ticket guardado
    assert logins[0][0] == ready.URLS["homologacion"]["wsaa"]
    cms = base64.b64decode(logins[0][2].split("<in0>")[1].split("</in0>")[0])
    signed = pkcs7.load_der_pkcs7_certificates(cms)
    assert signed and signed[0].subject.rfc4514_string().count(CUIT) == 1  # el CMS lleva el certificado
    cache = ready.home() / "cache" / "negocio-homologacion.json"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    sent = [c[2] for c in calls if "LoginCms" not in c[0]][0]
    assert "<Token>TOKEN-SECRETO</Token>" in sent and f"<Cuit>{CUIT}</Cuit>" in sent


def test_an_expiring_ticket_is_renewed(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})
    cache = ready.home() / "cache"
    cache.mkdir(parents=True)
    (cache / "negocio-homologacion.json").write_text(json.dumps({"token": "viejo", "sign": "viejo", "expires": time.time() + 60}))

    ready.ultimo_comprobante()

    assert any("LoginCms" in c[0] for c in calls)


def test_last_voucher_defaults_to_factura_c_for_monotributo_and_the_saved_sale_point(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})

    ready.ultimo_comprobante()

    body = [c[2] for c in calls if "LoginCms" not in c[0]][0]
    assert "<PtoVta>3</PtoVta><CbteTipo>11</CbteTipo>" in body


def test_last_voucher_asks_for_what_it_cannot_guess(arca, monkeypatch):
    write_accounts(arca, ri={"cuit": CUIT, "condicion": "responsable_inscripto"})
    make_files(arca.home(), alias="ri")
    serve(arca, monkeypatch, {"FECompUltimoAutorizado": LAST})

    assert "tipo=<número>" in arca.ultimo_comprobante(punto_venta=1)
    assert "punto_venta=<número>" in arca.ultimo_comprobante(tipo=6)
    assert "entre 1 y 999" in arca.ultimo_comprobante(tipo=0, punto_venta=1)


def test_last_voucher_when_there_are_none_yet(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": wsfe_answer("FECompUltimoAutorizado", "<CbteNro>0</CbteNro>")})

    assert "todavía no hay comprobantes" in ready.ultimo_comprobante()


def test_sale_points_and_voucher_types_are_listed(ready, monkeypatch):
    serve(ready, monkeypatch, {
        "FEParamGetPtosVenta": POINTS,
        "FEParamGetTiposCbte": wsfe_answer("FEParamGetTiposCbte", "<ResultGet><CbteTipo><Id>11</Id><Desc>Factura C</Desc></CbteTipo><CbteTipo><Id>13</Id><Desc>Nota de Credito C</Desc></CbteTipo></ResultGet>")})

    assert "- 3 · CAE · activo" in ready.puntos_de_venta()
    assert "- 11 · Factura C" in ready.tipos_de_comprobante() and "- 13 · Nota de Credito C" in ready.tipos_de_comprobante()


def test_no_sale_points_explains_which_kind_to_create(ready, monkeypatch):
    serve(ready, monkeypatch, {"FEParamGetPtosVenta": wsfe_answer("FEParamGetPtosVenta", "")})

    assert "RECE para aplicativo y web services" in ready.puntos_de_venta()


def test_arca_answering_602_to_the_sale_points_request_means_there_are_none(ready, monkeypatch):
    # Lo que contestó ARCA de verdad en homologación cuando todavía no había ningún punto de venta.
    none = wsfe_answer("FEParamGetPtosVenta", "<Errors><Err><Code>602</Code><Msg>Sin Resultados: - Metodo FEParamGetPtosVenta</Msg></Err></Errors>")
    serve(ready, monkeypatch, {"FEParamGetPtosVenta": none, "FEDummy": DUMMY})

    assert "No hay puntos de venta para web services" in ready.puntos_de_venta()
    text = ready.verificar()
    assert "✗ No hay puntos de venta para web services" in text and "Sin Resultados" not in text
    assert text.count("✗") == 1  # lo demás sigue en verde


def test_other_arca_errors_on_the_sale_points_are_not_hidden(ready, monkeypatch):
    broken = wsfe_answer("FEParamGetPtosVenta", "<Errors><Err><Code>500</Code><Msg>Error interno</Msg></Err></Errors>")
    serve(ready, monkeypatch, {"FEParamGetPtosVenta": broken})

    assert ready.puntos_de_venta() == "ARCA: Error interno (código 500)"


def test_one_voucher_shows_amounts_receiver_service_dates_and_cae(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompConsultar": CONSULT})

    text = ready.ver_comprobante(45)

    assert "<CbteTipo>11</CbteTipo><CbteNro>45</CbteNro><PtoVta>3</PtoVta>" in [c[2] for c in calls][-1].replace("<FeCompConsReq>", "")
    assert "Factura C 0003-00000045 · 2026-10-23" in text
    assert "Total: ARS 15.000,50 (neto 15.000,50 · IVA 0,00)" in text
    assert "Receptor: DNI 12345678" in text
    assert "Servicios (2026-10-01 a 2026-10-31, vence el pago 2026-11-10)" in text
    assert "CAE: 74123456789012 · vence 2026-11-02 · aprobado" in text
    assert "positivo" in ready.ver_comprobante(0)


def test_arca_errors_become_clear_messages(ready, monkeypatch):
    error = wsfe_answer("FECompConsultar", "<Errors><Err><Code>602</Code><Msg>No existen datos en nuestros registros</Msg></Err></Errors>")
    serve(ready, monkeypatch, {"FECompConsultar": error})

    assert ready.ver_comprobante(99) == "ARCA: No existen datos en nuestros registros (código 602)"


@pytest.mark.parametrize("fault, expected", [
    ("ns1:coe.alreadyAuthenticated: El CEE ya posee un TA valido", "ya hay un ticket vigente"),
    ("ns1:cms.cert.untrusted: Certificado no emitido por AC de confianza", "homologación y producción son distintos"),
    ("otra cosa", "ARCA rechazó el pedido: otra cosa")])
def test_wsaa_faults_are_explained(ready, monkeypatch, fault, expected):
    serve(ready, monkeypatch, {}, wsaa=(500, envelope(f"<soap:Fault><faultcode>x</faultcode><faultstring>{fault}</faultstring></soap:Fault>")))

    assert expected in ready.ultimo_comprobante()


def test_network_failure_and_garbage_are_reported_without_a_traceback(ready, monkeypatch):
    def down(url, action, body):
        raise ready.Problem("No pude hablar con ARCA (wsaahomo.afip.gov.ar): URLError.")

    monkeypatch.setattr(ready, "_post", down)
    assert "No pude hablar con ARCA" in ready.ultimo_comprobante()

    monkeypatch.setattr(ready, "_post", lambda url, action, body: (502, "<html>Bad gateway"))
    assert "no entiendo (código 502)" in ready.ultimo_comprobante()


def test_missing_files_say_where_to_put_them(arca):
    write_accounts(arca, negocio={"cuit": CUIT})
    make_files(arca.home())
    (arca.home() / "negocio.crt").unlink()

    assert "Falta el certificado" in arca.ultimo_comprobante(tipo=11, punto_venta=1) and "negocio.crt" in arca.ultimo_comprobante(tipo=11, punto_venta=1)
    (arca.home() / "negocio.key").unlink()
    assert "Falta la clave privada" in arca.ultimo_comprobante(tipo=11, punto_venta=1)


def test_verify_checks_everything_in_order(ready, monkeypatch):
    serve(ready, monkeypatch, {"FEDummy": DUMMY, "FEParamGetPtosVenta": POINTS})

    text = ready.verificar()

    assert text.splitlines()[0] == f"Cuenta negocio · CUIT {CUIT} · homologacion"
    assert "✗" not in text
    for expected in ["Certificado y clave encontrados", "Certificado vigente", "El CUIT del certificado coincide",
                     "La clave corresponde al certificado", "ARCA aceptó el ingreso", "AppServer OK", "1 punto(s) de venta"]:
        assert expected in text


def test_verify_catches_a_mismatched_key_an_expired_certificate_and_a_wrong_cuit(arca, monkeypatch):
    write_accounts(arca, negocio={"cuit": "27999999999"})
    make_files(arca.home(), days=-5, other_key=True)
    serve(arca, monkeypatch, {"FEDummy": DUMMY, "FEParamGetPtosVenta": POINTS})

    text = arca.verificar()

    assert "✗ El certificado venció" in text
    assert "✗ El certificado no es de este CUIT" in text
    assert "✗ La clave NO corresponde" in text


def test_verify_reports_a_missing_certificate_instead_of_failing(arca):
    write_accounts(arca, negocio={"cuit": CUIT})

    text = arca.verificar()

    assert text.splitlines()[0].startswith("Cuenta negocio") and "✗ Falta la clave privada" in text


def test_secrets_never_reach_the_answers(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FEDummy": DUMMY, "FEParamGetPtosVenta": POINTS})
    key_text = (ready.home() / "negocio.key").read_text()

    everything = ready.cuentas() + ready.verificar() + ready.ultimo_comprobante() + ready.puntos_de_venta()

    assert "TOKEN-SECRETO" not in everything and "SIGN-SECRETO" not in everything
    assert key_text.splitlines()[1] not in everything


# ---------- emitir una Factura C ----------

def approved(number=46, extra=""):
    return wsfe_answer("FECAESolicitar", (
        "<FeCabResp><Cuit>20123456789</Cuit><PtoVta>3</PtoVta><CbteTipo>11</CbteTipo><Resultado>A</Resultado></FeCabResp>"
        f"<FeDetResp><FECAEDetResponse><Concepto>3</Concepto><DocTipo>99</DocTipo><DocNro>0</DocNro><CbteDesde>{number}</CbteDesde>"
        f"<CbteHasta>{number}</CbteHasta><Resultado>A</Resultado><CAE>74123456789012</CAE><CbteFch>20261023</CbteFch>"
        f"<CAEFchVto>20261102</CAEFchVto>{extra}</FECAEDetResponse></FeDetResp>"))


def rejected(obs):
    return wsfe_answer("FECAESolicitar", (
        "<FeCabResp><Resultado>R</Resultado></FeCabResp><FeDetResp><FECAEDetResponse><Resultado>R</Resultado>"
        f"<CAE></CAE>{obs}</FECAEDetResponse></FeDetResp>"))


def the_code(text):
    return re.search(r"confirmar=([0-9a-f]{6})", text).group(1)


def issue(ready, monkeypatch, answer=None, **fields):
    """Simula y después confirma con el código que dio la simulación. Devuelve (texto emitido, pedidos a ARCA)."""
    fields.setdefault("importe", 500)
    fields.setdefault("concepto", "productos_y_servicios")
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FECAESolicitar": answer or approved()})
    code = the_code(ready.emitir_factura(**fields))
    return ready.emitir_factura(confirmar=code, **fields), calls


def test_simulating_shows_what_would_be_issued_and_sends_nothing(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})  # FECAESolicitar no está: llamarlo daría KeyError

    text = ready.emitir_factura(importe=500, concepto="productos_y_servicios", documento=None, sin_identificar=True)

    for expected in ["HOMOLOGACIÓN", "Factura C 0003-00000046", "Total: ARS 500,00", "productos y servicios",
                     "Consumidor final (sin identificar)", "condición consumidor_final", "No se emitió nada"]:
        assert expected in text
    assert the_code(text) and not sent(calls, "FECAESolicitar")
    assert not (ready.home() / "emitidas.jsonl").exists()


def test_confirming_with_the_code_sends_the_invoice_in_the_order_the_manual_requires(ready, monkeypatch):
    text, calls = issue(ready, monkeypatch, descripcion="Repuestos y reparaciones", documento="12345678")

    assert text.splitlines()[:4] == [
        "✓ [negocio] HOMOLOGACIÓN (pruebas, sin validez fiscal)", f"Factura C 0003-00000046 emitida · {datetime.now(ready.AR_TZ).date().isoformat()}",
        "Total: ARS 500,00 · Receptor: DNI 12345678", "CAE: 74123456789012 · vence 2026-11-02"]
    request = ET.fromstring(sent(calls, "FECAESolicitar")[0])
    names = [e.tag.rsplit("}", 1)[-1] for e in next(e for e in request.iter() if e.tag.endswith("FECAEDetRequest"))]
    assert names == ["Concepto", "DocTipo", "DocNro", "CbteDesde", "CbteHasta", "CbteFch", "ImpTotal", "ImpTotConc", "ImpNeto", "ImpOpEx",
                     "ImpTrib", "ImpIVA", "FchServDesde", "FchServHasta", "FchVtoPago", "MonId", "MonCotiz", "CondicionIVAReceptorId"]
    values = {e.tag.rsplit("}", 1)[-1]: e.text for e in request.iter() if e.text and e.text.strip()}
    today = datetime.now(ready.AR_TZ).date().strftime("%Y%m%d")
    assert (values["Concepto"], values["DocTipo"], values["DocNro"], values["CbteDesde"], values["CbteHasta"]) == ("3", "96", "12345678", "46", "46")
    assert (values["ImpTotal"], values["ImpNeto"], values["ImpTotConc"], values["ImpOpEx"], values["ImpTrib"], values["ImpIVA"]) == (
        "500.00", "500.00", "0.00", "0.00", "0.00", "0.00")  # tipo C: el neto es el subtotal, no hay IVA
    assert (values["CbteFch"], values["FchServDesde"], values["FchServHasta"], values["FchVtoPago"]) == (today,) * 4
    assert (values["MonId"], values["MonCotiz"], values["CondicionIVAReceptorId"], values["PtoVta"], values["CbteTipo"]) == ("PES", "1", "5", "3", "11")
    assert "<Iva>" not in sent(calls, "FECAESolicitar")[0] and "<Actividades>" not in sent(calls, "FECAESolicitar")[0]
    logged = json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])
    assert logged["resultado"] == "emitida" and logged["cae"] == "74123456789012" and logged["numero"] == 46
    assert logged["descripcion"] == "Repuestos y reparaciones" and logged["ambiente"] == "homologacion"
    assert stat.S_IMODE((ready.home() / "emitidas.jsonl").stat().st_mode) == 0o600


def test_products_only_sends_no_service_dates_and_services_send_the_given_ones(ready, monkeypatch):
    _, calls = issue(ready, monkeypatch, concepto="productos")
    assert "FchServDesde" not in sent(calls, "FECAESolicitar")[0]

    _, calls = issue(ready, monkeypatch, concepto="servicios", fecha="2026-10-23", servicio_desde="2026-10-01",
                     servicio_hasta="2026-10-31", vencimiento_pago="2026-11-10")
    body = sent(calls, "FECAESolicitar")[0]
    assert "<FchServDesde>20261001</FchServDesde><FchServHasta>20261031</FchServHasta><FchVtoPago>20261110</FchVtoPago>" in body


def test_the_activity_goes_last_and_only_when_the_account_has_one(arca, monkeypatch):
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3, "concepto": "productos", "actividad": "479900"})
    make_files(arca.home())

    _, calls = issue(arca, monkeypatch)

    assert "<CondicionIVAReceptorId>5</CondicionIVAReceptorId><Actividades><Actividad><Id>479900</Id></Actividad></Actividades>" in sent(calls, "FECAESolicitar")[0]
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3, "concepto": "productos", "actividad": "venta minorista"})
    assert "hasta 6 números" in arca.emitir_factura(importe=10, documento=None, sin_identificar=True)


def test_the_code_only_works_once_for_exactly_that_invoice(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})  # sin FECAESolicitar: ninguna de estas confirmaciones puede emitir
    code = the_code(ready.emitir_factura(importe=500, concepto="productos"))

    for changed in [{"importe": 501}, {"importe": 500, "documento": "87654321"}, {"importe": 500, "fecha": "2026-10-22"}]:
        refused = ready.emitir_factura(concepto="productos", confirmar=code, **changed)
        assert "El código no coincide" in refused and "No se emitió nada" in refused
    assert "El código no coincide" in ready.emitir_factura(importe=500, concepto="productos", confirmar="true")

    # Alguien (o este mismo comando, repetido) ya emitió el 46: el que ahora toca es el 47 y el código viejo no sirve.
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": wsfe_answer("FECompUltimoAutorizado", "<CbteNro>46</CbteNro>")})
    again = ready.emitir_factura(importe=500, concepto="productos", confirmar=code)
    assert "El código no coincide" in again and "0003-00000047" in again


def test_production_is_read_only_unless_the_account_enables_emission_on_purpose(arca, monkeypatch):
    write_accounts(arca, prod={"cuit": CUIT, "ambiente": "produccion", "punto_venta": 3, "concepto": "productos"})
    make_files(arca.home(), alias="prod")
    assert "prod (predeterminada): CUIT 20123456789 · produccion · listo · solo lectura" in arca.cuentas()
    serve(arca, monkeypatch, {"FECompUltimoAutorizado": LAST})  # FECAESolicitar no está: no puede emitir

    simulation = arca.emitir_factura(importe=500)
    assert "PRODUCCIÓN (comprobante real)" in simulation
    refused = arca.emitir_factura(importe=500, confirmar=the_code(simulation))
    assert "PRODUCCIÓN y está en solo lectura" in refused and '"emision": true' in refused
    assert not (arca.home() / "emitidas.jsonl").exists()

    write_accounts(arca, prod={"cuit": CUIT, "ambiente": "produccion", "punto_venta": 3, "concepto": "productos", "emision": True})
    assert "solo lectura" not in arca.cuentas()
    text, _ = issue(arca, monkeypatch)
    assert text.startswith("✓ [prod] PRODUCCIÓN (comprobante real)")
    write_accounts(arca, prod={"cuit": CUIT, "ambiente": "produccion", "punto_venta": 3, "concepto": "productos", "emision": "true"})
    assert "solo lectura" in arca.cuentas()  # solo el booleano verdadero habilita: ni el texto «true» ni nada parecido


@pytest.mark.parametrize("kind, number, expected", [
    (None, None, (99, 0, "Consumidor final (sin identificar)")),
    (None, "12.345.678", (96, 12345678, "DNI 12345678")),
    (None, "20-40937847-2", (86, 20409378472, "CUIL 20409378472")),
    (None, "30712345671", (80, 30712345671, "CUIT 30712345671")),
    ("cuit", "20409378472", (80, 20409378472, "CUIT 20409378472")),
    ("DNI", "5000000", (96, 5000000, "DNI 5000000"))])
def test_the_receiver_document_is_read_the_way_a_person_writes_it(arca, kind, number, expected):
    assert arca.parse_receiver(kind, number) == expected


@pytest.mark.parametrize("kind, number, message", [
    (None, "20409378473", "no es válido"), (None, "123456789", "no es válido"), (None, "12a45678", "solo números"),
    ("dni", None, "falta el número"), ("pasaporte", "123", "dni, cuil o cuit"), ("dni", "123456789", "hasta 8"), ("dni", "0", "hasta 8")])
def test_a_wrong_receiver_is_refused_before_asking_arca(arca, kind, number, message):
    with pytest.raises(arca.Problem, match=message):
        arca.parse_receiver(kind, number)


@pytest.mark.parametrize("value, expected", [(500, "500.00"), (1500.5, "1500.50"), ("20.05", "20.05"), (0.01, "0.01")])
def test_amounts_are_exact_with_two_decimals(arca, value, expected):
    assert f"{arca.parse_amount(value)}" == expected and isinstance(arca.parse_amount(value), Decimal)


@pytest.mark.parametrize("value, message", [(0, "mayor a 0"), (-5, "mayor a 0"), (10.005, "2 decimales"), (0.1 + 0.2, "2 decimales"),
                                            (float("nan"), "no es un número"), (10 ** 12, "mayor a 0")])
def test_bad_amounts_are_refused_and_never_rounded_silently(arca, value, message):
    with pytest.raises(arca.Problem, match=message):
        arca.parse_amount(value)


def test_dates_follow_arca_windows_and_the_service_period_must_make_sense(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})
    today = datetime.now(ready.AR_TZ).date()
    day = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731

    assert "hasta 5 días" in ready.emitir_factura(importe=10, concepto="productos", fecha=day(-6))
    assert "No se emitió nada" in ready.emitir_factura(importe=10, concepto="productos", fecha=day(5))
    assert "hasta 10 días" in ready.emitir_factura(importe=10, concepto="servicios", fecha=day(11))
    assert "No se emitió nada" in ready.emitir_factura(importe=10, concepto="servicios", fecha=day(-10))
    assert "AAAA-MM-DD" in ready.emitir_factura(importe=10, concepto="productos", fecha="23/10/2026")
    assert "no puede ser anterior a «servicio_desde»" in ready.emitir_factura(importe=10, concepto="servicios", servicio_desde=day(0), servicio_hasta=day(-1))
    assert "vencimiento_pago" in ready.emitir_factura(importe=10, concepto="servicios", vencimiento_pago=day(-1))


def test_the_concept_is_asked_for_unless_the_account_has_a_default(arca, monkeypatch):
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3})
    make_files(arca.home())
    serve(arca, monkeypatch, {"FECompUltimoAutorizado": LAST})

    assert "Decime el concepto" in arca.emitir_factura(importe=10)
    assert "Decime el concepto" in arca.emitir_factura(importe=10, concepto="regalos")
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3, "concepto": "servicios"})
    assert "servicios" in arca.emitir_factura(importe=10) and "Decime" not in arca.emitir_factura(importe=10)


def test_receiver_condition_accepts_a_name_or_a_number(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})

    assert "condición monotributo" in ready.emitir_factura(importe=10, concepto="productos", condicion_receptor="Monotributo")
    assert "condición 13" in ready.emitir_factura(importe=10, concepto="productos", condicion_receptor="13")
    assert "condicion_receptor: uno de" in ready.emitir_factura(importe=10, concepto="productos", condicion_receptor="otra")


def test_arca_rejecting_the_invoice_says_why_and_logs_it(ready, monkeypatch):
    for index, obs in enumerate(["<Observaciones><Obs><Code>10238</Code><Msg>La CUIT receptora ingresada no existe.</Msg></Obs></Observaciones>",
                                 "<Obs><Observaciones><Code>10245</Code><Msg>Falta la condición del receptor.</Msg></Observaciones></Obs>"]):
        text, _ = issue(ready, monkeypatch, answer=rejected(obs))

        assert text.startswith("✗ ARCA rechazó el comprobante: ") and "No se emitió nada" in text and "CAE" not in text
        assert ("(código 10238)" if index == 0 else "(código 10245)") in text
    logged = [json.loads(line) for line in (ready.home() / "emitidas.jsonl").read_text().splitlines()]
    assert [e["resultado"] for e in logged] == ["rechazada", "rechazada"] and "cae" not in logged[0]


def test_an_arca_error_such_as_a_wrong_sequence_number_is_reported_and_logged(ready, monkeypatch):
    error = wsfe_answer("FECAESolicitar", "<Errors><Err><Code>10016</Code><Msg>El número de comprobante debe ser mayor en 1 al último</Msg></Err></Errors>")

    text, _ = issue(ready, monkeypatch, answer=error)

    assert text.startswith("✗ ARCA: El número de comprobante debe ser mayor en 1 al último (código 10016)\nNo se emitió nada.")
    assert json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])["resultado"] == "error"


def test_an_approval_with_warnings_shows_them(ready, monkeypatch):
    text, _ = issue(ready, monkeypatch, answer=approved(extra="<Obs><Code>10063</Code><Msg>Revisá la categoría</Msg></Obs>"))

    assert text.startswith("✓") and "Observaciones de ARCA: Revisá la categoría (código 10063)" in text


def silent(ready, monkeypatch, after):
    """ARCA recibe el pedido pero no contesta. `after` es el último número que ve ARCA al mirar de nuevo (None: tampoco contesta).
    El último número se lee 3 veces: al simular, al confirmar (para recalcular el que toca) y después del silencio."""
    lasts = []

    def fake(url, action, body):
        if "LoginCms" in url:
            return 200, wsaa_answer()
        method = action.rsplit("/", 1)[-1]
        if method == "FECompUltimoAutorizado":
            lasts.append(1)
            if len(lasts) <= 2:
                return 200, LAST
            if after is None:
                raise ready.Problem("No pude hablar con ARCA (host): URLError.", maybe_sent=True)
            return 200, wsfe_answer("FECompUltimoAutorizado", f"<CbteNro>{after}</CbteNro>")
        raise ready.Problem("No pude hablar con ARCA (host): TimeoutError.", maybe_sent=True)

    monkeypatch.setattr(ready, "_post", fake)
    return lasts


@pytest.mark.parametrize("after, expected, result", [
    (46, "ARCA SÍ emitió el comprobante 0003-00000046", "emitida_sin_respuesta"),
    (45, "ARCA no emitió nada (el último comprobante sigue siendo el 45)", "no_emitida"),
    (None, "No repitas el comando", "sin_confirmar")])
def test_when_arca_goes_silent_after_the_request_it_checks_before_saying_anything_and_never_retries(ready, monkeypatch, after, expected, result):
    lasts = silent(ready, monkeypatch, after)
    code = the_code(ready.emitir_factura(importe=500, concepto="productos"))

    text = ready.emitir_factura(importe=500, concepto="productos", confirmar=code)

    assert expected in text
    assert len(lasts) == 3  # simular, confirmar y comprobar: el pedido de emisión no se reintenta
    assert json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])["resultado"] == result


def test_a_failure_that_certainly_did_not_reach_arca_is_not_treated_as_uncertain(ready, monkeypatch):
    fault = envelope("<soap:Fault><faultcode>x</faultcode><faultstring>token vencido</faultstring></soap:Fault>")
    text, _ = issue(ready, monkeypatch, answer=fault)

    assert text.startswith("✗ ARCA rechazó el pedido: token vencido\nNo se emitió nada.")


def test_the_log_survives_a_failure_to_write_it(ready, monkeypatch):
    (ready.home() / "emitidas.jsonl").mkdir()  # no se puede abrir como archivo

    text, _ = issue(ready, monkeypatch)

    assert text.startswith("✓") and "No pude anotarlo en el registro local" in text  # emitió igual y avisa


def test_receiver_conditions_are_read_from_arca(ready, monkeypatch):
    answer = wsfe_answer("FEParamGetCondicionIvaReceptor", (
        "<ResultGet><CondicionIvaReceptor><Id>5</Id><Desc>Consumidor Final</Desc><Cmp_Clase>C</Cmp_Clase></CondicionIvaReceptor>"
        "<CondicionIvaReceptor><Id>6</Id><Desc>Responsable Monotributo</Desc><Cmp_Clase>C</Cmp_Clase></CondicionIvaReceptor></ResultGet>"))
    calls = serve(ready, monkeypatch, {"FEParamGetCondicionIvaReceptor": answer})

    text = ready.condiciones_iva_receptor()

    assert "- 5 · Consumidor Final" in text and "- 6 · Responsable Monotributo" in text
    assert "<ClaseCmp>C</ClaseCmp>" in sent(calls, "FEParamGetCondicionIvaReceptor")[0]


def test_a_command_line_like_the_one_a_person_types_reaches_the_tool_with_the_right_types(arca):
    schema = {t.name: t.input_schema for t in asyncio.run(arca.server.list_tools())}["emitir_factura"]

    parsed = bridge.parse_cli(schema, "importe=1500.50 concepto=productos documento=12345678 punto_venta=2 confirmar=123456")

    assert parsed == {"importe": 1500.5, "concepto": "productos", "documento": "12345678", "punto_venta": 2, "confirmar": "123456"}
    assert schema["required"] == ["importe"]


def test_the_manual_names_the_detail_block_differently_from_what_arca_sends_and_both_are_read(ready, monkeypatch):
    manual_style = approved().replace("FECAEDetResponse", "FEDetResponse")

    text, _ = issue(ready, monkeypatch, answer=manual_style)

    assert text.startswith("✓") and "CAE: 74123456789012" in text


@pytest.mark.parametrize("answer", [
    wsfe_answer("FECAESolicitar", "<Algo>raro</Algo>"),  # no hay bloque de detalle
    wsfe_answer("FECAESolicitar", "<FeDetResp><FECAEDetResponse><Resultado>P</Resultado></FECAEDetResponse></FeDetResp>"),  # resultado desconocido
])
def test_an_answer_it_cannot_read_never_claims_nothing_was_issued_without_looking(ready, monkeypatch, answer):
    # La primera lectura del último número es la simulación, la segunda la confirmación y la tercera, tras no entender: ve el 46.
    state = {"n": 0}

    def last(body):
        state["n"] += 1
        return wsfe_answer("FECompUltimoAutorizado", f"<CbteNro>{45 if state['n'] <= 2 else 46}</CbteNro>")

    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": last, "FECAESolicitar": answer})
    code = the_code(ready.emitir_factura(importe=500, concepto="productos"))

    text = ready.emitir_factura(importe=500, concepto="productos", confirmar=code)

    assert "ARCA SÍ emitió el comprobante 0003-00000046" in text and "No se emitió nada" not in text
    assert len(sent(calls, "FECAESolicitar")) == 1  # y no se reintenta
    logged = json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])
    assert logged["resultado"] == "emitida_sin_respuesta" and "respuesta" in logged  # queda la respuesta cruda para diagnosticar


def test_a_rejection_without_observations_points_to_the_raw_answer(ready, monkeypatch):
    text, _ = issue(ready, monkeypatch, answer=rejected(""))

    assert "✗ ARCA rechazó el comprobante: sin más detalle" in text and "No se emitió nada" in text
    assert "Resultado=R" in json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])["respuesta"]


ACTIVITIES = wsfe_answer("FEParamGetActividades", (
    "<ResultGet><ActividadesTipo><Id>620100</Id><Orden>1</Orden><Desc>SERVICIOS DE CONSULTORES EN INFORMATICA</Desc></ActividadesTipo>"
    "<ActividadesTipo><Id>479900</Id><Orden>2</Orden><Desc>VENTA AL POR MENOR NO REALIZADA EN COMERCIOS</Desc></ActividadesTipo></ResultGet>"))
NO_ACTIVITIES = wsfe_answer("FEParamGetActividades", "<Errors><Err><Code>602</Code><Msg>Sin Resultados: - Metodo FEParamGetActividades</Msg></Err></Errors>")


def test_activities_are_listed_with_the_main_one_marked_and_an_empty_list_explains_itself(ready, monkeypatch):
    serve(ready, monkeypatch, {"FEParamGetActividades": ACTIVITIES})
    text = ready.actividades()
    assert "- 620100 · SERVICIOS DE CONSULTORES EN INFORMATICA (principal)" in text
    assert "- 479900 · VENTA AL POR MENOR NO REALIZADA EN COMERCIOS" in text and text.count("(principal)") == 1

    serve(ready, monkeypatch, {"FEParamGetActividades": NO_ACTIVITIES})  # lo que ARCA contesta en homologación sin actividades
    assert "no tiene actividades habilitadas" in ready.actividades() and "quitala de cuentas.json" in ready.actividades()


def test_the_activity_rejection_says_how_to_continue(arca, monkeypatch):
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3, "concepto": "productos", "actividad": "479900"})
    make_files(arca.home())
    obs = ("<Observaciones><Obs><Code>10223</Code><Msg>La actividad seleccionada 479900 no se encuentra vinculada al emisor "
           "o no esta activa. Ver método FEParamGetActividades</Msg></Obs></Observaciones>")

    text, _ = issue(arca, monkeypatch, answer=rejected(obs))

    assert text.startswith("✗ ARCA rechazó el comprobante: La actividad seleccionada 479900 no se encuentra vinculada al emisor")
    assert "herramienta «actividades»" in text and "quitá «actividad»" in text and "No se emitió nada" in text


def test_verify_checks_the_configured_activity_against_the_ones_of_the_issuer(arca, monkeypatch):
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3, "actividad": "479900"})
    make_files(arca.home())
    serve(arca, monkeypatch, {"FEDummy": DUMMY, "FEParamGetPtosVenta": POINTS, "FEParamGetActividades": ACTIVITIES})
    assert "✓ La actividad 479900 de la cuenta está habilitada para el emisor" in arca.verificar()

    serve(arca, monkeypatch, {"FEDummy": DUMMY, "FEParamGetPtosVenta": POINTS, "FEParamGetActividades": NO_ACTIVITIES})
    assert "✗ La actividad 479900 de la cuenta NO está entre las del emisor (ninguna)" in arca.verificar()

    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3})  # sin actividad configurada no se pregunta nada
    serve(arca, monkeypatch, {"FEDummy": DUMMY, "FEParamGetPtosVenta": POINTS})  # FEParamGetActividades ausente: llamarlo daría KeyError
    assert "actividad" not in arca.verificar()


def required_account(arca, *fields, **more):
    write_accounts(arca, negocio={"cuit": CUIT, "punto_venta": 3, "concepto": "productos", "obligatorios": list(fields), **more})


def test_without_the_list_only_what_arca_needs_is_required_but_missing_data_is_warned(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})
    text = ready.emitir_factura(importe=10, concepto="productos", documento=None, descripcion=None)
    assert "confirmar=" in text and "⚠ Sin descripción ni documento del cliente" in text


def test_an_account_can_require_the_description_the_document_and_the_place(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})
    required_account(ready, "descripcion", "documento", "localidad")
    refused = ready.emitir_factura(importe=10, descripcion=None)
    assert "Falta la descripción" in refused and "confirmar=" not in refused
    assert "Falta la descripción" in ready.emitir_factura(importe=10, descripcion="   ")
    refused = ready.emitir_factura(importe=10, documento=None)
    assert "Falta el documento" in refused and "sin_identificar=true" in refused and "confirmar=" not in refused
    assert "Falta la localidad" in ready.emitir_factura(importe=10)
    assert "confirmar=" in ready.emitir_factura(importe=10, localidad="CABA")
    assert "confirmar=" in ready.emitir_factura(importe=10, localidad="CABA", documento=None, sin_identificar=True)
    assert "dejá solo uno" in ready.emitir_factura(importe=10, localidad="CABA", sin_identificar=True)
    required_account(ready, "descripcion", localidad="CABA", descripcion="Repuestos y reparaciones")  # lo guardado en la cuenta cuenta
    assert "confirmar=" in ready.emitir_factura(importe=10, descripcion=None)


def test_a_wrong_list_of_required_fields_says_which_names_exist(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})
    required_account(ready, "domicilio")
    assert "documento, descripcion, localidad" in ready.emitir_factura(importe=10)


def test_the_summary_shows_payment_and_place_and_warns_that_arca_keeps_none_of_it(ready, monkeypatch):
    text, calls = issue(ready, monkeypatch, localidad="CABA")
    assert "Pago: Contado" in text and "PDF: " in text
    logged = json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])
    assert logged["forma_pago"] == "Contado" and logged["localidad"] == "CABA"
    simulation = ready.emitir_factura(importe=500, concepto="productos", localidad="CABA")
    assert "Localidad: CABA" in simulation and "ARCA no guarda la descripción" in simulation and "en el PDF" in simulation


def base64_request(calls):
    """El pedido de ingreso (TRA) que viajó firmado dentro del primer loginCms."""
    import base64
    cms = re.search(r"<in0>(.*?)</in0>", next(c[2] for c in calls if "LoginCms" in c[0])).group(1)
    return base64.b64decode(cms).decode("latin-1")


def padron_answer(method, body):
    return (f'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            f'<ns2:{method}Response xmlns:ns2="http://a13.soap.ws.server.puc.sr/">{body}</ns2:{method}Response></soap:Body></soap:Envelope>')


def person(cuil, last, first, state="ACTIVO"):
    return padron_answer("getPersonaV2", (
        f"<personaReturn><persona><apellido>{last}</apellido><nombre>{first}</nombre><idPersona>{cuil}</idPersona><tipoClave>CUIL</tipoClave>"
        f"<estadoClave>{state}</estadoClave>"
        "<domicilio><direccion>OTRA 1</direccion><localidad>LEGAL</localidad><tipoDomicilio>LEGAL/REAL</tipoDomicilio></domicilio>"
        "<domicilio><direccion>SAN RAMON 415</direccion><localidad>CABA</localidad><descripcionProvincia>CAPITAL FEDERAL</descripcionProvincia>"
        "<tipoDomicilio>FISCAL</tipoDomicilio></domicilio></persona></personaReturn>"))


def test_the_padron_looks_a_dni_up_with_its_own_ticket_and_shows_the_fiscal_address(ready, monkeypatch):
    ids = padron_answer("getIdPersonaListByDocumento", "<idPersonaListReturn><idPersona>20123456786</idPersona></idPersonaListReturn>")
    calls = serve(ready, monkeypatch, {"getIdPersonaListByDocumento": ids, "getPersonaV2": person("20123456786", "PEREZ", "JUAN")})

    text = ready.buscar_persona("12.345.678")

    assert "CUIL 20123456786 · PEREZ JUAN · SAN RAMON 415, CABA, CAPITAL FEDERAL" in text and "OTRA 1" not in text
    assert "HOMOLOGACIÓN" in text and "Hay más de una clave" not in text
    assert "<service>ws_sr_padron_a13</service>" in base64_request(calls)
    assert all(c[0] == "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA13" for c in calls if "LoginCms" not in c[0])
    assert (ready.home() / "cache" / "negocio-homologacion-ws_sr_padron_a13.json").is_file()  # un ticket por servicio
    body = next(c[2] for c in calls if c[1].endswith("getIdPersonaListByDocumento"))
    assert "<documento>12345678</documento>" in body and f"<cuitRepresentada>{CUIT}</cuitRepresentada>" in body


def test_two_keys_for_one_dni_are_listed_so_the_cuil_can_be_chosen(ready, monkeypatch):
    ids = padron_answer("getIdPersonaListByDocumento", "<idPersonaListReturn><idPersona>20123456786</idPersona><idPersona>27123456780</idPersona></idPersonaListReturn>")
    serve(ready, monkeypatch, {"getIdPersonaListByDocumento": ids, "getPersonaV2": person("20123456786", "PEREZ", "JUAN")})
    text = ready.buscar_persona("12345678")
    assert text.count("CUIL 2") == 2 and "usá el CUIL que corresponda" in text


def test_a_cuil_is_looked_up_directly_and_a_dni_nobody_has_says_so(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"getPersonaV2": person("20123456786", "PEREZ", "JUAN", state="INACTIVO")})
    assert "clave inactivo" in ready.buscar_persona("20-12345678-6")
    assert not [c for c in calls if c[1].endswith("getIdPersonaListByDocumento")]
    serve(ready, monkeypatch, {"getIdPersonaListByDocumento": padron_answer("getIdPersonaListByDocumento", "<idPersonaListReturn></idPersonaListReturn>")})
    assert "no tiene a nadie" in ready.buscar_persona("12345678")


def test_the_padron_validates_the_document_and_explains_a_missing_authorization(ready, monkeypatch):
    serve(ready, monkeypatch, {})
    assert "documento:" in ready.buscar_persona("abc") and "dígito verificador" in ready.buscar_persona("20123456780")
    fault = '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault><faultstring>Computador no autorizado a acceder al servicio</faultstring></soap:Fault></soap:Body></soap:Envelope>'
    serve(ready, monkeypatch, {"getIdPersonaListByDocumento": fault})
    assert "Falta autorizar el servicio «ws_sr_padron_a13»" in ready.buscar_persona("12345678")


def padron_for(*people):
    """Un padrón falso con esas personas (cuil, apellido, nombre): el DNI de 8 números devuelve todas sus claves."""
    ids = "".join(f"<idPersona>{c}</idPersona>" for c, _, _ in people)
    by_id = {c: person(c, last, first) for c, last, first in people}

    def one(body):
        return by_id[re.search(r"<idPersona>(\d+)</idPersona>", body).group(1)]
    return {"getIdPersonaListByDocumento": padron_answer("getIdPersonaListByDocumento", f"<idPersonaListReturn>{ids}</idPersonaListReturn>"),
            "getPersonaV2": one}


def test_emitting_shows_and_logs_the_name_and_address_from_the_padron(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FECAESolicitar": approved(), **padron_for(("20123456786", "PEREZ", "JUAN"))})
    simulation = ready.emitir_factura(importe=500, concepto="productos", documento="12345678")
    assert "Cliente (padrón): PEREZ JUAN · SAN RAMON 415, CABA, CAPITAL FEDERAL" in simulation
    done = ready.emitir_factura(importe=500, concepto="productos", documento="12345678", confirmar=the_code(simulation))
    assert "Receptor: DNI 12345678 · PEREZ JUAN" in done
    logged = json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])
    assert logged["cliente"] == "PEREZ JUAN" and logged["domicilio"].startswith("SAN RAMON 415")


def test_a_dni_with_several_keys_makes_you_choose_the_cuil_and_emits_nothing(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, **padron_for(("20123456786", "PEREZ", "JUAN"), ("27123456780", "PEREZ", "ANA"))})
    refused = ready.emitir_factura(importe=500, concepto="productos", documento="12345678")
    assert "tiene 2 claves" in refused and "PEREZ ANA" in refused and "confirmar=" not in refused


def test_when_the_padron_cannot_answer_the_invoice_still_goes_with_a_warning(ready, monkeypatch):
    fault = '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault><faultstring>Computador no autorizado a acceder al servicio</faultstring></soap:Fault></soap:Body></soap:Envelope>'
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "getIdPersonaListByDocumento": fault})
    simulation = ready.emitir_factura(importe=500, concepto="productos", documento="12345678")
    assert "⚠ No pude consultar el padrón" in simulation and "confirmar=" in simulation


def test_an_anonymous_consumer_never_asks_the_padron(ready, monkeypatch):
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST})
    ready.emitir_factura(importe=500, concepto="productos", documento=None, sin_identificar=True)
    assert not [c for c in calls if "sr-padron" in c[0]]


def test_a_connection_closed_without_an_answer_is_a_clear_error_not_a_crash(arca, monkeypatch):
    import http.client
    import urllib.request

    # la fixture reemplazó _post: se carga el módulo de nuevo para probar el de verdad
    spec = importlib.util.spec_from_file_location("arca_mcp_real", SCRIPT)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    def closed(*args, **kwargs):
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(urllib.request, "urlopen", closed)
    with pytest.raises(fresh.Problem) as raised:
        fresh._post("https://awshomo.afip.gov.ar/x", "a", "<x/>")
    assert "No pude hablar con ARCA" in str(raised.value) and raised.value.maybe_sent


def test_the_padron_retries_once_when_the_connection_drops_but_never_more(ready, monkeypatch):
    ok = padron_answer("getIdPersonaListByDocumento", "<idPersonaListReturn><idPersona>20123456786</idPersona></idPersonaListReturn>")
    attempts = []
    serve(ready, monkeypatch, {"getPersonaV2": person("20123456786", "PEREZ", "JUAN"), "FECompUltimoAutorizado": LAST})
    inner = ready._post

    def flaky(url, action, body):
        if action.endswith("getIdPersonaListByDocumento"):
            attempts.append(1)
            if len(attempts) == 1:
                raise ready.Problem("No pude hablar con ARCA", maybe_sent=True)
            return 200, ok
        return inner(url, action, body)

    monkeypatch.setattr(ready, "_post", flaky)
    assert "PEREZ JUAN" in ready.buscar_persona("12345678") and len(attempts) == 2
    attempts.clear()
    monkeypatch.setattr(ready, "_post", lambda *a: (_ for _ in ()).throw(ready.Problem("No pude hablar con ARCA", maybe_sent=True)) if a[1].endswith("getIdPersonaListByDocumento") else inner(*a))
    simulation = ready.emitir_factura(importe=1, concepto="productos", documento="12345678")
    assert "⚠ No pude consultar el padrón" in simulation


def decoded_qr_link(link):
    import base64
    assert link.startswith("https://www.afip.gob.ar/fe/qr/?p=")
    return json.loads(base64.b64decode(link.split("?p=", 1)[1]))


def test_emitting_makes_a_simple_pdf_with_the_data_and_a_qr_that_follows_arcas_format(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FECAESolicitar": approved(), **padron_for(("20123456786", "PEREZ", "JUAN"))})
    simulation = ready.emitir_factura(importe=1500.5, concepto="productos", documento="12345678", descripcion="Repuestos y reparaciones")
    text = ready.emitir_factura(importe=1500.5, concepto="productos", documento="12345678", descripcion="Repuestos y reparaciones",
                                confirmar=the_code(simulation))

    path = Path(re.search(r"PDF: (\S+\.pdf)", text).group(1))
    assert path.parent == ready.home() / "comprobantes" and path.name == "negocio-0003-00000046.pdf"
    assert path.read_bytes().startswith(b"%PDF") and stat.S_IMODE(path.stat().st_mode) == 0o600
    logged = json.loads((ready.home() / "emitidas.jsonl").read_text().splitlines()[-1])
    assert logged["pdf"] == str(path)
    qr = decoded_qr_link(ready.qr_link(ready.pick(None), logged))
    assert qr == {"ver": 1, "fecha": logged["fecha"], "cuit": int(CUIT), "ptoVta": 3, "tipoCmp": 11, "nroCmp": 46, "importe": 1500.5,
                  "moneda": "PES", "ctz": 1, "tipoDocRec": 96, "nroDocRec": 12345678, "tipoCodAut": "E", "codAut": 74123456789012}


def test_the_pdf_can_be_switched_off_and_a_pdf_failure_never_hides_an_issued_invoice(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FECAESolicitar": approved()})
    simulation = ready.emitir_factura(importe=500, concepto="productos", pdf=False)
    assert "NO habrá PDF" in simulation
    text = ready.emitir_factura(importe=500, concepto="productos", pdf=False, confirmar=the_code(simulation))
    assert "✓" in text and "PDF:" not in text and not (ready.home() / "comprobantes").exists()

    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FECAESolicitar": approved()})
    monkeypatch.setattr(ready, "render_pdf", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    simulation = ready.emitir_factura(importe=500, concepto="productos")
    text = ready.emitir_factura(importe=500, concepto="productos", confirmar=the_code(simulation))
    assert text.startswith("✓") and "CAE:" in text and "no pude armar el PDF" in text and "pdf_comprobante numero=46" in text


def test_pdf_comprobante_rebuilds_it_from_the_local_log_and_says_when_it_does_not_know_the_invoice(ready, monkeypatch):
    serve(ready, monkeypatch, {"FECompUltimoAutorizado": LAST, "FECAESolicitar": approved()})
    simulation = ready.emitir_factura(importe=500, concepto="productos", pdf=False)
    ready.emitir_factura(importe=500, concepto="productos", pdf=False, confirmar=the_code(simulation))

    rebuilt = ready.pdf_comprobante(46)
    assert rebuilt.startswith("PDF: ") and Path(rebuilt[5:]).read_bytes().startswith(b"%PDF")
    assert "No tengo en el registro local" in ready.pdf_comprobante(99)


def test_the_pdf_text_never_breaks_on_characters_the_standard_font_lacks(ready):
    assert ready.latin("Pérez 日本 ñ") == "Pérez ?? ñ"


# ---------- lotes ----------

def padron_by_document(people: dict):
    """Un padrón falso: documento -> [(cuil, apellido, nombre)]. Un DNI que no está no devuelve a nadie."""
    def ids(body):
        document = re.search(r"<documento>(\d+)</documento>", body).group(1)
        keys = "".join(f"<idPersona>{c}</idPersona>" for c, _, _ in people.get(document, []))
        return padron_answer("getIdPersonaListByDocumento", f"<idPersonaListReturn>{keys}</idPersonaListReturn>")

    def one(body):
        cuil = re.search(r"<idPersona>(\d+)</idPersona>", body).group(1)
        last, first = next((l, f) for found in people.values() for c, l, f in found if c == cuil)
        return person(cuil, last, first)
    return {"getIdPersonaListByDocumento": ids, "getPersonaV2": one}


PEOPLE = {"12345678": [("20123456786", "PEREZ", "JUAN CARLOS")], "23456789": [("20234567893", "GOMEZ", "ANA")]}


def serve_batch(ready, monkeypatch, answers=None, people=None, reject_number=None):
    """ARCA falso con numeración que avanza sola: cada emisión sube el último número."""
    state = {"last": 45}
    if "negocio" in ready.load_accounts() and "concepto" not in ready.load_accounts()["negocio"]:
        write_accounts(ready, negocio={"cuit": CUIT, "condicion": "monotributo", "punto_venta": 3, "concepto": "productos"})  # el concepto sale de la cuenta

    def last(body):
        return wsfe_answer("FECompUltimoAutorizado", f"<PtoVta>3</PtoVta><CbteTipo>11</CbteTipo><CbteNro>{state['last']}</CbteNro>")

    def emit(body):
        if state["last"] + 1 == reject_number:
            return rejected("<Obs><Code>10015</Code><Msg>Documento inválido</Msg></Obs>")
        state["last"] += 1
        return approved(state["last"])
    calls = serve(ready, monkeypatch, {"FECompUltimoAutorizado": last, "FECAESolicitar": emit, **padron_by_document(PEOPLE if people is None else people),
                                       **(answers or {})})
    return calls, state


def write_csv(tmp_path, text, name="facturas.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


CSV = "ID,nombre,dni,precio,localidad\nMP-1,Juan Perez,12345678,15000.50,\nMP-2,Ana Gomes,23456789,\"8.200,00\",Palermo\n"


def test_the_batch_simulation_compares_names_with_the_padron_and_emits_nothing(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch)

    text = ready.emitir_lote(write_csv(tmp_path, CSV))

    assert "| 2 | MP-1 | Juan Perez | 15.000,50 | 0003-00000046 | OK |" in text
    assert "| 3 | MP-2 | Ana Gomes | 8.200,00 | 0003-00000047 | REVISAR · el nombre no coincide: el padrón dice «GOMEZ ANA»" in text
    assert "A emitir: 2 por ARS 23.200,50 · para revisar: 1" in text
    assert "confirmar=" in text and "aceptar=MP-2" in text and "No se emitió nada" in text
    assert not sent(calls, "FECAESolicitar") and not (ready.home() / "emitidas.jsonl").exists()


def test_review_rows_need_to_be_accepted_one_by_one_and_then_everything_is_emitted_in_order(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch)
    path = write_csv(tmp_path, CSV)
    code = the_code(ready.emitir_lote(path))

    refused = ready.emitir_lote(path, confirmar=code)
    assert "faltan aceptar" in refused and "aceptar=MP-2" in refused and not sent(calls, "FECAESolicitar")

    text = ready.emitir_lote(path, confirmar=code, aceptar="MP-2")

    assert text.count("✓") == 2 and "Resultado: 2 emitidas de 2 por ARS 23.200,50" in text
    bodies = sent(calls, "FECAESolicitar")
    assert len(bodies) == 2 and "<CbteDesde>46</CbteDesde>" in bodies[0] and "<CbteDesde>47</CbteDesde>" in bodies[1]
    logged = [json.loads(line) for line in (ready.home() / "emitidas.jsonl").read_text().splitlines()]
    assert [(e["referencia"], e["numero"], e["resultado"], e["localidad"]) for e in logged] == [("MP-1", 46, "emitida", "CABA"), ("MP-2", 47, "emitida", "Palermo")]
    assert all(Path(e["pdf"]).read_bytes().startswith(b"%PDF") for e in logged)


def test_running_the_same_file_again_never_bills_an_id_twice(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch)
    path = write_csv(tmp_path, CSV)
    ready.emitir_lote(path, confirmar=the_code(ready.emitir_lote(path)), aceptar="MP-2")

    again = ready.emitir_lote(path)

    assert again.count("YA EMITIDA · ya salió como 0003-0000004") == 2 and "No hay nada para emitir" in again
    assert len(sent(calls, "FECAESolicitar")) == 2


def test_a_row_with_an_error_blocks_the_whole_batch(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch)
    text = ready.emitir_lote(write_csv(tmp_path, "ID,nombre,dni,precio\nA,Juan Perez,12345678,100\nB,Ana,23456789,12.3456\nC,X,,50\nA,Otro,12345678,10\n"))

    assert "ERROR · El importe lleva como máximo 2 decimales" in text and "ERROR · falta el DNI/CUIL/CUIT" in text
    assert "ERROR · el ID está repetido" in text and "confirmar=" not in text and "No se emitió nada" in text
    assert not sent(calls, "FECAESolicitar")


def test_the_batch_stops_at_the_first_failure_and_never_retries(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch, reject_number=47)
    path = write_csv(tmp_path, "ID,nombre,dni,precio\nA,Juan Perez,12345678,100\nB,Ana Gomez,23456789,200\nC,Juan Perez,12345678,300\n")

    text = ready.emitir_lote(path, confirmar=the_code(ready.emitir_lote(path)))

    assert "✓ Fila 2 · ID A" in text and "ARCA rechazó el comprobante" in text and "SE FRENÓ en la fila 3" in text
    assert "1 sin procesar: C" in text and "Resultado: 1 emitidas de 3" in text and len(sent(calls, "FECAESolicitar")) == 2


def test_the_code_depends_on_the_file_and_accepting_an_unknown_id_is_an_error(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch)
    code = the_code(ready.emitir_lote(write_csv(tmp_path, CSV)))
    changed = write_csv(tmp_path, CSV.replace("15000.50", "15000.60"), name="otro.csv")
    assert "El código no coincide con este lote" in ready.emitir_lote(changed, confirmar=code, aceptar="MP-2")
    assert "aceptar menciona IDs que no están para revisar: MP-9" in ready.emitir_lote(write_csv(tmp_path, CSV), aceptar="MP-9")
    assert not sent(calls, "FECAESolicitar")


def test_the_padron_being_down_leaves_every_row_to_review_but_the_batch_still_works(ready, monkeypatch, tmp_path):
    calls, _ = serve_batch(ready, monkeypatch, {"getIdPersonaListByDocumento": lambda body: (_ for _ in ()).throw(ready.Problem("No pude hablar con ARCA"))})
    text = ready.emitir_lote(write_csv(tmp_path, CSV))
    assert text.count("REVISAR · No pude consultar el padrón") == 2 and "aceptar=MP-1,MP-2" in text


def test_the_csv_can_come_from_a_spanish_excel_with_semicolons_and_other_column_names(ready, monkeypatch, tmp_path):
    serve_batch(ready, monkeypatch)
    text = ready.emitir_lote(write_csv(tmp_path, "Referencia;Cliente;CUIL;Importe\nÑ-1;Juan Pérez;12345678;$ 1.500\n", encoding="cp1252"))
    assert "| 2 | Ñ-1 | Juan Pérez | 1.500,00 | 0003-00000046 | OK |" in text


def test_a_csv_without_the_needed_columns_or_too_long_says_so(ready, monkeypatch, tmp_path):
    serve_batch(ready, monkeypatch)
    assert "faltan columnas: documento, importe" in ready.emitir_lote(write_csv(tmp_path, "ID,nombre\nA,B\n"))
    assert "No encuentro el archivo" in ready.emitir_lote(str(tmp_path / "no.csv"))
    write_accounts(ready, negocio={"cuit": CUIT, "punto_venta": 3, "concepto": "productos", "max_lote": 1})
    assert "permite 1 por lote" in ready.emitir_lote(write_csv(tmp_path, CSV))


def test_a_production_account_in_read_only_refuses_the_batch_even_with_a_valid_code(arca, monkeypatch, tmp_path):
    write_accounts(arca, prod={"cuit": CUIT, "ambiente": "produccion", "punto_venta": 3, "concepto": "productos"})
    make_files(arca.home(), alias="prod")
    calls, _ = serve_batch(arca, monkeypatch)
    path = write_csv(tmp_path, "ID,nombre,dni,precio\nA,Juan Perez,12345678,100\n")
    refused = arca.emitir_lote(path, confirmar=the_code(arca.emitir_lote(path)))
    assert "PRODUCCIÓN y está en solo lectura" in refused and not sent(calls, "FECAESolicitar")
