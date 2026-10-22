from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import quote

import httpx
from websockets.sync.client import connect

from epsilon.data import notes
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
LOG_FILE = "browser.log"
LOG_RESULT_CHARS = 120
HISTORY_FILE = Path.home() / ".config/chromium/Default/History"  # el historial de Chromium: se lee, jamás se escribe
HISTORY_ROWS = 5000  # cuántas direcciones se miran (las más visitadas)
MIN_REF = 4  # largo mínimo del id de una pestaña que se nombra

MAX_TEXT = 3000  # caracteres de texto que devuelve `read`
MAX_ELEMENTS = 40  # elementos que lista `read`
MAX_TYPED = 500  # caracteres que acepta `type_text`
KINDS = {"button": "botón", "link": "enlace", "input": "campo"}

_opened: set[str] = set()  # ids de las pestañas que abrió Epsilon en esta ejecución
_elements: dict[str, list["Element"]] = {}  # pestaña -> elementos de su última lectura
_sites: dict[str, str] = {}  # pestaña -> sitio que tenía cuando se leyó (para no actuar si cambió)
_last = threading.local()  # lo último que leyó `read` en este hilo (ver last_read_hosts)
MAX_TITLE = 200

# El ÚNICO código que corre dentro de una pestaña: fijo, sin nada que venga del modelo. Devuelve el título, el texto
# visible y los elementos tocables a la vista (jamás el valor de un campo). Marca como sensibles los de contraseña, tarjeta o CVV.
READ_JS = """(() => {
  const clean = (s, n) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, n);
  const out = [];
  const sel = 'a[href], button, [role=button], input:not([type=hidden]), textarea, select';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    const hint = [el.name, el.id, ac].join(' ').toLowerCase();
    const sensitive = type === 'password' || /^cc-/.test(ac) || /(card|cvv|cvc|security.?code)/.test(hint);
    const field = tag === 'input' || tag === 'textarea' || tag === 'select';
    const kind = type === 'password' ? 'password' : tag === 'a' ? 'link' : field ? 'input' : 'button';
    const label = clean(el.getAttribute('aria-label') || (field ? (el.placeholder || el.title || el.name) : (el.innerText || el.textContent)) || el.title || el.alt, 80);
    out.push({label, kind, x: r.left + r.width / 2, y: r.top + r.height / 2, sensitive, href: tag === 'a' ? el.href : '', el});
    if (out.length >= 200) break;
  }
  // Varios botones con el mismo rótulo («Reproducir» en cada tarjeta): se les suma el texto de su tarjeta para distinguirlos.
  const seen = {};
  for (const o of out) seen[o.label] = (seen[o.label] || 0) + 1;
  for (const o of out) {
    if (seen[o.label] > 1) {
      let p = o.el.parentElement;
      for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
        const t = clean(p.innerText, 200);
        if (t.length > o.label.length + 3 && t.length <= 120) { o.label = clean(o.label + ' — ' + t, 80); break; }
      }
    }
    delete o.el;
  }
  return {url: location.href, title: document.title, text: clean(document.body ? document.body.innerText : '', 3000), elements: out};
})()"""


class NavegadorError(ValueError):
    """Algo no salió: el mensaje es para la persona (y para el modelo), en español."""


@dataclass(frozen=True)
class Element:
    n: int  # número con que lo nombra `read` (desde 1)
    label: str
    kind: str  # button, link, input
    x: float
    y: float
    sensitive: bool  # contraseña, tarjeta o CVV: no se escribe
    href: str = ""  # a dónde lleva, si es un enlace


@dataclass(frozen=True)
class Tab:
    id: str
    title: str
    url: str
    host: str
    ws: str = ""  # dirección de control de la pestaña (solo se usa si empieza por WS_PREFIX; ver _cdp)

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
    return Tab(str(item.get("id", "")), str(item.get("title", "")), url, reglas.host_of(url), str(item.get("webSocketDebuggerUrl", "")))


