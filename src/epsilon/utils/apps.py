import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from epsilon.utils.navegador_reglas import fold

# Las apps que la IA puede abrir: los alias que la persona escribió (ajuste PC_APPS, «navegador=omarchy-launch-browser») y
# las instaladas (los archivos .desktop). La IA solo dice un NOMBRE: acá se busca en esas dos listas cerradas y el código
# arma el comando; nunca se ejecuta texto del modelo. Solo lee archivos (no lanza nada): lanzar es de utils/hyprland.py.

MIN_QUERY = 2
MAX_OPTIONS = 5
# Lo que puede tener la ruta de un .desktop o el comando de un alias: nada que signifique algo para un shell, ni comillas
# dobles ni barra invertida (el comando viaja dentro de un texto Lua entre comillas dobles).
_PATH = re.compile(r"[A-Za-z0-9 _./@+:-]+")
_ALIAS_COMMAND = re.compile(r"[A-Za-z0-9 _./~@%+:='-]+")  # sin coma: la lista del ajuste se separa por comas
_ALIAS_NAME = re.compile(r"[a-z0-9 ._-]{2,30}")


@dataclass(frozen=True)
class App:
    id: str  # el nombre del archivo sin .desktop
    name: str
    path: Path
    hints: str  # nombre genérico, comentario y palabras clave: para encontrarla por lo que hace


@dataclass
class Match:
    kind: str  # «alias», «app», «many» (hay varias igual de buenas) o «none»
    name: str = ""
    command: str = ""  # lo que se ejecuta (solo en «alias» y «app»)
    options: list[str] = field(default_factory=list)  # los nombres entre los que elegir (solo en «many»)


def data_dirs() -> list[Path]:
    """Dónde buscar .desktop, en orden de prioridad: la carpeta de la persona primero."""
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(home) / "applications", *(Path(part) / "applications" for part in system.split(":") if part)]


def _entry(path: Path) -> dict[str, str] | None:
    """Las claves de la sección [Desktop Entry] de un .desktop, o None si no se puede leer."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    values: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            if inside:
                break  # terminó la sección que nos importa
            inside = line == "[Desktop Entry]"
        elif inside and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())  # las claves traducidas («Name[es]») tienen otro nombre: se ignoran
    return values


def installed(dirs: list[Path] | None = None) -> list[App]:
    """Las apps visibles instaladas. Si una id está en dos carpetas, vale la primera (la de la persona pisa a la del sistema)."""
    found: dict[str, App] = {}
    for folder in dirs if dirs is not None else data_dirs():
        try:
            files = sorted(folder.glob("*.desktop"))
        except OSError:
            continue
        for path in files:
            if path.stem in found:
                continue
            entry = _entry(path)
            if not entry or entry.get("Type") != "Application" or not entry.get("Exec") or not entry.get("Name"):
                continue
            if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
                continue
            hints = " ".join([entry.get("GenericName", ""), entry.get("Comment", ""), entry.get("Keywords", "").replace(";", " ")])
            found[path.stem] = App(path.stem, entry["Name"], path, hints)
    return list(found.values())


def parse_aliases(entries: list[str]) -> dict[str, str]:
    """«navegador=omarchy-launch-browser» -> {"navegador": "omarchy-launch-browser"}. Lo que no sirve se salta: un alias sin
    comando, con un nombre raro o con un comando que tenga algo más que palabras y rutas (pipes, comillas dobles, $…)."""
    found: dict[str, str] = {}
    for item in entries:
        name, _, command = str(item).partition("=")
        name, command = _spaces(fold(name)), command.strip()
        if _ALIAS_NAME.fullmatch(name) and _ALIAS_COMMAND.fullmatch(command):
            found[name] = command
    return found


def _spaces(text: str) -> str:
    return " ".join(text.split())


def find(query: str, aliases: dict[str, str], found: list[App]) -> Match:
    """Busca por nombre. Primero los alias (exactos), después las apps, de lo más exacto a lo más suelto; si lo mejor lo
    cumplen varias apps con nombres distintos, no se adivina: se devuelven para que la persona elija."""
    wanted = _spaces(fold(query))
    if len(wanted) < MIN_QUERY:
        return Match("none")
    if wanted in aliases:
        return Match("alias", name=wanted, command=aliases[wanted])
    ranked: dict[int, list[App]] = {}
    for app in found:
        tier = _tier(wanted, app)
        if tier:
            ranked.setdefault(tier, []).append(app)
    if not ranked:
        return Match("none")
    best = ranked[min(ranked)]
    distinct = list({fold(app.name): app for app in reversed(best)}.values())[::-1]  # mismo nombre = la misma app: gana la primera
    if len(distinct) == 1:
        return Match("app", name=distinct[0].name, command=launch_command(distinct[0]))
    return Match("many", options=[app.name for app in distinct[:MAX_OPTIONS]])


def _tier(wanted: str, app: App) -> int:
    """Qué tan bien calza (1 es lo mejor); 0 si no calza."""
    name, ident = _spaces(fold(app.name)), fold(app.id)
    last = ident.rsplit(".", 1)[-1]
    if wanted in (name, ident, last):
        return 1
    if name.startswith(wanted):
        return 2
    if wanted in re.findall(r"\w+", name):
        return 3
    if wanted in name or wanted in ident:
        return 4
    if wanted in fold(app.hints):
        return 5
    return 0


def launch_command(app: App) -> str:
    """El comando que abre una app instalada: `gio launch` con la ruta de su .desktop (que sabe interpretar el Exec)."""
    path = str(app.path)
    if not _PATH.fullmatch(path):
        raise ValueError("La ruta de esa app tiene caracteres que no acepto para abrirla.")
    return f"gio launch '{path}'"


def sample(found: list[App], aliases: dict[str, str], count: int = 12) -> list[str]:
    """Unos nombres para decirle al modelo qué puede abrir: primero los alias de la persona."""
    names = [*sorted(aliases), *sorted({app.name for app in found}, key=fold)]
    return list(dict.fromkeys(names))[:count]
