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
    r"\b(?:envi\w*|publi\w*|coment\w*|elimin\w*|borr\w*|compr\w*|pag\w*|suscrib\w*|"
    r"send\w*|post\w*|publish\w*|comment\w*|delete\w*|remove\w*|buy\w*|purchase\w*|pay\w*|subscri\w*|submit\w*|confirm\w*)\b",
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


def url_problem(url: str, addresses: list[str]) -> str | None:
    """Por qué una dirección no se puede abrir (mensaje para la persona), o None si sí.
    `addresses` son las IP en que se resolvió el sitio; si el sitio ya es una IP, vale ella misma."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "Esa dirección no es válida."
    if parts.scheme not in SCHEMES:
        return "Solo abro direcciones http o https."
    if "@" in parts.netloc:
        return "No abro direcciones con usuario adentro: esconden el sitio real."
    host = host_of(url)
    if not host:
        return "Esa dirección no tiene sitio."
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return "No abro direcciones de esta compu ni de la red local."
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