def tabs() -> tuple[list[Tab], int]:
    """Las pestañas que la IA puede ver y cuántas se ocultan. Se ve la que abrió Epsilon o la de un sitio de
    confianza; la de un sitio bloqueado nunca (aunque la haya abierto Epsilon). Las demás no se nombran."""
    check_browser()
    trusted, blocked = config.site_list("BROWSER_TRUSTED_SITES"), config.site_list("BROWSER_BLOCKED_SITES")
    visible, hidden = [], 0
    for item in _raw_tabs():
        tab = _tab(item)
        shown = tab.id and not reglas.matches(tab.host, blocked) and reglas.local_problem(tab.url) is None
        if shown and (tab.id in _opened or reglas.matches(tab.host, trusted)):
            visible.append(tab)
        else:
            hidden += 1
    return visible, hidden


def trusted(host: str) -> bool:
    """¿El sitio es de confianza (BROWSER_TRUSTED_SITES)? Un sitio vacío no."""
    return reglas.matches(host, config.site_list("BROWSER_TRUSTED_SITES"))


def resolve_tab(ref: str) -> Tab:
    """La pestaña visible cuyo id empieza por `ref`. Una oculta, una que no existe o un prefijo ambiguo, no."""
    ref = ref.strip()
    if len(ref) < MIN_REF:
        raise NavegadorError("Falta el id de la pestaña (pedí la lista de pestañas).")
    found = [tab for tab in tabs()[0] if tab.id.startswith(ref)]
    if len(found) != 1:
        raise NavegadorError("No encuentro esa pestaña: pedí la lista de pestañas de nuevo.")
    return found[0]


def _history() -> list[tuple[str, str, int, int]]:
    """Punto único (bloqueado en los tests): el historial real de Chromium."""
    return read_history(HISTORY_FILE)


def read_history(source: Path) -> list[tuple[str, str, int, int]]:
    """Las direcciones más visitadas del historial de Chromium (url, título, visitas, veces escrita). Chromium tiene el
    archivo bloqueado mientras corre: se lee una copia temporal, abierta en solo lectura, que se borra al terminar.
    Sirve SOLO para resolver el nombre de un sitio (ver resolve_target); el historial nunca llega al modelo."""
    if not source.is_file():
        raise NavegadorError("No encuentro el historial de Chromium: pasame la dirección completa.")
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "history.db"
        shutil.copyfile(source, copy)
        try:
            db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                return [
                    (str(url), str(title or ""), int(visits), int(typed))
                    for url, title, visits, typed in db.execute(
                        "SELECT url, title, visit_count, typed_count FROM urls "
                        "WHERE hidden = 0 AND (url LIKE 'http://%' OR url LIKE 'https://%') "
                        "ORDER BY typed_count DESC, visit_count DESC LIMIT ?",
                        (HISTORY_ROWS,),
                    )
                ]
            finally:
                db.close()
        except sqlite3.Error:
            raise NavegadorError("No pude leer el historial de Chromium: pasame la dirección completa.") from None


def resolve_target(text: str) -> str:
    """De lo que pidió la IA a una dirección: una con «://» se deja tal cual (después la revisan las reglas); un nombre
    o una frase («yt», «aula virtual utn») se busca en el historial; un dominio suelto («youtube.com») recibe https://
    SOLO si la persona ya lo visitó o es de confianza (así no se abre un dominio que el modelo inventó). Si no se
    reconoce, NavegadorError con un mensaje claro (nunca una búsqueda en Google)."""
    text = text.strip()
    if "://" in text or not text:
        return text
    if reglas.DOMAIN_LIKE.fullmatch(text):
        host = reglas.host_of("https://" + text)
        if trusted(host) or reglas.known_site(host, _history()):
            return "https://" + text
        raise NavegadorError(
            f"«{host}» no está en tu historial ni en los sitios de confianza: no la invento. "
            "Pasame el nombre del sitio como lo dijo la persona o la dirección completa con https://."
        )
    if ":" in text or "/" in text:
        return text  # algo raro («javascript:…», una ruta): lo rechaza la revisión de la dirección
    host = reglas.pick_site(text, _history(), config.site_list("BROWSER_BLOCKED_SITES"))
    if not host:
        raise NavegadorError(f"No encontré «{text}» en el historial de Chromium: pasame la dirección completa.")
    return f"https://{host}/"


