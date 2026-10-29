import os
import re
import subprocess
from pathlib import Path

from epsilon.utils import ventanas

# Lo que Epsilon le pide a Hyprland. Por ahora una sola cosa: el atajo del Voice Companion (ver
# docs/superpowers/specs/2026-10-29-voice-companion-design.md). Epsilon escribe sus PROPIOS archivos
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
# bloquean. (Las acciones de ventanas del paso 5 se suman acá, con su propio punto único.)

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


def conf_text(hotkey: str, command: str) -> str:
    """El contenido de hyprland-voice.conf (config clásica): una sola línea de bind que corre nuestro script."""
    return (
        "# Lo escribe Epsilon (Voice Companion): no lo edites, se reescribe al cambiar VOICE_HOTKEY.\n"
        "# Incluilo una vez desde tu hyprland.conf con:  source = <esta ruta>\n"
        f"bind = {normalize_hotkey(hotkey)}, exec, {_command(command)}\n"
    )


def _lua_apply(hotkey: str, command: str, previous: str | None = None) -> str:
    """El código Lua que deja el atajo puesto. Suelta antes el anterior y el mismo atajo, por si ya estaba (así
    cargarlo dos veces nunca lo duplica)."""
    lines = []
    if previous and normalize_hotkey(previous) != normalize_hotkey(hotkey):
        lines.append(f'hl.unbind("{_lua_keys(previous)}")')
    lines.append(f'hl.unbind("{_lua_keys(hotkey)}")')
    lines.append(f'hl.bind("{_lua_keys(hotkey)}", hl.dsp.exec_cmd("{_command(command)}"))')
    return "\n".join(lines)


def lua_text(hotkey: str, command: str) -> str:
    """El contenido de hyprland-voice.lua (config en Lua)."""
    return (
        "-- Lo escribe Epsilon (Voice Companion): no lo edites, se reescribe al cambiar VOICE_HOTKEY.\n"
        "-- Incluilo una vez desde tu hyprland.lua con:  dofile(\"<esta ruta>\")\n" + _lua_apply(hotkey, command) + "\n"
    )


def write_conf(folder: Path, hotkey: str, command: str) -> tuple[Path, Path]:
    """Escribe los dos archivos (clásico y Lua) en la carpeta dada, por archivo temporal para no dejar uno a medias.
    Valida todo antes de escribir. Devuelve (conf, lua)."""
    texts = {CONF_NAME: conf_text(hotkey, command), LUA_NAME: lua_text(hotkey, command)}
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


def _must_be_ok(code: int, answer: str) -> None:
    if code != 0 or answer != "ok":
        raise HyprlandError(f"Hyprland no aceptó el atajo ({answer[:80] or 'sin respuesta'}).")


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
