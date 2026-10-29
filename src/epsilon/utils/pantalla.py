import os
import re
import subprocess
import uuid
from pathlib import Path

from epsilon.utils import ventanas

# Mirar la pantalla: una captura del monitor que tiene el foco, para que la IA la vea (herramienta `captura`; ver
# docs/superpowers/specs/2026-10-29-voice-companion-design.md). La captura puede mostrar cualquier cosa (claves, el banco,
# mensajes): por eso es de UN solo monitor, el archivo vive en una carpeta privada (0700) de la carpeta de ejecución del
# usuario —nunca en /tmp, que otros leen— y los bytes pasan a memoria y el archivo se borra enseguida, pase lo que pase.
# `_grim` es el ÚNICO punto que lanza un proceso (sin shell, argumentos fijos, con tiempo máximo); los tests lo bloquean.

FOLDER = "epsilon-screen"
TIMEOUT = 10.0
QUALITY = "80"
MAX_BYTES = 12 * 1024 * 1024  # un JPEG de una pantalla pesa uno o dos megas: más que esto es otra cosa
ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR")
_MONITOR = re.compile(r"[A-Za-z0-9_.:-]{1,40}")


class PantallaError(Exception):
    """No se pudo capturar la pantalla; el texto va para la persona (y el modelo)."""


def _grim(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Lanza grim: (código de salida, lo que dijo por stderr). El ÚNICO punto que toca el sistema."""
    done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    return done.returncode, done.stderr.strip()


def wayland_display(runtime: Path) -> str | None:
    """El socket de Wayland: la variable si el servicio la heredó y, si no, el primero que haya en la carpeta de ejecución."""
    if display := os.environ.get("WAYLAND_DISPLAY"):
        return display
    sockets = sorted(p.name for p in runtime.glob("wayland-*") if not p.name.endswith(".lock"))
    return sockets[0] if sockets else None


def _focused_monitor() -> str | None:
    """El nombre del monitor con el foco (de `hyprctl -j monitors`), o None si no se sabe: entonces se captura todo."""
    try:
        monitors = ventanas._load("monitors")
    except ventanas.VentanasError:
        return None
    for monitor in monitors if isinstance(monitors, list) else []:
        if isinstance(monitor, dict) and monitor.get("focused") and _MONITOR.fullmatch(str(monitor.get("name", ""))):
            return str(monitor["name"])
    return None


def capture() -> bytes:
    """Una captura JPEG del monitor con el foco, en memoria. PantallaError (en español) si algo sale mal."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_dir():
        raise PantallaError("No encuentro una carpeta privada para la captura (falta XDG_RUNTIME_DIR), así que no la saco.")
    folder = Path(runtime) / FOLDER
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.jpg"
    argv = ["grim", "-t", "jpeg", "-q", QUALITY]
    if monitor := _focused_monitor():
        argv += ["-o", monitor]
    argv.append(str(path))
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    if display := wayland_display(Path(runtime)):
        env["WAYLAND_DISPLAY"] = display
    try:
        try:
            code, _ = _grim(argv, env)
        except FileNotFoundError:
            raise PantallaError("Falta el programa «grim» para mirar la pantalla.") from None
        except subprocess.TimeoutExpired:
            raise PantallaError("La captura tardó demasiado y la corté.") from None
        except OSError:
            raise PantallaError("No pude ejecutar grim para capturar la pantalla.") from None
        if code != 0:
            raise PantallaError("No pude capturar la pantalla (¿hay una sesión gráfica abierta?).")
        try:
            size = path.stat().st_size
            if size > MAX_BYTES:
                raise PantallaError("La captura salió demasiado grande y no la mando.")
            data = path.read_bytes()
        except OSError:
            raise PantallaError("No pude leer la captura.") from None
        if not data:
            raise PantallaError("La captura salió vacía.")
        return data
    finally:
        path.unlink(missing_ok=True)  # los bytes ya están en memoria (o la captura falló): el archivo no se queda
