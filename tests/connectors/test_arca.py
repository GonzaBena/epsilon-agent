import asyncio
import base64
import importlib.util
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

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


def serve(arca, monkeypatch, wsfe: dict, wsaa=None):
    """Instala un ARCA falso: devuelve la lista de pedidos (url, acción, cuerpo)."""
    calls = []

    def fake(url, action, body):
        calls.append((url, action, body))
        if "LoginCms" in url:
            return (wsaa or (200, wsaa_answer()))
        method = action.rsplit("/", 1)[-1]
        return 200, wsfe[method]

    monkeypatch.setattr(arca, "_post", fake)
    return calls


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


def test_server_defines_the_read_only_tools_and_the_buttons_fit_them(arca):
    found = {t.name: t.input_schema for t in asyncio.run(arca.server.list_tools())}

    assert set(found) == {"cuentas", "verificar", "puntos_de_venta", "tipos_de_comprobante", "ultimo_comprobante", "ver_comprobante"}
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
    assert json.loads((folder / "cuentas.json").read_text()) == {"negocio": {"cuit": CUIT, "ambiente": "homologacion", "condicion": "monotributo"}}
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
