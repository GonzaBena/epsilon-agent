from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
import tomllib
from types import ModuleType

from epsilon.data import notes
from epsilon.settings import config

# Canales de terceros: cada uno es una carpeta en DATA_DIR/plugins/<nombre>/ con un manifiesto
# `plugin.toml` (solo datos) y su código (`channel.py`). Este módulo LEE y VALIDA manifiestos sin
# ejecutar nada del plugin: listar no corre código ajeno. Ver docs/plan-plugins.md.

FOLDER = "plugins"
MANIFEST = "plugin.toml"
DEFAULT_ENTRY = "channel.py"

NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,19}", re.ASCII)
RESERVED_NAMES = ("web", "telegram")
MAX_MANIFEST_BYTES = 16 * 1024
MAX_LABEL = 40
MAX_ABOUT = 200
MAX_SETTINGS = 10
DEFAULT_PATTERN = r"[^\r\n]{1,200}"
DEFAULT_MESSAGE = "Valor no válido."


class PluginError(Exception):
    """Un plugin no se puede usar. El mensaje está listo para mostrar (en español)."""


@dataclass(frozen=True)
class PluginSetting:
    key: str
    label: str
    secret: bool
    pattern: str
    message: str


@dataclass(frozen=True)
class Plugin:
    name: str
    label: str
    about: str
    entry: Path
    folder: Path
    settings: tuple[PluginSetting, ...]


@dataclass(frozen=True)
class Discovery:
    plugins: dict[str, Plugin]
    invalid: dict[str, str]  # carpeta -> motivo