def open_url(url: str) -> Tab:
    """Abre la dirección en una pestaña nueva. Revisa la dirección antes y la dirección final después."""
    ensure_running()
    url = url.strip()
    _check_destination(url)
    item = _http("PUT", "/json/new?" + quote(url, safe=""))  # Chromium parte el pedido en «&» y decodifica: todo escapado
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
        landing = tab.url
        if not landing.startswith("about:"):
            for _ in range(LOAD_TRIES):  # la dirección de /json/list puede ser la pedida: se mira la que cargó de verdad
                state = _cdp(tab.ws, "Runtime.evaluate", {"expression": "document.readyState + '|' + location.href", "returnByValue": True})
                ready, _, href = str(state.get("result", {}).get("value", "")).partition("|")
                landing = href or landing
                if ready == "complete" and not href.startswith("about:"):  # el about:blank inicial también está «complete»
                    break
                time.sleep(0.5)
            if not landing.startswith("about:"):  # sigue en blanco tras la espera: tabs() la revisa cada vez que se usa
                _check_destination(landing)
    except NavegadorError:
        try:
            _http("GET", f"/json/close/{tab_id}")
        except NavegadorError:
            pass
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


# --- Leer, tocar y escribir ----------------------------------------------------------------------


def parse_ref(args: str) -> tuple[str, int]:
    """«a1b2c3 4» -> («a1b2c3», 4): el id de la pestaña y el número de elemento (entero, desde 1)."""
    parts = args.split()
    if len(parts) != 2 or not (parts[1].isascii() and parts[1].isdigit()) or int(parts[1]) < 1:
        raise NavegadorError("Falta el id de la pestaña y el número del elemento, por ejemplo: a1b2c3 4")
    return parts[0], int(parts[1])


def _collect(tab: Tab) -> tuple[dict, list[Element]]:
    """Corre el script fijo de lectura en la pestaña: la página y sus elementos numerados (sin los de contraseña)."""
    result = _cdp(tab.ws, "Runtime.evaluate", {"expression": READ_JS, "returnByValue": True})
    data = result.get("result", {}).get("value")
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise NavegadorError("No pude leer la página.")
    found = [e for e in data["elements"] if isinstance(e, dict) and e.get("kind") != "password"][:MAX_ELEMENTS]
    elements = [
        Element(
            n,
            " ".join(str(e.get("label", "")).split())[:80],  # una sola línea: la página no puede fingir otras
            str(e.get("kind", "button")),
            float(e.get("x", 0)),
            float(e.get("y", 0)),
            bool(e.get("sensitive", True)),
            str(e.get("href", ""))[:500],
        )
        for n, e in enumerate(found, 1)
    ]
    return data, elements


def read(tab_ref: str) -> str:
    """El título, el texto principal (acotado) y los elementos tocables numerados de la pestaña."""
    tab = resolve_tab(tab_ref)
    data, elements = _collect(tab)
    _elements[tab.id] = elements
    _sites[tab.id] = tab.host
    _last.hosts = [tab.host, reglas.host_of(str(data.get("url", "")))]  # el de antes y el de después: la página pudo navegar
    title = " ".join(str(data.get("title", "")).split())[:MAX_TITLE]
    lines = [f"Título: {title}", f"Sitio: {tab.host}", "Elementos:"]
    lines += [f"{e.n}: {KINDS.get(e.kind, 'elemento')} «{e.label}»" for e in elements]
    lines += ["Texto:", str(data.get("text", ""))[:MAX_TEXT]]  # al final: si el resultado se recorta, se pierde texto y no números
    return "\n".join(lines)


