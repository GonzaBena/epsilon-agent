"""settings.json: el ÚNICO módulo que lo lee y lo escribe, y que guarda en memoria lo aplicado.

Dos vistas de cada ajuste: lo que dice hoy el archivo (`saved`) y lo que el proceso usa
(`applied`). Al arrancar son iguales; después, al cambiar el archivo, solo se aplican al
instante los ajustes `live` y, de los que aflojan una defensa (`loosens`), solo los cambios que
la endurecen: lo demás espera al reinicio. Un archivo roto nunca rompe nada: al arrancar rigen
los valores de fábrica y en pleno uso los últimos buenos. Los valores secretos nunca se leen
de acá ni aparecen en un aviso. Las claves son las internas («USER_NAME»), los valores el texto
que entienden los validadores («si», «6»).
"""

from dataclasses import dataclass
import difflib
import logging
import os
from pathlib import Path
import tempfile
import threading

from dotenv import dotenv_values

from epsilon.data import notes
from epsilon.settings import config, jsonc
from epsilon.settings.config import ConfigError, Setting

SETTINGS_FILE = "settings.json"
DEFAULT_FILE = "settings_default.json"
SCHEMA_FILE = "settings.schema.json"
SCHEMA_KEY = "$schema"  # reservada: la pone Epsilon para el autocompletado

SECRET_WARNING = "las claves van en la pantalla de Configuración, no en este archivo"
BOOTSTRAP_WARNING = "Este ajuste va en el .env: se lee antes que este archivo."
UNKNOWN = "No existe este ajuste."
REPEATED = "Está repetido: vale el último."
NOT_MIGRATED = "El valor del .env no sirve y no se pasó a este archivo"
MIGRATED_HEADER = "// Tus ajustes. Lo que no está acá usa el valor de fábrica (mirá settings_default.json)."

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Issue:
    """Un aviso sobre una clave del archivo. Nunca lleva el valor (podría ser secreto)."""

    name: str
    message: str


# --- Estado en memoria (todo bajo _lock) --------------------------------------

_lock = threading.Lock()
_saved: dict[str, str] = {}  # lo que dice el archivo (el último que se pudo leer)
_applied: dict[str, str] = {}  # lo que usa el proceso
_issues: list[Issue] = []
_broken: str | None = None  # motivo, con número de línea
_stamp: tuple[int, int] | None = None  # (mtime_ns, tamaño) de la última lectura; None = no existía
_migrate_issues: list[Issue] = []  # lo del .env que no se pudo pasar (sobrevive a las relecturas)


# --- Rutas ---------------------------------------------------------------------


def _path(name: str) -> Path:
    """Siempre por safe_path y calculada en cada uso (los tests cambian DATA_DIR)."""
    return notes.safe_path(name)


def settings_path() -> Path:
    return _path(SETTINGS_FILE)


def default_path() -> Path:
    return _path(DEFAULT_FILE)


def schema_path() -> Path:
    return _path(SCHEMA_FILE)


# --- Lectura -------------------------------------------------------------------


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _by_name() -> dict[str, Setting]:
    """Cómo se reconoce cada clave del archivo: por su nombre JSON y, los secretos y los de
    arranque, también por su clave del .env (alguien podría escribirlos así)."""
    found = {}
    for s in config.all_settings():
        if s.secret or s.bootstrap:
            found[s.key] = s
        if s.name:
            found[s.name] = s
    return found


def _parse(text: str) -> tuple[dict[str, str], list[Issue]]:
    """El texto como {clave interna: valor validado} más los avisos. Lanza si no se puede leer."""
    data = jsonc.loads(text)
    known = _by_name()
    names = sorted(name for name, s in known.items() if s.name == name)
    values: dict[str, str] = {}
    issues = [Issue(name, REPEATED) for name in jsonc.duplicates(text)]
    for name, raw in data.items():
        if name == SCHEMA_KEY:
            continue
        s = known.get(name)
        if s is None:
            close = difflib.get_close_matches(name, names, n=1, cutoff=0.8)
            hint = f" ¿Quisiste decir «{close[0]}»?" if close else ""
            issues.append(Issue(name, UNKNOWN + hint))
        elif s.secret:
            issues.append(Issue(name, SECRET_WARNING))
        elif s.bootstrap:
            issues.append(Issue(name, BOOTSTRAP_WARNING))
        else:
            try:
                value = config.from_json(s, raw)
            except Exception as e:  # un validador que se queja: rige el de fábrica
                issues.append(Issue(name, str(e) or "El valor no sirve."))
                continue
            if value:  # vacío = no está definido
                values[s.key] = value
    return values, issues


def _read() -> tuple[str | None, tuple[int, int] | None]:
    """El texto del archivo (None si no existe) y su sello."""
    path = settings_path()
    stamp = _file_stamp(path)
    if stamp is None:
        return None, None
    try:
        return path.read_text(encoding="utf-8"), stamp
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError):
        return "", stamp  # ilegible: cuenta como roto


