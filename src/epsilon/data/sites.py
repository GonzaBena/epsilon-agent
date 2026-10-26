import json
import os
import re
import threading

from epsilon.data import notes
from epsilon.utils import navegador_reglas as reglas

# Los atajos de sitios de la persona: «facu» → https://aulasvirtuales.frba.utn.edu.ar/, en DATA_DIR/sites.json (nombre
# fijo, siempre por safe_path). El navegador de la IA los mira ANTES que los marcadores y el historial para entender un
# nombre suelto (navegador.resolve_target), así lo que solo sabe la persona («facu», «inglés») no depende del modelo.
# Solo la persona los agrega o borra (/sitio): la IA no tiene ninguna herramienta que los toque. Cada cambio reescribe
# el archivo por uno temporal (excepción a «solo agregar», como las rutinas).

FILE = "sites.json"
MAX_SITES = 100
MAX_URL = 500
NAME = re.compile(r"\w[\w -]{0,39}")  # una o pocas palabras: «facu», «aula inglés»


class SitesError(Exception):
    """No se pudo guardar el atajo; el texto va para la persona."""


_lock = threading.Lock()


def _read() -> dict[str, str]:
    try:
        data = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = data.get("sites", {}) if isinstance(data, dict) else {}
    return {name: url for name, url in found.items() if isinstance(name, str) and isinstance(url, str)} if isinstance(found, dict) else {}


def _write(sites: dict[str, str]) -> None:
    path = notes.safe_path(FILE)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"sites": sites}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def all_sites() -> dict[str, str]:
    """Los atajos guardados (nombre → dirección), en el orden en que se agregaron."""
    with _lock:
        return _read()


def clean_name(name: str) -> str:
    """El nombre en minúsculas y con un espacio entre palabras; SitesError si no sirve como nombre."""
    name = " ".join(name.lower().split())
    if not NAME.fullmatch(name):
        raise SitesError("El nombre del atajo va en pocas palabras, sin signos (por ejemplo: facu, aula inglés).")
    return name


def clean_url(url: str) -> str:
    """La dirección lista para guardar: con https:// si no lo tenía. SitesError si el navegador no la abriría igual
    (otra cosa que http(s), de esta compu o la red local, con usuario o con otra dirección adentro)."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    if len(url) > MAX_URL or any(c.isspace() for c in url):
        raise SitesError("Esa dirección no es válida.")
    if problem := reglas.local_problem(url):
        raise SitesError(problem)
    if "." not in reglas.host_of(url):  # «javascript:x» se volvió «https://javascript:x»: eso no es un sitio
        raise SitesError("Esa dirección no es válida.")
    if reglas.has_embedded_url(url):
        raise SitesError("Esa dirección lleva otra adentro: guardá la del sitio al que querés ir.")
    return url


def add(name: str, url: str) -> tuple[str, str]:
    """Guarda un atajo nuevo y lo devuelve (nombre, dirección) ya limpios. Nunca pisa uno que existe (sin importar
    acentos): para cambiarlo, se borra y se agrega de nuevo."""
    name, url = clean_name(name), clean_url(url)
    with _lock:
        sites = _read()
        if any(reglas.fold(old) == reglas.fold(name) for old in sites):
            raise SitesError(f"Ya hay un atajo «{name}»: borralo primero con /sitio borrar {name}.")
        if len(sites) >= MAX_SITES:
            raise SitesError(f"Ya tenés {MAX_SITES} atajos: borrá alguno antes de agregar otro.")
        sites[name] = url
        try:
            _write(sites)
        except OSError:
            raise SitesError("No pude guardar el atajo.") from None
    return name, url


def remove(name: str) -> tuple[str, str] | None:
    """Borra el atajo con ese nombre (sin importar acentos) y lo devuelve; None si no había."""
    wanted = reglas.fold(" ".join(name.lower().split()))
    with _lock:
        sites = _read()
        found = next((old for old in sites if reglas.fold(old) == wanted), None)
        if found is None:
            return None
        url = sites.pop(found)
        try:
            _write(sites)
        except OSError:
            raise SitesError("No pude borrar el atajo.") from None
    return found, url