def last_read_hosts() -> list[str]:
    """Los sitios que tenía la pestaña antes y después de la última lectura de este hilo (vacío si no hubo)."""
    return list(getattr(_last, "hosts", []))


def element(tab_ref: str, n: int) -> Element:
    """El elemento `n` de la última lectura de esa pestaña."""
    tab = resolve_tab(tab_ref)
    elements = _elements.get(tab.id)
    if not elements:
        raise NavegadorError("Leé la pestaña primero (acción leer) para ver los números de los elementos.")
    if not 1 <= n <= len(elements):
        raise NavegadorError(f"No hay un elemento {n}: leé la pestaña de nuevo.")
    return elements[n - 1]


def _press(tab: Tab, n: int, saved: Element, field: bool = False) -> Element:
    """Hace clic de verdad en el elemento `n`, después de comprobar que la página sigue siendo la que se leyó
    (mismo sitio y mismo rótulo). Con `field`, además tiene que seguir siendo un campo que no sea sensible."""
    if _sites.get(tab.id) != tab.host:
        raise NavegadorError("La página cambió de sitio: leé la pestaña de nuevo.")
    _, fresh = _collect(tab)
    now = next((e for e in fresh if e.n == n), None)
    if now is None or now.label != saved.label:
        raise NavegadorError("La página cambió: leé la pestaña de nuevo.")
    if field and (now.kind != "input" or now.sensitive):
        raise NavegadorError("Ese campo es de contraseña o de tarjeta, o ya no es un campo: no escribo ahí.")
    _elements[tab.id] = fresh
    for kind in ("mousePressed", "mouseReleased"):
        _cdp(tab.ws, "Input.dispatchMouseEvent", {"type": kind, "x": now.x, "y": now.y, "button": "left", "clickCount": 1})
    return now


def click(tab_ref: str, n: int) -> str:
    """Hace clic en el elemento `n` de la última lectura. Es un clic real: la reproducción lo necesita."""
    tab, saved = resolve_tab(tab_ref), element(tab_ref, n)
    now = _press(tab, n, saved)
    return f"Hice clic en «{now.label}»."


def type_text(tab_ref: str, n: int, text: str) -> str:
    """Escribe en el campo `n` de la última lectura. Nunca en contraseñas ni datos de tarjeta."""
    tab, saved = resolve_tab(tab_ref), element(tab_ref, n)
    if saved.kind != "input":
        raise NavegadorError("Ese elemento no es un campo de texto: para tocarlo usá clic.")
    if saved.sensitive:
        raise NavegadorError("Ese campo es de contraseña o de tarjeta: no escribo ahí.")
    if len(text) > MAX_TYPED:
        raise NavegadorError(f"Ese texto es muy largo (máximo {MAX_TYPED} caracteres).")
    now = _press(tab, n, saved, field=True)
    _cdp(tab.ws, "Input.insertText", {"text": text})
    return f"Escribí en «{now.label}»."


def tab_host(tab_ref: str) -> str:
    """El sitio actual de la pestaña (para decidir permisos por el sitio real)."""
    return resolve_tab(tab_ref).host


# --- Registro --------------------------------------------------------------------------------------


def audit(action: str, host: str, result: str, asked: bool) -> None:
    """Agrega una línea al registro de lo que hizo la IA en el navegador (DATA_DIR/browser.log): hora, acción, sitio,
    resultado (una línea, redactado y corto) y si se pidió permiso. Solo se agrega. Un fallo al escribir nunca rompe la acción."""
    short = " ".join(config.redact(result).split())[:LOG_RESULT_CHARS]
    fields = [datetime.now().isoformat(timespec="seconds"), action, host or "-", short, "preguntó" if asked else "directo"]
    try:
        with notes.safe_path(LOG_FILE).open("a", encoding="utf-8") as log:
            log.write("\t".join(fields) + "\n")
    except OSError:
        pass
