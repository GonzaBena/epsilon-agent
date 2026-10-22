import json
import os
from pathlib import Path
import re
import subprocess

# Ventanas abiertas y la que tiene el foco, leídas de Hyprland. Sin modelo y de solo lectura: no mueve, cierra ni
# enfoca nada. NO ejecuta comandos que alguien escriba: `_hyprctl` es el ÚNICO punto que lanza un proceso, con
# argumentos fijos (`hyprctl -j clients|activewindow`), sin shell y con tiempo máximo. Los títulos los escribe cada
# aplicación (una página web, un correo): se cortan y son dato, nunca instrucciones.

TIMEOUT = 5.0
MAX_TITLE = 80
ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY")
OPTIONS = ("activa",)


class VentanasError(Exception):
    """No se pudo hablar con Hyprland; el texto va para la persona (o el modelo)."""


def _instance_signature() -> str | None:
    """La firma de la sesión de Hyprland. Cambia en cada inicio de sesión: si el servicio no la heredó, se toma la
    instancia más nueva de la carpeta de ejecución."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return None  # ya está en el entorno
    folder = Path(os.environ.get("XDG_RUNTIME_DIR", "")) / "hypr"
    try:
        found = [p for p in folder.iterdir() if p.is_dir()]
    except OSError:
        return None
    return max(found, key=lambda p: p.stat().st_mtime).name if found else None


def _hyprctl(what: str) -> str:
    """La salida JSON de `hyprctl -j <what>`. El ÚNICO punto que toca el sistema."""
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    if signature := _instance_signature():
        env["HYPRLAND_INSTANCE_SIGNATURE"] = signature
    try:
        done = subprocess.run(["hyprctl", "-j", what], capture_output=True, text=True, timeout=TIMEOUT, env=env, check=False)
    except FileNotFoundError:
        raise VentanasError("Falta el programa «hyprctl»: esto solo funciona con Hyprland.") from None
    except (OSError, subprocess.SubprocessError):
        raise VentanasError("No pude hablar con Hyprland.") from None
    if done.returncode != 0:
        raise VentanasError("Hyprland no respondió.")
    return done.stdout


def _load(what: str):
    try:
        return json.loads(_hyprctl(what))
    except ValueError:
        raise VentanasError("No entendí lo que contestó Hyprland.") from None


def _clean(text: str) -> str:
    """Una línea, corta y sin barras verticales (romperían la tabla)."""
    return " ".join(str(text).split())[:MAX_TITLE].replace("|", "/")


def _app(window: dict) -> str:
    """«chrome-music.youtube.com__-Default» (una app web instalada) -> «music.youtube.com»."""
    name = str(window.get("class") or window.get("initialClass") or "?")
    return _clean(re.sub(r"__-?\w+$", "", name.removeprefix("chrome-")))


def _visible(window: dict) -> bool:
    return bool(window.get("mapped")) and not window.get("hidden")


def report(item: str) -> str:
    """Las ventanas abiertas en una tabla markdown, con la activa marcada. `item` «activa» = solo esa.
    ValueError si no es una opción."""
    item = item.strip().lower()
    if item and item not in OPTIONS:
        raise ValueError(f"No conozco «{item[:30]}». Opciones: {', '.join(OPTIONS)} (sin opción, todas).")
    active = _load("activewindow")
    active_address = active.get("address") if isinstance(active, dict) else None
    if item:
        if not active_address:
            return "No hay ninguna ventana con el foco."
        return f"**Ventana activa**: {_app(active)} — {_clean(active.get('title', '')) or 'sin título'} (espacio {active['workspace']['name']})"
    clients = [w for w in _load("clients") if isinstance(w, dict) and _visible(w)]
    if not clients:
        return "No hay ventanas abiertas."
    clients.sort(key=lambda w: (w["workspace"]["id"], w.get("focusHistoryID", 0)))
    rows = []
    for window in clients:
        mark = " (activa)" if window.get("address") == active_address else ""
        rows.append(f"| {window['workspace']['name']} | {_app(window)}{mark} | {_clean(window.get('title', ''))} |")
    return "**Ventanas**\n\n| Espacio | App | Título |\n|---|---|---|\n" + "\n".join(rows)
