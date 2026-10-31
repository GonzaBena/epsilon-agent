import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from epsilon.utils import ventanas
from epsilon.utils.navegador_reglas import fold

# Lo que Epsilon le pide a Hyprland: el atajo del Voice Companion y, para la IA, moverse entre espacios de trabajo, enfocar o
# cerrar una ventana y abrir una app (ver docs/superpowers/specs/2026-10-29-voice-companion-design.md). Epsilon escribe sus PROPIOS archivos
# `hyprland-voice.conf` (config clásica) y `hyprland-voice.lua` (config en Lua, la de Hyprland 0.55+ y la de Omarchy
# nuevo): la persona incluye el que use una sola vez, y además Epsilon aplica el atajo al instante, sin recargar.
# Nunca toca el resto de la config de Hyprland. Todo se valida contra listas cerradas: nada que escriba la persona
# (o un modelo) llega al comando ni al código Lua tal cual.
#
# Dos estilos, porque `hyprctl keyword` NO funciona con la config en Lua (contesta un error pero sale con código 0) y
# `hyprctl eval` no existe en los viejos: se prueba cuál hay (`_style`) y, como `hyprctl` también contesta «ok» o un
# error en el TEXTO, solo se da por bueno un «ok» exacto.
#
# `_ctl` es el ÚNICO punto que lanza un proceso (sin shell, argumentos fijos, con tiempo máximo); los tests lo
# bloquean. Las órdenes a las ventanas son un conjunto CERRADO (ir, mover, enfocar, cerrar, ejecutar): el modelo nunca arma un
# comando de Hyprland. Un número de espacio es un entero de 1 a 10; una ventana se elige entre las que Hyprland lista y se
# enfoca por su dirección exacta; lo que se ejecuta lo arma utils/apps.py (alias de la persona o un .desktop instalado).

TIMEOUT = 5.0
CONF_NAME = "hyprland-voice.conf"
LUA_NAME = "hyprland-voice.lua"
PROBE = "return 1"  # con la config en Lua, `hyprctl eval` lo contesta «ok»
MODIFIERS = ("SUPER", "ALT", "CTRL", "SHIFT")
NAMED_KEYS = ("RETURN", "SPACE", "TAB", "ESCAPE", "GRAVE", "MINUS", "EQUAL")
_KEY = re.compile(r"[A-Z0-9]|F([1-9]|1[0-9]|2[0-4])")
# La ruta del script va dentro de una línea de config y de un texto Lua: solo caracteres que no significan nada ahí
# (sin espacios, comas, comillas, barra invertida ni corchetes).
_COMMAND = re.compile(r"[A-Za-z0-9_./~+@%:=-]+")


class HyprlandError(Exception):
    """No se pudo hablar con Hyprland; el texto va para la persona."""


def normalize_hotkey(text: str) -> str:
    """«alt, x» -> «ALT, X». Un atajo es uno o más modificadores, una coma y una tecla de las permitidas.
    Sin modificador no vale (se comería la tecla al escribir). Lanza ValueError con un mensaje en español."""
    mods_text, comma, key_text = str(text).partition(",")
    mods = mods_text.upper().split()
    key = key_text.strip().upper()
    if not comma or not mods or not key or "," in key or any(c in text for c in "\r\n"):
        raise ValueError("El atajo se escribe «modificador, tecla», por ejemplo «ALT, X».")
    if len(set(mods)) != len(mods) or any(mod not in MODIFIERS for mod in mods):
        raise ValueError(f"Los modificadores posibles son {', '.join(MODIFIERS)} (sin repetir).")
    if not (_KEY.fullmatch(key) or key in NAMED_KEYS):
        raise ValueError(f"La tecla tiene que ser una letra, un número, F1 a F24 o una de: {', '.join(NAMED_KEYS)}.")
    return f"{' '.join(mods)}, {key}"


def _command(command: str) -> str:
    if not _COMMAND.fullmatch(command):
        raise ValueError("La ruta del comando solo puede tener letras, números y . _ / ~ + @ % : = - (sin espacios).")
    return command


def _lua_keys(hotkey: str) -> str:
    """«SUPER SHIFT, Z» -> «SUPER + SHIFT + Z», como las escribe la config en Lua."""
    mods, key = normalize_hotkey(hotkey).split(", ")
    return " + ".join([*mods.split(), key])


def conf_text(hotkey: str, command: str, extra: tuple[str, str] | None = None) -> str:
    """El contenido de hyprland-voice.conf (config clásica): una línea de bind que corre nuestro script (y otra para el
    atajo de texto, `extra` = (atajo, comando), si lo hay)."""
    text = (
        "# Lo escribe Epsilon (Voice Companion): no lo edites, se reescribe al cambiar VOICE_HOTKEY o VOICE_TEXT_HOTKEY.\n"
        "# Incluilo una vez desde tu hyprland.conf con:  source = <esta ruta>\n"
        f"bind = {normalize_hotkey(hotkey)}, exec, {_command(command)}\n"
    )
    if extra:
        text += f"bind = {normalize_hotkey(extra[0])}, exec, {_command(extra[1])}\n"
    return text


