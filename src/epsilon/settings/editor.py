"""Abre settings.json (o settings_default.json) en el editor de texto de la PC.

Solo abre uno de esos dos archivos, por su nombre fijo: nunca una ruta que venga de afuera.
`_run` y `_launch` son los ÚNICOS puntos que lanzan procesos (sin shell, argumentos fijos);
los tests los bloquean.
"""

import os
import shutil
import subprocess

from epsilon.settings import generate, store

NO_EDITOR = "Este equipo no tiene un editor de texto para abrir"


def _run(args: list[str]) -> str:
    """Corre un programa corto y devuelve lo que escribió (vacío si falla)."""
    try:
        done = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _launch(args: list[str]) -> None:
    """Lanza un programa sin esperarlo (el editor queda abierto aunque Epsilon se reinicie)."""
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def can_open() -> bool:
    """¿Hay pantalla y con qué abrir? (Epsilon en un servidor sin pantalla, no.)"""
    screen = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    return bool(screen and (shutil.which("xdg-open") or shutil.which("gtk-launch")))


def _path(which: str):
    if which == "settings":
        store.migrate({})  # si no existe, lo crea vacío (con el $schema)
        return store.settings_path()
    if which == "default":
        if not store.default_path().exists():
            generate.write_generated()
        return store.default_path()
    raise ValueError("Ese archivo no se puede abrir")


def open_file(which: str) -> None:
    """Abre `settings` o `default` con el editor de texto predeterminado (si no hay, con xdg-open)."""
    if which not in ("settings", "default"):
        raise ValueError("Ese archivo no se puede abrir")
    if not can_open():
        raise OSError(NO_EDITOR)
    path = str(_path(which))
    # xdg-open abriría un .json con lo que esté asociado a JSON (a veces el navegador):
    # se prefiere el editor de texto plano.
    desktop = _run(["xdg-mime", "query", "default", "text/plain"]).strip()
    if desktop.endswith(".desktop") and shutil.which("gtk-launch"):
        _launch(["gtk-launch", desktop, path])
    elif shutil.which("xdg-open"):
        _launch(["xdg-open", path])
    else:
        raise OSError(NO_EDITOR)