def _text(table: dict, field: str, limit: int, required: bool = True) -> str:
    value = table.get(field, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise PluginError(f"«{field}» tiene que ser un texto" + (" no vacío." if required else "."))
    if len(value) > limit:
        raise PluginError(f"«{field}» es demasiado largo (máximo {limit} caracteres).")
    return value.strip()


def _setting(name: str, table: object) -> PluginSetting:
    if not isinstance(table, dict):
        raise PluginError("Cada ajuste tiene que ser una tabla [[settings]].")
    key = table.get("key", "")
    prefix = f"{name.upper()}_"
    if (
        not isinstance(key, str)
        or not re.fullmatch(rf"{prefix}[A-Z0-9_]+", key, re.ASCII)
        or key == f"{prefix}ENABLED"
    ):
        raise PluginError(f"La clave «{key}» tiene que empezar con {prefix} (y no ser {prefix}ENABLED).")
    if key in {own.key for own in config.SETTINGS}:
        raise PluginError(f"La clave «{key}» ya es un ajuste de Epsilon.")
    pattern = table.get("pattern", DEFAULT_PATTERN)
    try:
        re.compile(pattern)
    except (re.error, TypeError):
        raise PluginError(f"El patrón de «{key}» no es una expresión regular válida.") from None
    secret = table.get("secret", False)
    if not isinstance(secret, bool):
        raise PluginError(f"«secret» de «{key}» tiene que ser true o false.")
    message = table.get("message", DEFAULT_MESSAGE)
    if not isinstance(message, str):
        raise PluginError(f"«message» de «{key}» tiene que ser un texto.")
    return PluginSetting(key, _text(table, "label", MAX_LABEL), secret, pattern, message)


def read_manifest(folder: Path) -> Plugin:
    """Lee y valida el manifiesto de la carpeta de un plugin. Lanza PluginError si no sirve."""
    path = folder / MANIFEST
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise PluginError(f"{MANIFEST} es demasiado grande (máximo {MAX_MANIFEST_BYTES // 1024} KB).")
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise PluginError(f"Falta {MANIFEST}.") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise PluginError(f"No se pudo leer {MANIFEST}: {type(e).__name__}.") from None

    name = data.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise PluginError("«name» tiene que ser minúsculas, números y _ (2 a 20 caracteres, empieza con letra).")
    if name != folder.name:
        raise PluginError(f"«name» ({name}) tiene que ser igual al nombre de la carpeta ({folder.name}).")
    if name in RESERVED_NAMES:
        raise PluginError(f"«{name}» es un canal de Epsilon.")

    entry_name = data.get("entry", DEFAULT_ENTRY)
    if not isinstance(entry_name, str):
        raise PluginError("«entry» tiene que ser un texto.")
    entry = (folder / entry_name).resolve()
    if not entry.is_relative_to(folder.resolve()):
        raise PluginError("«entry» apunta fuera de la carpeta del plugin.")
    if not entry.is_file():
        raise PluginError(f"No existe el archivo «{entry_name}».")

    raw_settings = data.get("settings", [])
    if not isinstance(raw_settings, list) or len(raw_settings) > MAX_SETTINGS:
        raise PluginError(f"Un plugin puede declarar hasta {MAX_SETTINGS} ajustes.")
    settings = tuple(_setting(name, table) for table in raw_settings)
    keys = [s.key for s in settings]
    if len(set(keys)) != len(keys):
        raise PluginError("Hay una clave de ajuste repetida.")

    return Plugin(
        name=name,
        label=_text(data, "label", MAX_LABEL),
        about=_text(data, "about", MAX_ABOUT),
        entry=entry,
        folder=folder,
        settings=settings,
    )


def discover() -> Discovery:
    """Mira DATA_DIR/plugins y devuelve los plugins válidos y los inválidos (con el motivo).

    Solo lee manifiestos: no importa ni ejecuta código de ningún plugin."""
    root = notes.safe_path(FOLDER)
    plugins: dict[str, Plugin] = {}
    invalid: dict[str, str] = {}
    if not root.is_dir():
        return Discovery(plugins, invalid)

    taken: set[str] = set()  # claves de ajustes ya usadas por un plugin anterior
    for folder in sorted(root.iterdir(), key=lambda p: p.name):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        try:
            plugin = read_manifest(folder)
            repeated = taken.intersection(s.key for s in plugin.settings)
            if repeated:
                raise PluginError(f"La clave «{sorted(repeated)[0]}» ya la usa otro plugin.")
        except PluginError as e:
            invalid[folder.name] = str(e)
            continue
        taken.update(s.key for s in plugin.settings)
        plugins[plugin.name] = plugin
    return Discovery(plugins, invalid)


REQUIRED = ("is_configured", "run", "stop")


def load_module(plugin: Plugin) -> ModuleType:
    """Importa el código del plugin (recién ahora se ejecuta). Lanza PluginError si falla o si
    no cumple el contrato. El módulo no queda registrado en sys.modules."""
    spec = importlib.util.spec_from_file_location(f"epsilon_plugin_{plugin.name}", plugin.entry)
    if spec is None or spec.loader is None:
        raise PluginError("No se pudo cargar el código del plugin.")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as e:  # SystemExit: un plugin que corta al importarse
        raise PluginError(config.redact(f"El código del plugin falló al cargarse: {type(e).__name__}: {e}")) from None
    for part in REQUIRED:
        if not callable(getattr(module, part, None)):
            raise PluginError(f"Al plugin le falta «{part}» (un canal necesita {', '.join(REQUIRED)}).")
    return module


def _validator(setting: PluginSetting):
    """validate de un ajuste de plugin: sin espacios de más, sin saltos de línea ni caracteres de
    control, y que cumpla el patrón del manifiesto."""

    def validate(value: str) -> str:
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value) or not re.fullmatch(setting.pattern, value):
            raise ValueError(setting.message)
        return value

    return validate


def settings_for(plugin: Plugin) -> list[config.Setting]:
    """Los ajustes de un plugin como los entiende config: los de su manifiesto (se editan en
    Configuración) más el interruptor <NOMBRE>_ENABLED (en la página Canales, «no» por defecto)."""
    result = [
        config.Setting(s.key, s.label, secret=s.secret, live=True, validate=_validator(s))
        for s in plugin.settings
    ]
    result.append(
        config.Setting(
            f"{plugin.name.upper()}_ENABLED",
            f"Canal de {plugin.label} prendido",
            live=True,
            default="no",
            validate=config._yes_no,
            page="canales",
        )
    )
    return result