def _lua_apply(hotkey: str, command: str, previous: str | None = None) -> str:
    """El código Lua que deja el atajo puesto. Suelta antes el anterior y el mismo atajo, por si ya estaba (así
    cargarlo dos veces nunca lo duplica)."""
    lines = []
    if previous and normalize_hotkey(previous) != normalize_hotkey(hotkey):
        lines.append(f'hl.unbind("{_lua_keys(previous)}")')
    lines.append(f'hl.unbind("{_lua_keys(hotkey)}")')
    lines.append(f'hl.bind("{_lua_keys(hotkey)}", hl.dsp.exec_cmd("{_command(command)}"))')
    return "\n".join(lines)


def lua_text(hotkey: str, command: str, extra: tuple[str, str] | None = None) -> str:
    """El contenido de hyprland-voice.lua (config en Lua); con `extra`, también el atajo de texto."""
    text = (
        "-- Lo escribe Epsilon (Voice Companion): no lo edites, se reescribe al cambiar VOICE_HOTKEY o VOICE_TEXT_HOTKEY.\n"
        "-- Incluilo una vez desde tu hyprland.lua con:  dofile(\"<esta ruta>\")\n" + _lua_apply(hotkey, command) + "\n"
    )
    if extra:
        text += _lua_apply(extra[0], extra[1]) + "\n"
    return text


def write_conf(folder: Path, hotkey: str, command: str, extra: tuple[str, str] | None = None) -> tuple[Path, Path]:
    """Escribe los dos archivos (clásico y Lua) en la carpeta dada, por archivo temporal para no dejar uno a medias.
    Valida todo antes de escribir. `extra` = (atajo, comando) suma el atajo de texto a los mismos archivos. Devuelve (conf, lua)."""
    texts = {CONF_NAME: conf_text(hotkey, command, extra), LUA_NAME: lua_text(hotkey, command, extra)}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in texts.items():
        path = folder / name
        temp = path.with_name(name + ".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
        paths.append(path)
    return paths[0], paths[1]


def _ctl(*args: str) -> tuple[int, str]:
    """`hyprctl <args>`: (código de salida, respuesta sin espacios de más). El ÚNICO punto que toca el sistema."""
    env = {key: os.environ[key] for key in ventanas.ENV_KEYS if key in os.environ}
    if signature := ventanas._instance_signature():
        env["HYPRLAND_INSTANCE_SIGNATURE"] = signature
    try:
        done = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=TIMEOUT, env=env, check=False)
    except FileNotFoundError:
        raise HyprlandError("Falta el programa «hyprctl»: esto solo funciona con Hyprland.") from None
    except (OSError, subprocess.SubprocessError):
        raise HyprlandError("No pude hablar con Hyprland.") from None
    return done.returncode, done.stdout.strip()


def _style() -> str:
    """«lua» si Hyprland usa la config en Lua (acepta `eval`); «legacy» si no."""
    code, answer = _ctl("eval", PROBE)
    return "lua" if code == 0 and answer == "ok" else "legacy"


def _must_be_ok(code: int, answer: str, what: str = "el atajo") -> None:
    if code != 0 or answer != "ok":
        raise HyprlandError(f"Hyprland no aceptó {what} ({answer[:80] or 'sin respuesta'}).")


def apply_hotkey(hotkey: str, command: str, previous: str | None = None) -> str:
    """Aplica el atajo en caliente (sin recargar Hyprland); si había otro, lo suelta antes. Valida todo ANTES de
    tocar nada. Devuelve el atajo ya normalizado."""
    new = normalize_hotkey(hotkey)
    command = _command(command)
    old = normalize_hotkey(previous) if previous else None
    if _style() == "lua":
        _must_be_ok(*_ctl("eval", _lua_apply(new, command, old)))
        return new
    # Clásico: `keyword bind` suma otro bind aunque ya exista (el del archivo con `source`): hay que soltarlo antes.
    if old and old != new:
        _must_be_ok(*_ctl("keyword", "unbind", old))
    _must_be_ok(*_ctl("keyword", "unbind", new))
    _must_be_ok(*_ctl("keyword", "bind", f"{new}, exec, {command}"))
    return new


def unbind_hotkey(hotkey: str) -> None:
    """Suelta un atajo en caliente (el de texto, cuando la persona lo vacía)."""
    hotkey = normalize_hotkey(hotkey)
    if _style() == "lua":
        _must_be_ok(*_ctl("eval", f'hl.unbind("{_lua_keys(hotkey)}")'))
    else:
        _must_be_ok(*_ctl("keyword", "unbind", hotkey))


