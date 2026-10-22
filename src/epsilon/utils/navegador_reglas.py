import ipaddress
import re
from urllib.parse import urlsplit

# Reglas del navegador de la IA, todas funciones puras: sin red, sin Chromium, sin ajustes. Quien las usa
# (navegador.py, ai/browser_guard.py) les pasa las listas de sitios y las IP ya resueltas. Fallan cerradas:
# si algo no se puede determinar, se rechaza o se trata como sensible.

SCHEMES = ("http", "https")
LOCAL_SUFFIXES = (".local", ".localhost", ".internal")
# Rótulos de botones que envían, publican, borran o cobran (español e inglés). Es una heurística de partida.
SENSITIVE = re.compile(
    r"\b(?:envi\w*|publi\w*|coment\w*|elimin\w*|borr\w*|compr\w*|pag\w*|suscrib\w*|suscrip\w*|"
    r"respond\w*|compart\w*|guard\w*|crea\w*|subir|denunc\w*|bloque\w*|cerrar\s+sesi\w*|acept\w*|anular|"
    r"send\w*|post\w*|publish\w*|comment\w*|delete\w*|remove\w*|buy\w*|purchase\w*|pay\w*|subscri\w*|unsubscri\w*|"
    r"submit\w*|confirm\w*|repl(?:y|ies)|shar\w*|sav\w*|creat\w*|upload\w*|report\w*|block\w*|sign\W?out|log\W?out|accept\w*)\b",
    re.IGNORECASE,
)


def host_of(url: str) -> str:
    """El sitio real de una dirección, en minúsculas («https://a.com@b.com/» es b.com); vacío si no hay."""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def matches(host: str, sites: list[str]) -> bool:
    """¿El sitio es uno de la lista o un subdominio suyo? «eviltyoutube.com» no es de «youtube.com»."""
    return bool(host) and any(host == site or host.endswith("." + site) for site in sites)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def local_problem(url: str) -> str | None:
    """Lo que se ve de una dirección SIN consultar ningún DNS: esquema, usuario o «%» escondido en el sitio, sitios
    de esta compu y direcciones IP no públicas. Sirve para revisar una pestaña que ya está abierta."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "Esa dirección no es válida."
    if parts.scheme not in SCHEMES:
        return "Solo abro direcciones http o https."
    if "@" in parts.netloc or "%" in parts.netloc:
        return "No abro direcciones con usuario o con signos escondidos en el sitio: esconden el sitio real."
    host = host_of(url)
    if not host:
        return "Esa dirección no tiene sitio."
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return "No abro direcciones de esta compu ni de la red local."
    if _is_ip(host) and not ipaddress.ip_address(host).is_global:
        return "No abro direcciones de esta compu ni de la red local."
    return None


def has_embedded_url(url: str) -> bool:
    """¿La dirección lleva otra dirección adentro (un «redirect?q=https://…»)? Así se salta de un sitio de confianza a otro."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    rest = f"{parts.path}?{parts.query}#{parts.fragment}"
    return bool(re.search(r"https?(?::|%3a)", rest, re.IGNORECASE)) or parts.path.lower().startswith("/redirect")


def url_problem(url: str, addresses: list[str]) -> str | None:
    """Por qué una dirección no se puede abrir (mensaje para la persona), o None si sí.
    `addresses` son las IP en que se resolvió el sitio; si el sitio ya es una IP, vale ella misma."""
    if problem := local_problem(url):
        return problem
    host = host_of(url)
    resolved = [host] if _is_ip(host) else addresses
    if not resolved:
        return "No pude encontrar ese sitio."
    for address in resolved:
        try:
            public = ipaddress.ip_address(address).is_global
        except ValueError:
            public = False
        if not public:
            return "No abro direcciones de esta compu ni de la red local."
    return None


def is_sensitive_label(label: str) -> bool:
    """¿El rótulo de un botón parece enviar, publicar, borrar o pagar? Un rótulo vacío cuenta como sensible."""
    return not label.strip() or bool(SENSITIVE.search(label))


# --- Nombres de sitio: «yt» → youtube.com, sacado del historial ------------------------------------------------

SECOND_LEVEL = {"co", "com", "net", "org", "gov", "edu", "gob"}  # mercadolibre.com.ar: el sitio son tres partes
NAME_ONLY = re.compile(r"[\w-]{2,40}")
DOMAIN_LIKE = re.compile(r"(?:[\w-]+\.)+[a-z]{2,}(?:[/?#]\S*)?", re.IGNORECASE)


def site_of(host: str) -> str:
    """El sitio «de la persona» de un host: music.youtube.com → youtube.com, www.x.com.ar → x.com.ar."""
    parts = host.split(".")
    keep = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL else 2
    return ".".join(parts[-keep:])


def _subsequence(small: str, big: str) -> bool:
    letters = iter(big)
    return all(letter in letters for letter in small)  # cada letra aparece, en orden, después de la anterior


def pick_site(query: str, rows: list[tuple[str, str, int, int]], blocked: list[str]) -> str | None:
    """El sitio del historial que mejor responde a un nombre corto (`rows`: url, título, visitas, veces escrita).
    Se prefiere, en orden: el nombre del sitio empieza igual («you» → youtube.com), una palabra de su título empieza
    igual, y las letras aparecen en orden dentro del nombre («yt»). Dentro de cada grupo gana el más usado, con más
    peso lo que la persona escribió a mano. Los sitios bloqueados nunca salen. None si nada coincide."""
    q = query.strip().lower()
    if not NAME_ONLY.fullmatch(q):
        return None
    sites: dict[str, dict] = {}
    for url, title, visits, typed in rows:
        host = host_of(url)
        if not host or _is_ip(host) or matches(host, blocked):
            continue
        entry = sites.setdefault(site_of(host.removeprefix("www.")), {"score": 0, "words": set()})
        entry["score"] += visits + 10 * typed
        entry["words"].update(re.findall(r"\w+", (title or "").lower()))
    best: tuple[int, int, str] | None = None
    for site, entry in sites.items():
        name = site.split(".")[0]
        if name.startswith(q):
            tier = 0
        elif any(word.startswith(q) for word in entry["words"]):
            tier = 1
        elif _subsequence(q, name):
            tier = 2
        else:
            continue
        candidate = (tier, -entry["score"], site)
        best = candidate if best is None or candidate < best else best
    return best[2] if best else None
