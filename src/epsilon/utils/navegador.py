from dataclasses import dataclass
import json
import socket
import subprocess
import time
from urllib.parse import quote

import httpx
from websockets.sync.client import connect

from epsilon.settings import config
from epsilon.utils import navegador_reglas as reglas

# El navegador de la IA: habla con el Chromium de la persona por su puerto de depuración (CDP), SOLO en
# 127.0.0.1:9222. Sin modelo. Los ÚNICOS puntos que tocan algo de afuera son `_http`, `_cdp`, `_resolve` y `_run`
# (los tests los bloquean). Las reglas de seguridad (sitios bloqueados, pestañas ocultas, direcciones) viven acá y
# en navegador_reglas.py, nunca dependen de lo que diga el modelo. Ver docs/superpowers/specs/2026-10-22-navegador-design.md.

PORT = 9222
BASE = f"http://127.0.0.1:{PORT}"
WS_PREFIX = f"ws://127.0.0.1:{PORT}/"
BROWSERS = ("Chrome/", "Chromium/", "HeadlessChrome/")
TIMEOUT = 5.0
LOAD_TRIES = 20  # al abrir: cuántas veces se mira si la pestaña cargó (de a medio segundo)
LAUNCH_WAIT = 15.0  # segundos que se espera a que un Chromium recién lanzado abra el puerto
MIN_REF = 4  # largo mínimo del id de una pestaña que se nombra

_opened: set[str] = set()  # ids de las pestañas que abrió Epsilon en esta ejecución


class NavegadorError(ValueError):
    """Algo no salió: el mensaje es para la persona (y para el modelo), en español."""


@dataclass(frozen=True)
class Tab:
    id: str
    title: str
    url: str
    host: str

    @property
    def short(self) -> str:
        return self.id[:6]


# --- Los únicos puntos que tocan algo de afuera --------------------------------------------------