# --- Órdenes a las ventanas (la IA) ---------------------------------------------------------------------------------------

WORKSPACES = range(1, 11)
_ADDRESS = re.compile(r"0x[0-9a-f]{1,16}")
# El comando va dentro de un texto Lua entre comillas dobles y lo ejecuta un shell: nada que signifique algo ahí.
_EXEC = re.compile(r"[A-Za-z0-9 _./~@%+:=,'-]+")
_GO = re.compile(r"(?:ir\s+(?:al?\s+)?)?(\d{1,2})")
_MOVE = re.compile(r"mover\s+(?:a(?:l)?\s+)?(\d{1,2})")


def parse_workspace(text: str) -> tuple[str, int | str]:
    """«3», «ir al 3» o «anterior» -> ("go", 3 | "previous"); «mover 3» -> ("move", 3). Un conjunto cerrado de frases y
    solo espacios de 1 a 10. ValueError con un mensaje en español si no."""
    wanted = " ".join(fold(str(text)).split())
    if wanted in ("anterior", "previous", "volver"):
        return ("go", "previous")
    for pattern, kind in ((_GO, "go"), (_MOVE, "move")):
        found = pattern.fullmatch(wanted)
        if found and int(found.group(1)) in WORKSPACES:
            return (kind, int(found.group(1)))
    raise ValueError("Decime un espacio de trabajo del 1 al 10 («3»), «anterior» o «mover 3» (manda la ventana activa a ese espacio).")