def _broken_reason(e: Exception, starting: bool) -> tuple[str, int]:
    line = getattr(e, "line", None) or 1
    fallback = "los valores de fábrica" if starting else "los últimos valores que funcionaban"
    return f"El archivo tiene un error en la línea {line}: se usan {fallback}.", line


def _effective(s: Setting | None, value: str | None) -> str:
    if value is not None:
        return value
    return s.default if s is not None else ""


def _load(starting: bool) -> bool:
    """Lee el archivo y actualiza el estado. Sin candado (lo toma quien llama). Nunca lanza.
    Devuelve si cambió algo."""
    global _saved, _applied, _issues, _broken, _stamp
    before = (dict(_saved), dict(_applied), list(_issues), _broken)
    text, _stamp = _read()
    if text is None:
        values, issues, broken = {}, [], None
    else:
        try:
            values, issues = _parse(text)
            broken = None
        except Exception as e:  # JsoncError, pero también RecursionError u otros con texto raro
            broken, _ = _broken_reason(e, starting)
            values, issues = None, None
    _broken = broken
    if values is None:  # roto: al arrancar, fábrica; en pleno uso, lo último bueno
        if starting:
            _saved, _applied, _issues = {}, {}, []
    elif starting:
        _saved, _applied, _issues = values, dict(values), issues
    else:
        known = {s.key: s for s in config.all_settings()}
        applied = dict(_applied)
        for key in set(_applied) | set(values):
            old, new = _applied.get(key), values.get(key)
            if old == new:
                continue
            s = known.get(key)
            if s is None:
                continue
            if s.loosens:
                apply = tightens(s, _effective(s, old), _effective(s, new))
            else:
                apply = s.live
            if apply:
                if new is None:
                    applied.pop(key, None)
                else:
                    applied[key] = new
        _saved, _applied, _issues = values, applied, issues
    return before != (_saved, _applied, _issues, _broken)


def load_initial() -> None:
    """Al arrancar: lee settings.json y aplica todo (también lo que pide reinicio)."""
    with _lock:
        _load(starting=True)


def refresh() -> bool:
    """Si el archivo cambió (mtime o tamaño), lo relee y aplica lo que se puede en caliente."""
    with _lock:
        if _file_stamp(settings_path()) == _stamp:
            return False
        return _load(starting=False)


# --- Consultas -----------------------------------------------------------------


def applied(key: str) -> str | None:
    """El valor que usa el proceso desde el archivo (None = no está en el archivo)."""
    with _lock:
        return _applied.get(key)


def saved(key: str) -> str | None:
    """Lo que dice hoy el archivo, aplicado o no."""
    with _lock:
        return _saved.get(key)


def issues() -> list[Issue]:
    with _lock:
        return list(_issues) + list(_migrate_issues)


def broken() -> str | None:
    with _lock:
        return _broken


# --- Endurecer o aflojar -------------------------------------------------------


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _sites(text: str) -> set[str]:
    return {x.strip().lower() for x in text.split(",") if x.strip()}


def tightens(s: Setting, old: str, new: str) -> bool:
    """Si pasar de `old` a `new` va hacia el lado seguro del ajuste (`safer`). Pura."""
    if s.safer == "lower":
        a, b = _number(old or ""), _number(new or "")
        return a is not None and b is not None and b < a
    if s.safer in ("si", "no"):
        def norm(text: str) -> str:
            return (text or "").strip().lower().replace("sí", "si")
        return norm(new) == s.safer and norm(old) != s.safer
    if s.safer in ("more", "fewer"):
        a, b = _sites(old or ""), _sites(new or "")
        return b > a if s.safer == "more" else b < a
    return False


# --- Escritura -----------------------------------------------------------------


def _writable(key: str) -> Setting:
    s = {x.key: x for x in config.all_settings()}.get(key)
    if s is None or s.secret or s.bootstrap or not s.name:
        raise ConfigError({key: "Este ajuste no se guarda en settings.json."})
    return s


def write(changes: dict[str, str], clear: list[str]) -> None:
    """Cambia en el archivo solo las claves tocadas (valores ya validados, como texto) y saca
    las de `clear`. Relee el texto de disco bajo el candado (así no pisa lo que guardó un editor),
    escribe por archivo temporal y vuelve a leer. Si el archivo está roto, no lo toca."""
    targets = [(_writable(key), text) for key, text in changes.items()]
    removals = [_writable(key) for key in clear]
    global _broken
    with _lock:
        path = settings_path()
        text, _ = _read()
        if text is None:
            text = "{\n}\n"
        try:
            jsonc.loads(text)
        except Exception as e:
            reason, line = _broken_reason(e, starting=False)
            if _broken is None:  # el que ya estaba (p. ej. «de fábrica», al arrancar) sigue valiendo
                _broken = reason
            raise ConfigError(
                {SETTINGS_FILE: f"El archivo tiene un error (línea {line}): arreglalo a mano antes de guardar desde acá."}
            ) from None
        for s, value_text in targets:
            value = config.to_json(s, value_text)
            if value is None or value == "" or value == []:
                text = jsonc.remove(text, s.name)
            else:
                text = jsonc.set_value(text, s.name, value)
        for s in removals:
            text = jsonc.remove(text, s.name)
        _replace(path, text)
        _load(starting=False)  # siempre: el mtime podría no haber cambiado


