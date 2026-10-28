from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import httpx
from websockets.sync.client import connect

from epsilon.data import notes, sites
from epsilon.settings import config
from epsilon.utils import navegador_reglas as reglas

# El navegador de la IA: habla con el Chromium de la persona por su puerto de depuración (CDP), SOLO en
# 127.0.0.1:9222. Sin modelo. Los ÚNICOS puntos que tocan algo de afuera son `_http`, `_cdp`, `_resolve`, `_run`,
# `_history`, `_bookmarks`, `_webapps` y `_youtube_history` (los tests los bloquean). Las reglas de seguridad (sitios bloqueados, pestañas ocultas, direcciones) viven acá y
# en navegador_reglas.py, nunca dependen de lo que diga el modelo. Ver docs/superpowers/specs/2026-10-22-navegador-design.md.

PORT = 9222
BASE = f"http://127.0.0.1:{PORT}"
WS_PREFIX = f"ws://127.0.0.1:{PORT}/"
BROWSERS = ("Chrome/", "Chromium/", "HeadlessChrome/")
TIMEOUT = 5.0
LOAD_TRIES = 20  # al abrir: cuántas veces se mira si la pestaña cargó (de a medio segundo)
SETTLE_PAUSE = 0.7  # tras un clic: pausa para que la página empiece a navegar antes de preguntarle si cargó
SETTLE_TRIES = 10  # y cuántas veces se mira si terminó de cargar (de a medio segundo)
LAUNCH_WAIT = 15.0  # segundos que se espera a que un Chromium recién lanzado abra el puerto
LOG_FILE = "browser.log"
LOG_RESULT_CHARS = 120
HISTORY_FILE = Path.home() / ".config/chromium/Default/History"  # el historial de Chromium: se lee, jamás se escribe
HISTORY_ROWS = 5000  # cuántas direcciones se miran (las más visitadas)
BOOKMARKS_FILE = Path.home() / ".config/chromium/Default/Bookmarks"  # los marcadores de Chromium: se leen, jamás se escriben
MAX_BOOKMARKS = 2000
HISTORY_DAYS = 28  # «las últimas semanas» de yt_historial
HISTORY_RESULTS = 20
APPS_FOLDER = Path.home() / ".local/share/applications"  # los .desktop: de ahí salen las web apps de Omarchy (solo lectura)
MAX_APPS = 500
MIN_REF = 4  # largo mínimo del id de una pestaña que se nombra

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
    let label = clean(el.getAttribute('aria-label') || (field ? (el.placeholder || el.title || el.name) : (el.innerText || el.textContent)) || el.title || el.alt, 80);
    // Un botón de solo ícono (la hamburguesa de un menú) no tiene texto: se lo nombra por su id o su clase para que el modelo lo vea.
    if (!label && !field) label = clean('sin nombre (' + (el.id || String(el.className || '').split(' ')[0] || el.tagName.toLowerCase()) + ')', 80);
    const expanded = el.getAttribute('aria-expanded');
    const state = expanded === 'true' ? 'abierto' : expanded === 'false' ? 'cerrado' : '';
    out.push({label, kind, state, x: r.left + r.width / 2, y: r.top + r.height / 2, sensitive, href: tag === 'a' ? el.href : '', el});
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
  const more = document.documentElement.scrollHeight - (scrollY + innerHeight) > 40;
  return {url: location.href, title: document.title, more, text: clean(document.body ? document.body.innerText : '', 3000), elements: out};
})()"""


# El SEGUNDO (y último) código fijo que corre en una pestaña: lee el estado de repetir y aleatorio de la barra de
# YouTube Music (atributos `repeat-mode` y `shuffle-on`; el rótulo de los botones se queda viejo, no sirve), qué canción dice
# y dónde están los botones. No toca nada de la página y no recibe nada del modelo.
MUSIC_HOST = "music.youtube.com"
MUSIC_REPEAT = ("NONE", "ALL", "ONE")
MUSIC_JS = """(() => {
  const bar = document.querySelector('ytmusic-player-bar');
  if (!bar) return null;
  const center = (el) => {
    const r = el && el.getBoundingClientRect();
    return r && r.width > 0 && r.height > 0 && r.top >= 0 && r.top < innerHeight ? {x: r.left + r.width / 2, y: r.top + r.height / 2} : null;
  };
  const title = bar.querySelector('.title');
  return {repeat: bar.getAttribute('repeat-mode'), shuffle: bar.hasAttribute('shuffle-on'), title: title ? title.textContent : '',
          repeatAt: center(bar.querySelector('.repeat')), shuffleAt: center(bar.querySelector('.shuffle'))};
})()"""
# Otro código fijo (solo lee): las playlists de la página de resultados de YouTube Music (los ítems con un enlace
# `playlist?list=<id>`): id, título y el texto del ítem («Playlist • autor • 69 canciones»). No recibe nada del modelo:
# la búsqueda va en la dirección. Todo es dato de otros y se limpia y recorta en Python (ver _music_results).
MUSIC_SEARCH_JS = """(() => {
  const clean = (s, k) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, k);
  const out = [];
  for (const el of document.querySelectorAll('ytmusic-responsive-list-item-renderer, ytmusic-two-row-item-renderer')) {
    const a = [...el.querySelectorAll('a[href]')].find((x) => /playlist\\?list=/.test(x.getAttribute('href') || ''));
    if (!a) continue;
    const title = el.querySelector('.title, yt-formatted-string.title');
    out.push({id: (a.getAttribute('href').match(/list=([^&]+)/) || [])[1] || '', title: clean(title ? title.textContent : '', 150), text: clean(el.innerText, 300)});
    if (out.length >= 20) break;
  }
  return out;
})()"""
MUSIC_RESULTS = 8
# La biblioteca de la persona es la misma página de resultados (los mismos ítems con enlace `playlist?list=`), pero con más tope.
MUSIC_LIBRARY_JS = MUSIC_SEARCH_JS.replace("out.length >= 20", "out.length >= 60")
MAX_PLAYLISTS = 50  # cuántas playlists de la biblioteca entran al índice
INDEX_TRACKS = 15  # y cuántas canciones de cada una se leen
# Otro código fijo (solo lee): el título y las primeras canciones (título y artista) de la página de UNA playlist.
PLAYLIST_JS = """(() => {
  const clean = (s, k) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, k);
  const rows = [...document.querySelectorAll('ytmusic-responsive-list-item-renderer')].filter((e) => !e.closest('ytmusic-guide-renderer'));
  return rows.slice(0, 15).map((el) => {
    const title = el.querySelector('.title, yt-formatted-string.title');
    const cols = [...el.querySelectorAll('.secondary-flex-columns yt-formatted-string, yt-formatted-string.flex-column')];
    return {title: clean(title ? title.textContent : '', 100), artist: clean(cols[0] ? cols[0].textContent : '', 60)};
  });
})()"""
# Otro código fijo (solo lee, no recibe nada del modelo): dónde están, en la página de YouTube Music que abrió
# `play_music`, el botón «Reproducir» y el «Aleatorio» de la cabecera de una playlist y el botón de reproducir del
# primer resultado de una búsqueda. Devuelve posiciones; el clic lo da Python con el mouse. Reconoce los rótulos en
# español e inglés porque eso es lo que Chromium de la persona muestra.
PLAY_JS = """(() => {
  const center = (el) => {
    const r = el && el.getBoundingClientRect();
    return r && r.width > 0 && r.height > 0 && r.top >= 0 && r.top < innerHeight ? {x: r.left + r.width / 2, y: r.top + r.height / 2} : null;
  };
  const label = (el) => ((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')).replace(/\\s+/g, ' ').trim().toLowerCase();
  const head = document.querySelector('ytmusic-responsive-header-renderer, ytmusic-detail-header-renderer, ytmusic-immersive-header-renderer');
  const find = (root, re) => root ? [...root.querySelectorAll('button, a, yt-button-renderer, ytmusic-play-button-renderer')].find((el) => re.test(label(el)) && center(el)) : null;
  const first = [...document.querySelectorAll('ytmusic-play-button-renderer')].find((el) => /^(reproducir|play)\\b/i.test(el.getAttribute('aria-label') || '') && !el.closest('tp-yt-paper-item, ytmusic-guide-renderer, ytmusic-player-bar') && center(el));
  return {play: center(find(head, /^(reproducir|play)\\b/)), shuffle: center(find(head, /(aleatori|shuffle)/)),
          result: center(first)};
})()"""
# Otro código fijo (solo lee): el video de una página de YouTube (si está pausado, su título y dónde tocarlo).
VIDEO_JS = """(() => {
  const v = document.querySelector('video');
  if (!v) return null;
  const r = v.getBoundingClientRect();
  return {paused: v.paused, title: document.title, at: r.width > 0 && r.height > 0 ? {x: r.left + r.width / 2, y: r.top + r.height / 2} : null};
})()"""
PLAY_WAIT = 20  # cuántas veces se espera (de a medio segundo) a que YouTube Music dibuje los botones
# El TERCER código fijo: lee la página de resultados de Google (los enlaces con título `h3`, que es lo que
# viene siendo estable) y si Google pidió el captcha. No toca nada y no recibe nada del modelo: la búsqueda va en la
# dirección. El texto de cada resultado es dato y se limpia y recorta en Python (ver search).
SEARCH_URL = "https://www.google.com/search?hl=es&q="
GOOGLE_HOST = re.compile(r"(?:www\.)?google\.[a-z.]+")  # la portada y los resultados de Google (no drive.google.com)
MAX_QUERY = 200
MAX_RESULTS = 8
MAX_SNIPPET = 200
CRUMB = re.compile(r"^.{0,80}?https?://\S+(?: › \S+)*\s*")  # el sitio y la «miga de pan» con que Google empieza cada resultado
SEARCH_JS = """(() => {
  if (location.pathname.startsWith('/sorry') || document.querySelector('#captcha-form, iframe[src*="recaptcha"]')) return {blocked: true, results: []};
  const clean = (s, n) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, n);
  const out = [];
  for (const h3 of document.querySelectorAll('a h3')) {
    const a = h3.closest('a');
    const title = clean(h3.innerText, 150);
    if (!a || !title) continue;
    let snippet = '';
    for (let p = a.parentElement, i = 0; p && i < 8; p = p.parentElement, i++) {
      const t = clean(p.innerText, 600);
      if (t.length > title.length + 60) { snippet = t; break; }
    }
    out.push({title, href: a.href, snippet});
    if (out.length >= 20) break;
  }
  return {blocked: false, results: out};
})()"""
MUSIC_TRIES = 3  # clics máximos para llegar al modo de repetir pedido (son tres modos)
MUSIC_POLLS = 6  # cuántas veces se mira si el clic cambió el estado (de a 0,3 s)


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
    state: str = ""  # «abierto» o «cerrado» si es un desplegable (aria-expanded); vacío si no lo es


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
    """Una orden del protocolo de Chromium por WebSocket; devuelve su `result`. (Punto único que los tests bloquean:
    el trabajo está en `_talk`.)"""
    return _talk(ws_url, method, params)


DIALOG_WAIT = 1.0  # tras navegar: cuánto se espera un «¿Salir del sitio?» que llegue después de la respuesta


def _talk(ws_url: str, method: str, params: dict | None = None) -> dict:
    """La conexión de `_cdp`. Al NAVEGAR una página (Page.navigate) acepta el «¿Salir del sitio?» (`beforeunload`): YouTube
    Music lo muestra si suena algo, y quedaría esperando a la persona. Solo ese tipo de diálogo y solo al navegar: lo
    pedimos nosotros, así que salir de la página es justo lo que se quiere."""
    if not ws_url.startswith(WS_PREFIX):
        raise NavegadorError("Chromium dio una dirección de control que no es de esta compu.")
    navigating = method == "Page.navigate"

    def dialog(ws, message: dict) -> None:
        if message.get("method") == "Page.javascriptDialogOpening" and message.get("params", {}).get("type") == "beforeunload":
            ws.send(json.dumps({"id": 3, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))

    try:
        with connect(ws_url, open_timeout=TIMEOUT, close_timeout=TIMEOUT) as ws:
            if navigating:
                ws.send(json.dumps({"id": 2, "method": "Page.enable", "params": {}}))
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                message = json.loads(ws.recv(timeout=TIMEOUT * 2))
                if navigating:
                    dialog(ws, message)
                if message.get("id") == 1:
                    break
            if navigating:
                try:
                    while True:  # el diálogo puede llegar un rato después de la respuesta
                        dialog(ws, json.loads(ws.recv(timeout=DIALOG_WAIT)))
                except TimeoutError:
                    pass
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


def _bookmarks() -> list[tuple[str, str]]:
    """Punto único (bloqueado en los tests): los marcadores reales de Chromium."""
    return read_bookmarks(BOOKMARKS_FILE)


def read_bookmarks(source: Path) -> list[tuple[str, str]]:
    """Los marcadores de Chromium (nombre, dirección http(s)), en el orden en que se ven. Es un JSON que Chromium
    reescribe entero: se lee tal cual y nunca se escribe. Sin el archivo, o roto, lista vacía (los marcadores ayudan
    pero no hacen falta). Como el historial, sirven SOLO para resolver un nombre; nunca llegan al modelo."""
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    roots = data.get("roots") if isinstance(data, dict) else None
    pending = [node for node in (roots.values() if isinstance(roots, dict) else []) if isinstance(node, dict)][::-1]
    found: list[tuple[str, str]] = []
    while pending and len(found) < MAX_BOOKMARKS:  # sin recursión: una carpeta dentro de otra no tiene tope
        node = pending.pop()
        name, url = node.get("name"), node.get("url")
        if node.get("type") == "url" and isinstance(name, str) and isinstance(url, str) and url.startswith(("http://", "https://")):
            found.append((name, url))
        children = node.get("children")
        if isinstance(children, list):
            pending.extend(child for child in reversed(children) if isinstance(child, dict))
    return found


def resolve_target(text: str) -> str:
    """De lo que pidió la IA a una dirección: una con «://» se deja tal cual (después la revisan las reglas). Un nombre
    o una frase («facu», «yt», «aula virtual utn») se busca, en este orden, en los atajos de la persona (/sitio, la
    dirección guardada), en los marcadores de Chromium (la dirección completa del marcador) y en el historial (solo
    el sitio). Un dominio suelto («youtube.com») recibe https:// SOLO si la persona ya lo visitó, lo tiene en un
    marcador o es de confianza (así no se abre un dominio que el modelo inventó). Si no se reconoce, NavegadorError
    con un mensaje claro (nunca una búsqueda en Google)."""
    text = text.strip()
    if "://" in text or not text:
        return text
    if url := reglas.pick_alias(text, sites.all_sites()):
        return url
    if reglas.DOMAIN_LIKE.fullmatch(text):
        host = reglas.host_of("https://" + text)
        marked = [(url, name, 0, 0) for name, url in _bookmarks()]
        if trusted(host) or reglas.known_site(host, _history() + marked):
            return "https://" + text
        raise NavegadorError(
            f"«{host}» no está en tu historial ni en los sitios de confianza: no la invento. "
            "Pasame el nombre del sitio como lo dijo la persona o la dirección completa con https://."
        )
    if ":" in text or "/" in text:
        return text  # algo raro («javascript:…», una ruta): lo rechaza la revisión de la dirección
    blocked = config.site_list("BROWSER_BLOCKED_SITES")
    if url := reglas.pick_bookmark(text, _bookmarks(), blocked):
        return url
    host = reglas.pick_site(text, _history(), blocked)
    if not host:
        raise NavegadorError(
            f"No encontré «{text}» en los atajos, los marcadores ni el historial de Chromium. No inventes la dirección: "
            "si es un sitio de la persona (su facu, su banco), preguntale a la persona cuál es (puede guardarla con "
            "/sitio nombre = dirección) o pasame la dirección completa; si es algo público, usá buscar una vez."
        )
    return f"https://{host}/"


def _background_tab(url: str) -> str:
    """Crea una pestaña SIN pasarla al frente (Target.createTarget con `background`, por el control general de
    Chromium): no le roba el foco a lo que la persona está haciendo. Devuelve su id."""
    version = _http("GET", "/json/version")
    result = _cdp(str(version.get("webSocketDebuggerUrl", "")) if isinstance(version, dict) else "", "Target.createTarget", {"url": url, "background": True})
    if not result.get("targetId"):
        raise NavegadorError("Chromium no abrió la pestaña.")
    return str(result["targetId"])


def _webapps() -> list[tuple[str, str]]:
    """Punto único (bloqueado en los tests): las web apps de Omarchy de la persona."""
    return read_webapps(APPS_FOLDER)


def read_webapps(folder: Path) -> list[tuple[str, str]]:
    """Las web apps (nombre, dirección http(s)) de los `.desktop` de la carpeta. Solo se leen; sin la carpeta, nada."""
    found: list[tuple[str, str]] = []
    try:
        files = sorted(folder.glob("*.desktop"))[:MAX_APPS]
    except OSError:
        return found
    for file in files:
        try:
            app = reglas.parse_desktop(file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if app:
            found.append(app)
    return found


def _visible(ws_url: str) -> bool:
    """¿La página se está viendo (no está en una pestaña de fondo ni en una ventana minimizada)? Si no se puede saber, no."""
    try:
        return _cdp(ws_url, "Runtime.evaluate", {"expression": "document.visibilityState", "returnByValue": True}).get("result", {}).get("value") == "visible"
    except NavegadorError:
        return False


def _webapp_tab(url: str, app_url: str) -> Tab:
    """La página de la web app que sirve para `url`: si ya hay una abierta se navega ahí (cada lanzamiento abriría
    otra ventana); si no, se lanza `omarchy-launch-webapp` con la dirección completa. Chromium la ve como una
    pestaña más (misma sesión de Google). NavegadorError si no sale: quien llama cae a una pestaña normal."""
    app_host = reglas.host_of(app_url)
    pages = [i for i in _raw_tabs() if i.get("id") and i.get("webSocketDebuggerUrl") and reglas.matches(reglas.host_of(str(i.get("url", ""))), [app_host])]
    if pages:
        # Con varias del mismo sitio (una pestaña olvidada y la ventana de la web app), la VISIBLE: YouTube Music no
        # reproduce en una página oculta. Si no hay ninguna visible, la primera, y se la trae al frente.
        item = next((i for i in pages if _visible(str(i["webSocketDebuggerUrl"]))), pages[0])
        ws = str(item["webSocketDebuggerUrl"])
        _cdp(ws, "Page.bringToFront")
        _cdp(ws, "Page.navigate", {"url": url})
        time.sleep(SETTLE_PAUSE)  # que empiece a navegar antes de preguntarle si cargó
        return _tab(item)
    before = {i.get("id") for i in _raw_tabs()}
    if _run(["omarchy-launch-webapp", url]).returncode != 0:
        raise NavegadorError("No pude abrir la web app.")
    for _ in range(LOAD_TRIES):
        for item in _raw_tabs():
            if item.get("id") not in before and reglas.matches(reglas.host_of(str(item.get("url", ""))), [app_host]):
                return _tab(item)
        time.sleep(0.5)
    raise NavegadorError("La web app no apareció.")


def open_url(url: str, background: bool = False) -> Tab:
    """Abre la dirección en una pestaña nueva (al frente, o con `background` detrás, sin quitarle el foco a nadie).
    Si el sitio tiene una web app de Omarchy (BROWSER_USE_WEBAPPS), usa su ventana en vez de una pestaña.
    Revisa la dirección antes y la dirección final después."""
    ensure_running()
    url = url.strip()
    _check_destination(url)
    tab = None
    app_url = None if background or not config.enabled("BROWSER_USE_WEBAPPS") else reglas.pick_webapp(reglas.host_of(url), _webapps())
    if app_url:
        try:
            tab = _webapp_tab(url, app_url)
        except NavegadorError:
            tab = None  # sin web app que ande, la pestaña de siempre
    if tab is not None:
        tab_id = tab.id
    elif background:
        tab_id = _background_tab(url)
        tab = Tab(tab_id, "", url, reglas.host_of(url))
    else:
        item = _http("PUT", "/json/new?" + quote(url, safe=""))  # Chromium parte el pedido en «&» y decodifica: todo escapado
        if not isinstance(item, dict) or not item.get("id"):
            raise NavegadorError("Chromium no abrió la pestaña.")
        tab_id = str(item["id"])
        tab = _tab(item)
    _opened.add(tab_id)
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


# --- Buscar en Google --------------------------------------------------------------------------------


def _close(tab: Tab) -> None:
    """Cierra una pestaña que abrió Epsilon; si no se puede, queda abierta (no es un error para nadie)."""
    try:
        _http("GET", f"/json/close/{tab.id}")
    except NavegadorError:
        pass
    _opened.discard(tab.id)


def _result_url(href: str) -> str:
    """La dirección real de un resultado: un enlace de Google «/url?q=…» se desenvuelve; uno a Google mismo
    (más resultados, imágenes, la portada) queda vacío."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return ""
    if not GOOGLE_HOST.fullmatch(reglas.host_of(href)):
        return href
    if parts.path == "/url":
        inner = (parse_qs(parts.query).get("q") or parse_qs(parts.query).get("url") or [""])[0]
        return inner if not GOOGLE_HOST.fullmatch(reglas.host_of(inner)) else ""
    return ""


def _search_results(data: object) -> list[tuple[str, str, str]]:
    """(título, dirección, texto) de los resultados que se podrían abrir: http(s), sin esta compu ni la red local, sin
    los sitios bloqueados ni Google mismo, sin repetir. Todo en una sola línea y recortado: es dato de una página."""
    items = data.get("results") if isinstance(data, dict) else None
    blocked = config.site_list("BROWSER_BLOCKED_SITES")
    found: list[tuple[str, str, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = _result_url(str(item.get("href", ""))[:500])
        if not url or reglas.local_problem(url) or reglas.matches(reglas.host_of(url), blocked) or any(url == f[1] for f in found):
            continue
        title = " ".join(str(item.get("title", "")).split())[:150]
        snippet = " ".join(str(item.get("snippet", "")).split()).replace(title, "", 1)
        snippet = CRUMB.sub("", snippet).strip(" ·-—")[:MAX_SNIPPET]  # sin «UTN.BA https://frba.utn.edu.ar › posgrado ›», que repite la dirección
        found.append((title, url, snippet))
        if len(found) >= MAX_RESULTS:
            break
    return found


def search(query: str) -> str:
    """Busca en Google con el Chromium de la persona: abre los resultados en una pestaña nueva DETRÁS (no le roba el
    foco a la persona), los lee con el script fijo y la cierra. No abre ninguno: devuelve título, sitio y dirección para que se elija uno con `abrir`. Si Google
    pide el captcha, la pestaña queda abierta para que la persona lo resuelva."""
    query = " ".join(query.split())
    if not query:
        raise NavegadorError("Falta qué buscar.")
    if len(query) > MAX_QUERY:
        raise NavegadorError(f"La búsqueda es muy larga: como mucho {MAX_QUERY} caracteres.")
    tab = open_url(SEARCH_URL + quote_plus(query), background=True)
    try:
        data = _cdp(tab.ws, "Runtime.evaluate", {"expression": SEARCH_JS, "returnByValue": True}).get("result", {}).get("value")
    except NavegadorError:
        _close(tab)
        raise
    if isinstance(data, dict) and data.get("blocked") is True:
        raise NavegadorError(f"Google pidió comprobar que es una persona: que la persona lo resuelva en la pestaña {tab.short} y después buscá de nuevo.")
    _close(tab)
    results = _search_results(data)
    if not results:
        return f"Google no devolvió resultados que se puedan abrir para «{query}»."
    lines = [f"Resultados de Google para «{query}»:"]
    for n, (title, url, snippet) in enumerate(results, 1):
        lines += [f"{n}. {title} — {reglas.host_of(url)}", f"   {url}"] + ([f"   {snippet}"] if snippet else [])
    return "\n".join(lines)


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
    found = [e for e in data["elements"] if isinstance(e, dict) and e.get("kind") != "password"][: config.number("BROWSER_MAX_READ_ELEMENTS")]
    elements = [
        Element(
            n,
            " ".join(str(e.get("label", "")).split())[:80],  # una sola línea: la página no puede fingir otras
            str(e.get("kind", "button")),
            float(e.get("x", 0)),
            float(e.get("y", 0)),
            bool(e.get("sensitive", True)),
            str(e.get("href", ""))[:500],
            state if (state := str(e.get("state", ""))) in ("abierto", "cerrado") else "",
        )
        for n, e in enumerate(found, 1)
    ]
    return data, elements


def read(args: str) -> str:
    """El título, el texto principal (acotado) y los elementos tocables numerados de la pestaña.
    «id abajo» o «id arriba» desplaza la página antes de leer y «id pasar N» pone el mouse sobre el elemento N (menús de hover);
    ambos con eventos del mouse, sin JavaScript."""
    parts = args.split()
    direction = parts[1].lower() if len(parts) > 1 else ""
    hover = direction == "pasar" and len(parts) == 3 and parts[2].isascii() and parts[2].isdigit() and int(parts[2]) >= 1
    if not hover and (len(parts) > 2 or direction not in ("", "abajo", "arriba")):
        raise NavegadorError("Después del id de la pestaña solo puede ir «abajo», «arriba» o «pasar N» (N: el número del elemento).")
    tab = resolve_tab(parts[0] if parts else "")
    if hover:  # abre los menús que solo se despliegan al pasar el mouse
        now = _locate(tab, int(parts[2]), element(parts[0], int(parts[2])))
        _cdp(tab.ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": now.x, "y": now.y})
        time.sleep(0.5)
    elif direction:
        pixels = config.number("BROWSER_SCROLL_PIXELS")  # cuánto baja o sube `leer … abajo|arriba`
        delta = pixels if direction == "abajo" else -pixels
        _cdp(tab.ws, "Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 300.0, "y": 300.0, "deltaX": 0, "deltaY": delta})
        time.sleep(0.4)
    data, elements = _collect(tab)
    _elements[tab.id] = elements
    _sites[tab.id] = tab.host
    _last.hosts = [tab.host, reglas.host_of(str(data.get("url", "")))]  # el de antes y el de después: la página pudo navegar
    title = " ".join(str(data.get("title", "")).split())[:MAX_TITLE]
    lines = [f"Título: {title}", f"Sitio: {tab.host}", "Elementos:"]
    lines += [f"{e.n}: {KINDS.get(e.kind, 'elemento')} «{e.label}»" + (f" ({e.state})" if e.state else "") for e in elements]
    if data.get("more") is True:
        lines.append(f"(Hay más abajo: leer con «{tab.short} abajo» para ver el resto.)")
    lines += ["Texto:", str(data.get("text", ""))[: config.number("BROWSER_MAX_READ_CHARS")]]  # al final: si el resultado se recorta, se pierde texto y no números
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


def _locate(tab: Tab, n: int, saved: Element, field: bool = False) -> Element:
    """El elemento `n` tal como está AHORA, después de comprobar que la página sigue siendo la que se leyó
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
    return now


def _press(tab: Tab, n: int, saved: Element, field: bool = False) -> Element:
    """Hace clic de verdad en el elemento `n`, después de comprobarlo con `_locate`."""
    now = _locate(tab, n, saved, field)
    for kind in ("mousePressed", "mouseReleased"):
        _cdp(tab.ws, "Input.dispatchMouseEvent", {"type": kind, "x": now.x, "y": now.y, "button": "left", "clickCount": 1})
    return now


def _settle(tab: Tab) -> None:
    """Después de un clic, espera a que la página termine de cargar (si el clic la hizo navegar): leer antes mostraba la anterior."""
    time.sleep(SETTLE_PAUSE)
    for _ in range(SETTLE_TRIES):
        try:
            state = _cdp(tab.ws, "Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})
        except NavegadorError:  # justo navegando, el contexto de la página desaparece un instante
            time.sleep(0.5)
            continue
        value = state.get("result", {}).get("value")
        if not isinstance(value, str) or value == "complete":
            return
        time.sleep(0.5)


def _location(tab: Tab) -> str:
    """La dirección actual de la pestaña (script fijo); vacía si no se pudo saber."""
    try:
        state = _cdp(tab.ws, "Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
    except NavegadorError:
        return ""
    value = state.get("result", {}).get("value")
    return value if isinstance(value, str) else ""


def _same_page(a: str, b: str) -> bool:
    return a.split("#", 1)[0].rstrip("/") == b.split("#", 1)[0].rstrip("/")


def click(tab_ref: str, n: int) -> str:
    """Hace clic en el elemento `n` de la última lectura. Es un clic real: la reproducción lo necesita.
    Espera a que cargue lo que abrió el clic; si abrió una pestaña nueva (enlace «target=_blank»), la suma a las que Epsilon puede usar.
    Si era un enlace y la página no cambió (un cajón animado desplazó el elemento y el clic cayó en otro lado), abre el destino del enlace."""
    tab, saved = resolve_tab(tab_ref), element(tab_ref, n)
    before = {item.get("id") for item in _raw_tabs()}
    url_before = _location(tab)
    now = _press(tab, n, saved)
    message = f"Hice clic en «{now.label}»."
    try:
        _settle(tab)
        fresh = [_tab(item) for item in _raw_tabs() if item.get("id") not in before]
        _opened.update(t.id for t in fresh if t.id)
        shown = {t.id for t in tabs()[0]}
        for t in fresh:
            if t.id in shown:
                message += f" Se abrió una pestaña nueva: {t.short} ({t.host})."
        href = saved.href
        url_after = _location(tab)
        if (not fresh and url_before and href.startswith(("http://", "https://")) and _same_page(url_after, url_before)
                and not _same_page(href, url_after)):
            _check_destination(href)  # las mismas reglas que al abrir una dirección
            _cdp(tab.ws, "Page.navigate", {"url": href})
            _settle(tab)
            message += " El clic no cambió la página, así que abrí el destino del enlace directamente."
    except NavegadorError:
        pass  # el clic ya se hizo: avisar de esto no puede tirarlo abajo
    return message


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


def _music_state(tab: Tab) -> dict:
    """El estado de la barra de YouTube Music de esa pestaña (ver MUSIC_JS)."""
    data = _cdp(tab.ws, "Runtime.evaluate", {"expression": MUSIC_JS, "returnByValue": True}).get("result", {}).get("value")
    if not isinstance(data, dict) or data.get("repeat") not in MUSIC_REPEAT:
        raise NavegadorError("No encuentro los controles de YouTube Music: ¿hay algo reproduciéndose en esa pestaña?")
    return data


def _music_click(tab: Tab, spot: object, changed) -> dict:
    """Un clic de verdad en el botón (donde lo ubicó MUSIC_JS) y espera a que el estado cambie. Devuelve el estado nuevo."""
    if not isinstance(spot, dict) or not all(isinstance(spot.get(axis), (int, float)) for axis in ("x", "y")):
        raise NavegadorError("No veo ese botón en la pestaña de YouTube Music (¿está la barra del reproductor a la vista?).")
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        _cdp(tab.ws, "Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"], "button": "left", "clickCount": 1})
    for _ in range(MUSIC_POLLS):
        time.sleep(0.3)
        state = _music_state(tab)
        if changed(state):
            return state
    return state


def _music_tab() -> Tab:
    if not config.enabled("BROWSER_ENABLED"):
        raise NavegadorError("El navegador está apagado (BROWSER_ENABLED=no).")
    tab = next((t for t in tabs()[0] if t.host == MUSIC_HOST), None)  # tabs() ya deja afuera sitios bloqueados y pestañas personales
    if tab is None:
        raise NavegadorError("No hay una pestaña de YouTube Music abierta (o su sitio no es de confianza).")
    return tab


def _same_song(a: str, b: str) -> bool:
    return bool(a.strip()) and " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def music_is_playing(title: str) -> bool:
    """¿La pestaña de YouTube Music muestra esta canción? MPRIS no dice de qué página viene lo que suena en Chromium,
    así que se compara el título. Cualquier problema (navegador apagado o cerrado, sin pestaña) es simplemente «no»."""
    try:
        return _same_song(_music_state(_music_tab()).get("title", ""), title)
    except NavegadorError:
        return False


def _music_check(state: dict, expect: str | None) -> None:
    if expect is not None and not _same_song(str(state.get("title", "")), expect):
        raise NavegadorError("Lo que suena no es de YouTube Music (la pestaña muestra otra canción): no toco sus botones.")


def music_repeat(want: str | None, expect: str | None = None) -> str:
    """Pone repetir de YouTube Music en NONE, ALL u ONE; con None alterna canción (ONE) y apagado. Solo clics en el
    botón de la barra: nada de texto ni código que venga de afuera. Con `expect` (el título de lo que suena según el
    reproductor), no actúa si la pestaña muestra otra canción. Devuelve el modo en que quedó."""
    if want is not None and want not in MUSIC_REPEAT:
        raise NavegadorError("Modo de repetir desconocido.")
    tab = _music_tab()
    state = _music_state(tab)
    _music_check(state, expect)
    target = want or ("NONE" if state["repeat"] == "ONE" else "ONE")
    for _ in range(MUSIC_TRIES):
        if state["repeat"] == target:
            break
        before = state["repeat"]
        state = _music_click(tab, state.get("repeatAt"), lambda s: s.get("repeat") != before)
    if state["repeat"] != target:
        raise NavegadorError("No pude cambiar repetir en YouTube Music.")
    audit("musica", tab.host, f"repetir {target}", asked=False)
    return target


def music_shuffle(want: bool | None, expect: str | None = None) -> bool:
    """Prende o apaga el aleatorio de YouTube Music; con None alterna. `expect` como en `music_repeat`. Devuelve cómo quedó."""
    tab = _music_tab()
    state = _music_state(tab)
    _music_check(state, expect)
    target = (not state["shuffle"]) if want is None else want
    if bool(state["shuffle"]) != target:
        state = _music_click(tab, state.get("shuffleAt"), lambda s: bool(s.get("shuffle")) == target)
        if bool(state["shuffle"]) != target:
            raise NavegadorError("No pude cambiar el aleatorio en YouTube Music.")
    audit("musica", tab.host, f"aleatorio {'sí' if target else 'no'}", asked=False)
    return target


def _play_spots(tab: Tab) -> dict:
    """Dónde están los botones de reproducir de la página (ver PLAY_JS); vacío si todavía no se dibujaron."""
    data = _cdp(tab.ws, "Runtime.evaluate", {"expression": PLAY_JS, "returnByValue": True}).get("result", {}).get("value")
    return data if isinstance(data, dict) else {}


def _play_video(video: str) -> str:
    """Pone un video de YouTube (no de YouTube Music) por su id: la dirección la arma el código. Si la página lo deja
    pausado (la política de autoplay), un clic de mouse en el video lo arranca. Devuelve el título."""
    if not reglas.VIDEO_ID.fullmatch(video):
        raise NavegadorError("Ese id de video no es válido.")
    tab = open_url(f"https://www.youtube.com/watch?v={video}")
    state: dict = {}

    def look() -> dict:
        value = _cdp(tab.ws, "Runtime.evaluate", {"expression": VIDEO_JS, "returnByValue": True}).get("result", {}).get("value")
        return value if isinstance(value, dict) else {}

    for _ in range(PLAY_WAIT):  # el reproductor aparece un rato después de cargar
        state = look()
        if state:
            break
        time.sleep(0.5)
    if not state:
        raise NavegadorError("No encuentro el video en la página: puede que YouTube haya cambiado o que el video no esté disponible.")
    if state.get("paused") and isinstance(state.get("at"), dict):
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            _cdp(tab.ws, "Input.dispatchMouseEvent", {"type": kind, "x": state["at"]["x"], "y": state["at"]["y"], "button": "left", "clickCount": 1})
        for _ in range(MUSIC_POLLS):
            time.sleep(0.3)
            state = look()
            if not state.get("paused"):
                break
    audit("musica", "www.youtube.com", f"poner video {video}", asked=False)
    if state.get("paused"):
        raise NavegadorError("Abrí el video pero no veo que haya empezado: mirá la pestaña " + tab.short + ".")
    title = _clean_video_title(str(state.get("title", "")))[:100]
    return f"Reproduciendo en YouTube: {title}"


def play_music(kind: str, value: str, shuffle: bool = False) -> str:
    """Pone música en YouTube Music: una playlist por id o una búsqueda de canciones. La dirección la arma ESTE código
    (el id ya validado por `reglas.parse_play`; el texto va escapado en la búsqueda) y abre con `open_url`, así que usa
    la web app de Omarchy si hay. Después toca con el mouse el botón de reproducir (o el de aleatorio) que ubica
    PLAY_JS y confirma mirando el título de la barra del reproductor. Devuelve qué quedó sonando."""
    if not config.enabled("BROWSER_ENABLED"):
        raise NavegadorError("El navegador está apagado (BROWSER_ENABLED=no).")
    if kind == "video":
        return _play_video(value)
    if kind == "playlist":
        if not reglas.PLAYLIST_ID.fullmatch(value):
            raise NavegadorError("Ese id de playlist no es válido.")
        url, button = f"https://{MUSIC_HOST}/playlist?list={value}", "shuffle" if shuffle else "play"
    elif kind == "canciones":
        text = " ".join(value.split())[:MAX_QUERY]
        if not text:
            raise NavegadorError("Falta qué poner.")
        url, button = f"https://{MUSIC_HOST}/search?q={quote_plus(text)}", "result"
    else:
        raise NavegadorError("Solo sé poner una playlist o canciones.")
    tab = open_url(url)
    if tab.host != MUSIC_HOST:
        raise NavegadorError("La página no quedó en YouTube Music.")
    spot = None
    for _ in range(PLAY_WAIT):  # YouTube Music dibuja los botones un rato después de cargar
        spot = _play_spots(tab).get(button)
        if spot:
            break
        time.sleep(0.5)
    if not spot:
        raise NavegadorError(
            "No encuentro el botón de reproducir: puede que YouTube Music haya cambiado. "
            "Probá con leer y clic sobre la pestaña " + tab.short + "."
        )
    try:
        before = str(_music_state(tab).get("title", ""))
    except NavegadorError:
        before = ""  # todavía no había reproductor
    try:
        state = _music_click(tab, spot, lambda s: bool(str(s.get("title", "")).strip()) and not _same_song(str(s.get("title", "")), before))
    except NavegadorError:
        state = {}  # sin barra de reproductor: no arrancó nada
    title = " ".join(str(state.get("title", "")).split())[:100]
    if kind == "canciones" and shuffle:
        music_shuffle(True)
    audit("musica", MUSIC_HOST, f"poner {kind} {value}"[:100], asked=False)
    if not title:
        raise NavegadorError("Toqué reproducir pero no veo que haya empezado: mirá la pestaña " + tab.short + ".")
    return f"Sonando en YouTube Music: {title}" + (" (aleatorio)" if shuffle else "")


def _music_results(data: object, limit: int = MUSIC_RESULTS) -> list[tuple[str, str, str]]:
    """(id, título, detalle) de las playlists que devolvió MUSIC_SEARCH_JS: id válido, sin repetir, una sola línea y
    recortado (es dato de otros). El detalle es lo que acompaña al título («Playlist • autor • 69 canciones»)."""
    found: list[tuple[str, str, str]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        list_id = str(item.get("id", ""))
        if not reglas.PLAYLIST_ID.fullmatch(list_id) or any(list_id == f[0] for f in found):
            continue
        title = " ".join(str(item.get("title", "")).split())[:100]
        detail = " ".join(str(item.get("text", "")).split()).replace(title, "", 1).strip(" ·-—•")[:80]
        if detail.casefold().startswith("podcast"):  # el mismo enlace lo usan los podcasts: no son música para poner
            continue
        found.append((list_id, title or "(sin título)", detail))
        if len(found) >= limit:
            break
    return found


def music_search(query: str) -> str:
    """Busca playlists en YouTube Music: abre los resultados en una pestaña DETRÁS (no interrumpe lo que suena ni le roba
    el foco a la persona), los lee con MUSIC_SEARCH_JS y la cierra. No reproduce nada: devuelve id, título y detalle
    para elegir una con `play_music`."""
    if not config.enabled("BROWSER_ENABLED"):
        raise NavegadorError("El navegador está apagado (BROWSER_ENABLED=no).")
    query = " ".join(query.split())
    if not query:
        raise NavegadorError("Falta qué buscar.")
    if len(query) > MAX_QUERY:
        raise NavegadorError(f"La búsqueda es muy larga: como mucho {MAX_QUERY} caracteres.")
    tab = open_url(f"https://{MUSIC_HOST}/search?q={quote_plus(query)}", background=True)
    try:
        results: list[tuple[str, str, str]] = []
        for _ in range(PLAY_WAIT):  # los resultados se dibujan un rato después de cargar
            data = _cdp(tab.ws, "Runtime.evaluate", {"expression": MUSIC_SEARCH_JS, "returnByValue": True}).get("result", {}).get("value")
            results = _music_results(data)
            if results:
                break
            time.sleep(0.5)
    finally:
        _close(tab)
    audit("musica", MUSIC_HOST, f"buscar {query}"[:100], asked=False)
    if not results:
        return f"YouTube Music no devolvió playlists para «{query}»: probá con otras palabras."
    lines = [f"Playlists de YouTube Music para «{query}» (para ponerla: poner con «playlist <id>»):"]
    for n, (list_id, title, detail) in enumerate(results, 1):
        lines.append(f"{n}. {title}" + (f" — {detail}" if detail else "") + f"\n   id: {list_id}")
    return "\n".join(lines)


COUNT = re.compile(r"(\d[\d.,]*)\s*(?:pistas|canciones|songs|tracks)", re.IGNORECASE)


def music_library() -> list[tuple[str, str, int]]:
    """Las playlists de la biblioteca de YouTube Music de la persona: (id, título, cantidad de canciones; 0 si no lo
    dice). Primero las que armó ella («N pistas») y después las que arma YouTube (mezclas, recaps), hasta
    MAX_PLAYLISTS. Se lee desde una pestaña DETRÁS con MUSIC_LIBRARY_JS (solo lee) y se cierra."""
    if not config.enabled("BROWSER_ENABLED"):
        raise NavegadorError("El navegador está apagado (BROWSER_ENABLED=no).")
    tab = open_url(f"https://{MUSIC_HOST}/library/playlists", background=True)
    try:
        found: list[tuple[str, str, str]] = []
        for _ in range(PLAY_WAIT):
            data = _cdp(tab.ws, "Runtime.evaluate", {"expression": MUSIC_LIBRARY_JS, "returnByValue": True}).get("result", {}).get("value")
            found = _music_results(data, limit=60)
            if found:
                break
            time.sleep(0.5)
    finally:
        _close(tab)
    mine = re.compile(r"\bpistas\b", re.IGNORECASE)
    found.sort(key=lambda item: 0 if mine.search(item[2]) else 1)  # estable: dentro de cada grupo, el orden de la página
    rows = []
    for list_id, title, detail in found[:MAX_PLAYLISTS]:
        match = COUNT.search(detail)
        rows.append((list_id, title, int(re.sub(r"\D", "", match.group(1)) or 0) if match else 0))
    return rows


def music_tracks(ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Las primeras INDEX_TRACKS canciones (título, artista) de cada playlist, leídas con PLAYLIST_JS (solo lee) en UNA
    pestaña DETRÁS que va de página en página con direcciones que arma el código (ids validados). Una que no
    carga queda con lista vacía: no frena a las demás."""
    if not config.enabled("BROWSER_ENABLED"):
        raise NavegadorError("El navegador está apagado (BROWSER_ENABLED=no).")
    ids = [i for i in ids if reglas.PLAYLIST_ID.fullmatch(i)][:MAX_PLAYLISTS]
    if not ids:
        return {}
    tab = open_url(f"https://{MUSIC_HOST}/library/playlists", background=True)
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        for list_id in ids:
            _cdp(tab.ws, "Page.navigate", {"url": f"https://{MUSIC_HOST}/playlist?list={list_id}"})
            time.sleep(SETTLE_PAUSE)
            rows: list[tuple[str, str]] = []
            for _ in range(PLAY_WAIT):
                here = _cdp(tab.ws, "Runtime.evaluate", {"expression": "location.href", "returnByValue": True}).get("result", {}).get("value")
                if f"list={list_id}" not in str(here):  # todavía es la página anterior: no leer sus canciones como de esta
                    time.sleep(0.5)
                    continue
                data = _cdp(tab.ws, "Runtime.evaluate", {"expression": PLAYLIST_JS, "returnByValue": True}).get("result", {}).get("value")
                rows = [
                    (" ".join(str(r.get("title", "")).split())[:100], " ".join(str(r.get("artist", "")).split())[:60])
                    for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and str(r.get("title", "")).strip()
                ][:INDEX_TRACKS]
                if rows:
                    break
                time.sleep(0.5)
            out[list_id] = rows
    finally:
        _close(tab)
    audit("musica", MUSIC_HOST, f"leer {len(out)} playlists", asked=False)
    return out


def _clean_video_title(title: str) -> str:
    """El título de una página de YouTube sin « - YouTube» al final ni el «(1) » de las notificaciones al principio."""
    return re.sub(r"^\(\d+\)\s*", "", re.sub(r"\s*-\s*YouTube$", "", " ".join(title.split())))


def _youtube_history() -> list[tuple[str, str, int]]:
    """Punto único (bloqueado en los tests): los videos de YouTube vistos en las últimas semanas, del historial real."""
    return read_youtube_history(HISTORY_FILE, HISTORY_DAYS, datetime.now())


def read_youtube_history(source: Path, days: int, now: datetime) -> list[tuple[str, str, int]]:
    """(id, título, veces) de los videos de YouTube (`watch?v=`) que la persona vio en los últimos `days` días, uno por
    video (las direcciones con &t= o &list= se suman). Como `read_history`: se lee una copia en solo lectura. Es el único
    lugar que mira las FECHAS del historial; nunca devuelve direcciones de otros sitios."""
    if not source.is_file():
        raise NavegadorError("No encuentro el historial de Chromium.")
    since = int((now.timestamp() - days * 86400 + 11644473600) * 1_000_000)  # Chromium cuenta microsegundos desde 1601
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "history.db"
        shutil.copyfile(source, copy)
        try:
            db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                rows = db.execute(
                    "SELECT url, title, visit_count FROM urls WHERE hidden = 0 AND last_visit_time >= ? "
                    "AND (url LIKE 'https://www.youtube.com/watch?%' OR url LIKE 'https://youtube.com/watch?%' OR url LIKE 'https://m.youtube.com/watch?%') "
                    "ORDER BY visit_count DESC LIMIT 2000",
                    (since,),
                ).fetchall()
            finally:
                db.close()
        except sqlite3.Error:
            raise NavegadorError("No pude leer el historial de Chromium.") from None
    videos: dict[str, list] = {}
    for url, title, visits in rows:
        video = reglas.video_id(str(url))
        if not video:
            continue
        entry = videos.setdefault(video, [video, "", 0])
        entry[1] = entry[1] or _clean_video_title(str(title or ""))
        entry[2] += int(visits)
    return [(v, t, n) for v, t, n in videos.values()]


def youtube_history(query: str) -> str:
    """Los videos de YouTube que la persona más vio en las últimas semanas (con su id para ponerlos), filtrados por las
    palabras de `query` (todas, sin importar acentos ni mayúsculas). Hasta HISTORY_RESULTS, los más vistos primero. Los
    títulos los escribió otro: son dato. Solo lee la copia del historial; no abre nada."""
    if not config.enabled("BROWSER_ENABLED"):
        raise NavegadorError("El navegador está apagado (BROWSER_ENABLED=no).")
    if reglas.matches("www.youtube.com", config.site_list("BROWSER_BLOCKED_SITES")):
        raise NavegadorError("YouTube está bloqueado: no miro ese historial.")
    words = reglas.fold(" ".join(query.split())[:MAX_QUERY]).split()
    rows = [r for r in _youtube_history() if all(w in reglas.fold(r[1]) for w in words)]
    rows.sort(key=lambda r: -r[2])
    if not rows:
        return "No encontré videos de YouTube en tu historial de las últimas semanas" + (f" que digan «{' '.join(words)}»." if words else ".")
    lines = ["Videos de YouTube que viste en las últimas semanas (los más vistos primero; para ponerlo: poner con «video <id>»):"]
    for i, (video, title, visits) in enumerate(rows[:HISTORY_RESULTS], 1):
        short = " ".join(title.split())[:100] or "(sin título)"
        lines.append(f"{i}. {short} — {visits} veces\n   id: {video}")
    audit("musica", "www.youtube.com", f"historial {' '.join(words)}"[:100], asked=True)
    return "\n".join(lines)


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