def _number(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value not in WORKSPACES:
        raise ValueError("Los espacios de trabajo van del 1 al 10.")
    return value


def _dispatch(lua: str, *classic: str) -> None:
    """Una orden a Hyprland: en Lua se manda la expresión; en el clásico, el nombre del dispatcher y su argumento."""
    if _style() == "lua":
        _must_be_ok(*_ctl("dispatch", lua), what="la orden")
    else:
        _must_be_ok(*_ctl("dispatch", *classic), what="la orden")


def go_to_workspace(target: int | str) -> str:
    """Cambia de espacio de trabajo (1 a 10 o «previous») y dice dónde quedó."""
    if target == "previous":
        _dispatch('hl.dsp.focus({ workspace = "previous" })', "workspace", "previous")
    else:
        number = _number(target)
        _dispatch(f'hl.dsp.focus({{ workspace = "{number}" }})', "workspace", str(number))
    try:
        return f"Estás en el espacio {ventanas._load('activeworkspace').get('name', '?')}."
    except ventanas.VentanasError:
        return "Listo."


def move_window(number: int) -> str:
    """Manda la ventana con el foco a otro espacio de trabajo."""
    number = _number(number)
    active = ventanas._load("activewindow")
    if not isinstance(active, dict) or not active.get("address"):
        return "No hay ninguna ventana con el foco."
    _dispatch(f'hl.dsp.window.move({{ workspace = "{number}" }})', "movetoworkspace", str(number))
    return f"Mandé la ventana de {ventanas._app(active)} al espacio {number}."


def focus_window(query: str) -> str:
    """Pasa el foco a una ventana abierta, buscándola por el nombre de su app (o, si no, por su título). Con varias apps igual
    de buenas no adivina: las nombra. De una misma app toma la de uso más reciente. El título no se repite en la respuesta."""
    wanted = " ".join(fold(str(query)).split())
    if len(wanted) < 2:
        return "Decime qué ventana querés enfocar."
    best: dict[int, list[dict]] = {}
    for window in ventanas._load("clients"):
        if not isinstance(window, dict) or not ventanas._visible(window) or not _ADDRESS.fullmatch(str(window.get("address", ""))):
            continue
        app, title = fold(ventanas._app(window)), fold(str(window.get("title", "")))
        tier = 1 if app == wanted else 2 if app.startswith(wanted) else 3 if wanted in app else 4 if wanted in title else 0
        if tier:
            best.setdefault(tier, []).append(window)
    if not best:
        return f"No encontré ninguna ventana abierta que se llame «{ventanas._clean(query)}»."
    found = best[min(best)]
    apps = sorted({ventanas._app(window) for window in found})
    if len(apps) > 1:
        return "Hay varias ventanas que coinciden: " + ", ".join(apps) + ". Decime cuál."
    window = min(found, key=lambda w: w.get("focusHistoryID", 0))
    _dispatch(f'hl.dsp.focus({{ window = "address:{window["address"]}" }})', "focuswindow", f"address:{window['address']}")
    return f"Enfoqué {apps[0]} (espacio {window['workspace']['name']})."


def close_window() -> str:
    """Cierra la ventana con el foco (la aplicación decide si pregunta por lo que no guardaste)."""
    active = ventanas._load("activewindow")
    if not isinstance(active, dict) or not active.get("address"):
        return "No hay ninguna ventana con el foco."
    app = ventanas._app(active)
    _dispatch("hl.dsp.window.close()", "killactive")
    return f"Cerré la ventana de {app}."


def run(command: str) -> None:
    """Hace que Hyprland ejecute un comando (abrir una app). Solo comandos que armó utils/apps.py: se validan igual."""
    if not _EXEC.fullmatch(command):
        raise ValueError("Ese comando tiene caracteres que no acepto.")
    _dispatch(f'hl.dsp.exec_cmd("{command}")', "exec", command)


# --- Devolver el foco ------------------------------------------------------------------------------------------------------
# Una acción que abre una ventana (poner música en Chromium) le roba el foco a la persona y puede mandarla a otro espacio de
# trabajo. `keep_focus` guarda la ventana activa antes y la vuelve a enfocar al terminar; es todo o nada: sin Hyprland, o ante
# cualquier problema, no hace nada y nunca rompe la acción que envuelve.

SETTLE = 0.6  # que la ventana nueva termine de tomar el foco antes de devolverlo (si no, nos pisa)


@dataclass(frozen=True)
class Focus:
    address: str
    workspace: int | None


_depth = 0
_depth_lock = threading.Lock()


def snapshot_focus() -> Focus | None:
    """La ventana con el foco y su espacio de trabajo, o None si no se puede saber (sin Hyprland, sin ventana activa)."""
    try:
        active = ventanas._load("activewindow")
        address = str(active.get("address", "")) if isinstance(active, dict) else ""
        if not _ADDRESS.fullmatch(address):
            return None
        number = active["workspace"].get("id")
        return Focus(address, number if isinstance(number, int) and number in WORKSPACES else None)
    except Exception:
        return None


def restore_focus(snap: Focus | None) -> None:
    """Devuelve el foco a la ventana guardada (Hyprland también te lleva a su espacio). Si ya no existe, vuelve al espacio
    en que estabas. Si ya estás ahí no hace nada; cualquier error se traga."""
    if snap is None:
        return
    try:
        active = ventanas._load("activewindow")
        if isinstance(active, dict) and active.get("address") == snap.address:
            return
        alive = any(isinstance(w, dict) and w.get("address") == snap.address and ventanas._visible(w) for w in ventanas._load("clients"))
        if alive:
            _dispatch(f'hl.dsp.focus({{ window = "address:{snap.address}" }})', "focuswindow", f"address:{snap.address}")
        elif snap.workspace is not None:
            go_to_workspace(snap.workspace)
    except Exception:
        return


CHROMIUM_CLASSES = ("chromium", "chromium-browser")  # la ventana normal del navegador; una web app tiene otra clase («chrome-…__»)


def window_addresses() -> set[str] | None:
    """Las direcciones de las ventanas abiertas ahora, o None si no se puede saber."""
    try:
        return {str(w["address"]) for w in ventanas._load("clients") if isinstance(w, dict) and _ADDRESS.fullmatch(str(w.get("address", "")))}
    except Exception:
        return None


def park_new_windows(before: set[str] | None) -> None:
    """Manda, sin seguirlas, las ventanas de Chromium que aparecieron después de `before` al primer espacio de trabajo
    vacío (1 a 10), para que lo que quedó sonando no ocupe el lugar donde estás. Las web apps no se tocan. Sin lugar
    vacío, o ante cualquier problema, no hace nada."""
    if before is None:
        return
    try:
        visible = [w for w in ventanas._load("clients") if isinstance(w, dict) and ventanas._visible(w) and _ADDRESS.fullmatch(str(w.get("address", "")))]
        mine = [w for w in visible if w["address"] not in before and str(w.get("class", "")).lower() in CHROMIUM_CLASSES]
        if not mine:
            return
        used = {w["workspace"]["id"] for w in visible if w not in mine}
        free = next((n for n in WORKSPACES if n not in used), None)
        if free is None:
            return
        for window in mine:
            address = window["address"]
            _dispatch(f'hl.dsp.window.move({{ workspace = "{free}", follow = false, window = "address:{address}" }})', "movetoworkspacesilent", f"{free},address:{address}")
    except Exception:
        return


@contextmanager
def keep_focus():
    """Al terminar el bloque (salga bien o mal) vuelve a enfocar lo que tenías al empezar. Si hay varios anidados, solo el
    de afuera guarda y devuelve (una sola vez al final de la tarea entera)."""
    global _depth
    with _depth_lock:
        outer = _depth == 0
        _depth += 1
    snap = snapshot_focus() if outer else None
    try:
        yield
    finally:
        with _depth_lock:
            _depth -= 1
        if outer and snap is not None:
            time.sleep(SETTLE)
            restore_focus(snap)