def _http(method: str, path: str) -> object:
    """Un pedido al puerto de depuración; devuelve el JSON, o el texto si la respuesta no es JSON."""
    try:
        response = httpx.request(method, BASE + path, timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError:
        raise NavegadorError(
            f"No pude hablar con Chromium en el puerto {PORT}. ¿Está abierto con --remote-debugging-port={PORT}?"
        ) from None
    try:
        return response.json()
    except ValueError:
        return response.text


def _cdp(ws_url: str, method: str, params: dict | None = None) -> dict:
    """Una orden del protocolo de Chromium por WebSocket; devuelve su `result`."""
    if not ws_url.startswith(WS_PREFIX):
        raise NavegadorError("Chromium dio una dirección de control que no es de esta compu.")
    try:
        with connect(ws_url, open_timeout=TIMEOUT, close_timeout=TIMEOUT) as ws:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                message = json.loads(ws.recv(timeout=TIMEOUT * 2))
                if message.get("id") == 1:
                    break
    except (OSError, ValueError, TimeoutError):
        raise NavegadorError("Chromium no contestó la orden. Probá de nuevo.") from None
    if "error" in message:
        raise NavegadorError("Chromium rechazó la orden.")
    return message.get("result", {})


def _resolve(host: str) -> list[str]:
    """Las IP del sitio; vacío si no se encuentra."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Lanza un proceso con argumentos fijos: sin shell y con tiempo máximo."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        raise NavegadorError("No pude lanzar Chromium.") from None


# --- Pestañas ------------------------------------------------------------------------------------


def check_browser() -> None:
    """Falla con un mensaje claro si en el puerto no responde un Chromium (y de esta compu)."""
    version = _http("GET", "/json/version")
    ok = (
        isinstance(version, dict)
        and str(version.get("Browser", "")).startswith(BROWSERS)
        and str(version.get("webSocketDebuggerUrl", "")).startswith(WS_PREFIX)
    )
    if not ok:
        raise NavegadorError(
            f"En el puerto {PORT} no responde un Chromium. ¿Está abierto con --remote-debugging-port={PORT}?"
        )


def ensure_running() -> None:
    """Deja Chromium listo con el puerto abierto. Si está cerrado, lo abre (en su propia unidad de systemd, para que
    reiniciar el servicio de Epsilon no lo cierre). Si ya está abierto SIN el puerto, no lo toca: lo explica."""
    try:
        check_browser()
        return
    except NavegadorError:
        pass
    if _run(["pgrep", "-x", "chromium"]).returncode == 0:
        raise NavegadorError(
            "Chromium está abierto pero sin el puerto de depuración. Hay que reiniciarlo: cerralo del todo y abrilo de "
            f"nuevo, con la línea --remote-debugging-port={PORT} en ~/.config/chromium-flags.conf."
        )
    if _run(["systemd-run", "--user", "--collect", "chromium"]).returncode != 0:
        raise NavegadorError("No pude lanzar Chromium.")
    deadline = time.monotonic() + LAUNCH_WAIT
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            check_browser()
            return
        except NavegadorError:
            continue
    raise NavegadorError("Abrí Chromium pero el puerto de depuración no respondió a tiempo.")


def _raw_tabs() -> list[dict]:
    """Las pestañas (tipo página) tal como las da Chromium; una respuesta rara es un error, no una lista vacía."""
    raw = _http("GET", "/json/list")
    if not isinstance(raw, list):
        raise NavegadorError("Chromium contestó algo que no entendí.")
    return [item for item in raw if isinstance(item, dict) and item.get("type") == "page"]


def _tab(item: dict) -> Tab:
    url = str(item.get("url", ""))
    return Tab(str(item.get("id", "")), str(item.get("title", "")), url, reglas.host_of(url))


def tabs() -> tuple[list[Tab], int]:
    """Las pestañas que la IA puede ver y cuántas se ocultan. Se ve la que abrió Epsilon o la de un sitio de
    confianza; la de un sitio bloqueado nunca (aunque la haya abierto Epsilon). Las demás no se nombran."""
    check_browser()
    trusted, blocked = config.site_list("BROWSER_TRUSTED_SITES"), config.site_list("BROWSER_BLOCKED_SITES")
    visible, hidden = [], 0
    for item in _raw_tabs():
        tab = _tab(item)
        if tab.id and not reglas.matches(tab.host, blocked) and (tab.id in _opened or reglas.matches(tab.host, trusted)):
            visible.append(tab)
        else:
            hidden += 1
    return visible, hidden


def resolve_tab(ref: str) -> Tab:
    """La pestaña visible cuyo id empieza por `ref`. Una oculta, una que no existe o un prefijo ambiguo, no."""
    ref = ref.strip()
    if len(ref) < MIN_REF:
        raise NavegadorError("Falta el id de la pestaña (pedí la lista de pestañas).")
    found = [tab for tab in tabs()[0] if tab.id.startswith(ref)]
    if len(found) != 1:
        raise NavegadorError("No encuentro esa pestaña: pedí la lista de pestañas de nuevo.")
    return found[0]


def open_url(url: str) -> Tab:
    """Abre la dirección en una pestaña nueva. Revisa la dirección antes y la dirección final después."""
    ensure_running()
    url = url.strip()
    _check_destination(url)
    item = _http("PUT", "/json/new?" + quote(url, safe=":/?&=#%+,;~!$'()*@"))
    if not isinstance(item, dict) or not item.get("id"):
        raise NavegadorError("Chromium no abrió la pestaña.")
    tab_id = str(item["id"])
    _opened.add(tab_id)
    tab = _tab(item)
    for _ in range(LOAD_TRIES):  # esperar a que salga de about:blank, para ver adónde llegó de verdad
        current = next((_tab(i) for i in _raw_tabs() if i.get("id") == tab_id), None)
        if current is None:
            raise NavegadorError("La pestaña se cerró sola.")
        tab = current
        if not tab.url.startswith("about:"):
            break
        time.sleep(0.5)
    try:
        if not tab.url.startswith("about:"):
            _check_destination(tab.url)
    except NavegadorError:
        _http("GET", f"/json/close/{tab_id}")
        _opened.discard(tab_id)
        raise
    return tab


def _check_destination(url: str) -> None:
    """Rechaza una dirección que no se puede abrir: mal formada, local, sin resolver o de un sitio bloqueado."""
    host = reglas.host_of(url)
    if reglas.matches(host, config.site_list("BROWSER_BLOCKED_SITES")):
        raise NavegadorError("Ese sitio está bloqueado: no lo abro.")
    problem = reglas.url_problem(url, _resolve(host) if host else [])
    if problem:
        raise NavegadorError(problem)