def _replace(path: Path, text: str) -> None:
    """Escribe por archivo temporal en la misma carpeta y lo pone en su lugar de un saque."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def reset() -> None:
    """Olvida todo lo leído. Solo lo usan los tests."""
    global _saved, _applied, _issues, _broken, _stamp
    with _lock:
        _saved, _applied, _issues = {}, {}, []
        _broken, _stamp = None, None
        _migrate_issues.clear()


# --- Pasar el .env a settings.json (una vez) ----------------------------------------


def _migratable(keys) -> list[Setting]:
    """Los ajustes de `keys` que van en settings.json, en el orden del registro."""
    wanted = set(keys)
    return [s for s in config.all_settings() if s.key in wanted and s.name and not s.secret and not s.bootstrap]


def _add_values(text: str, env_values: dict[str, str], settings: list[Setting], present: set[str]) -> tuple[str, list[str]]:
    """Agrega al texto los valores del .env que validan y no están ya. Sin candado."""
    moved = []
    for s in settings:
        if s.name in present:
            continue
        raw = (env_values.get(s.key) or "").strip()
        if not raw:
            continue  # vacío en el .env: no estaba definido
        try:
            value = config.to_json(s, config.from_json(s, raw))
            config.from_json(s, value)  # lo que va al archivo tiene que leerse igual
        except Exception as e:  # un validador que se queja: queda el de fábrica y se avisa (sin el valor)
            _migrate_issues.append(Issue(s.name, f"{NOT_MIGRATED}: {e}" if str(e) else NOT_MIGRATED + "."))
            continue
        if value is None or value == "" or value == []:
            continue
        text = jsonc.set_value(text, s.name, value)
        moved.append(s.key)
    return text, moved


def migrate(env_values: dict[str, str]) -> list[str]:
    """Si settings.json no existe, lo crea con los ajustes que estaban en el .env (las claves y lo
    de arranque se quedan allá). Devuelve las claves pasadas. Si ya existe, no lo toca."""
    with _lock:
        path = settings_path()
        if path.exists():
            return []
        text = f'{MIGRATED_HEADER}\n{{\n  "{SCHEMA_KEY}": "./settings.schema.json"\n}}\n'
        text, moved = _add_values(text, env_values, _migratable(env_values), set())
        _replace(path, text)
        return moved


def migrate_keys(env_values: dict[str, str], keys) -> list[str]:
    """Agrega a settings.json (lo crea si falta) las claves de `keys` que todavía no tiene, sin
    pisar ninguna, y aplica lo nuevo. Para los ajustes de un plugin que aparece en pleno uso. Si
    el archivo está roto, no lo toca."""
    settings = _migratable(keys)
    if not settings:
        return []
    with _lock:
        path = settings_path()
        text, _ = _read()
        if text is None:
            text = f'{MIGRATED_HEADER}\n{{\n  "{SCHEMA_KEY}": "./settings.schema.json"\n}}\n'
        try:
            present = set(jsonc.loads(text))
        except Exception:
            return []
        text, moved = _add_values(text, env_values, settings, present)
        if moved:
            _replace(path, text)
            _load(starting=False)
        return moved


def leftover_env() -> list[str]:
    """Las claves de ajustes de settings.json que siguen escritas en el .env (solo los nombres,
    nunca los valores): la pantalla las lista para que la persona las borre."""
    path = config.ENV_FILE
    try:
        found = set(dotenv_values(path)) if path.exists() else set()
    except OSError:
        return []
    return [s.key for s in _migratable(found)]


# --- El vigía: relee el archivo cuando cambia --------------------------------------

_watcher: threading.Thread | None = None
_stop_watching = threading.Event()


def _watch(interval: float, stop: threading.Event) -> None:
    while not stop.wait(interval):
        try:
            refresh()
        except Exception as e:  # una pasada que falla no frena al vigía
            log.warning("No pude releer settings.json: %s", config.redact(repr(e)))


def start_watcher(interval: float = 2.0) -> threading.Thread:
    """Un hilo daemon que llama a refresh() cada `interval` segundos."""
    global _watcher, _stop_watching
    stop_watcher()
    _stop_watching = threading.Event()
    _watcher = threading.Thread(target=_watch, args=(interval, _stop_watching), name="ajustes-vigia", daemon=True)
    _watcher.start()
    return _watcher


def stop_watcher(timeout: float = 5) -> None:
    """Frena el vigía (si hay uno) y espera a que termine."""
    global _watcher
    _stop_watching.set()
    if _watcher is not None:
        _watcher.join(timeout)
    _watcher = None
