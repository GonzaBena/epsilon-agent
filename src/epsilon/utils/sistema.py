import os
from pathlib import Path
import shutil
import subprocess

# Estado de la máquina donde corre Epsilon: disco, memoria, tiempo encendido y el servicio. Sin modelo y de solo
# lectura. NO ejecuta comandos que alguien escriba: cada dato sale de una función fija. Casi todo se lee de /proc o
# con la biblioteca estándar; el único proceso que se lanza es `systemctl --user show epsilon` (`_systemctl`, el
# ÚNICO punto que toca el sistema fuera de /proc), con argumentos fijos, sin shell y con tiempo máximo.

SERVICE = "epsilon"
MEMINFO = Path("/proc/meminfo")
UPTIME = Path("/proc/uptime")
LOADAVG = Path("/proc/loadavg")
TIMEOUT = 5.0
MAX_FILES = 200_000  # al medir la carpeta de datos: pasado esto se dice «más de»
ITEMS = ("disco", "ram", "uptime", "servicio")
NOT_AVAILABLE = "no disponible"
# Del entorno del proceso solo lo que `systemctl --user` necesita para hablar con el bus de la sesión.
SYSTEMCTL_ENV = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")


def _size(count: float) -> str:
    """1536 -> «1,5 KB»; con coma decimal, como se escribe en Argentina."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            text = f"{count:.0f}" if unit == "B" else f"{count:.1f}".replace(".", ",")
            return f"{text} {unit}"
        count /= 1024
    return ""  # inalcanzable


def _folder_size(folder: Path) -> str:
    total = files = 0
    for root, _, names in os.walk(folder, followlinks=False):
        for name in names:
            files += 1
            if files > MAX_FILES:
                return f"más de {_size(total)}"
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return _size(total)


def disk(data_dir: Path) -> list[tuple[str, str]]:
    """Espacio del disco donde viven los datos y cuánto ocupan."""
    try:
        usage = shutil.disk_usage(data_dir)
    except OSError:
        return [("Disco", NOT_AVAILABLE)]
    percent = round(usage.used / usage.total * 100) if usage.total else 0
    return [
        ("Disco", f"{_size(usage.free)} libres de {_size(usage.total)} ({percent} % usado)"),
        ("Datos de Epsilon", _folder_size(data_dir)),
    ]


def _meminfo() -> dict[str, int]:
    values = {}
    for line in MEMINFO.read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024  # /proc lo da en KB
    return values


def ram() -> list[tuple[str, str]]:
    try:
        info = _meminfo()
        rows = [("Memoria", f"{_size(info['MemAvailable'])} disponibles de {_size(info['MemTotal'])}")]
        if info.get("SwapTotal"):
            rows.append(("Swap", f"{_size(info['SwapFree'])} libres de {_size(info['SwapTotal'])}"))
    except (OSError, KeyError):
        return [("Memoria", NOT_AVAILABLE)]
    return rows


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{days} d" if days else "", f"{hours} h" if hours or days else "", f"{minutes} min"]
    return " ".join(p for p in parts if p)


def uptime() -> list[tuple[str, str]]:
    try:
        seconds = float(UPTIME.read_text(encoding="utf-8").split()[0])
        load = " · ".join(LOADAVG.read_text(encoding="utf-8").split()[:3])
    except (OSError, ValueError, IndexError):
        return [("Encendido hace", NOT_AVAILABLE)]
    return [("Encendido hace", _duration(seconds)), ("Carga (1, 5, 15 min)", load)]


def _systemctl() -> str:
    """El estado del servicio de Epsilon, tal como lo imprime systemctl (CLAVE=valor por línea)."""
    env = {key: os.environ[key] for key in SYSTEMCTL_ENV if key in os.environ}
    done = subprocess.run(
        ["systemctl", "--user", "show", SERVICE, "-p", "ActiveState,SubState,ActiveEnterTimestamp"],
        capture_output=True, text=True, timeout=TIMEOUT, env=env, check=False,
    )
    return done.stdout


def service() -> list[tuple[str, str]]:
    label = f"Servicio {SERVICE}"
    try:
        output = _systemctl()
    except (OSError, subprocess.SubprocessError):
        return [(label, NOT_AVAILABLE)]
    state = dict(line.partition("=")[::2] for line in output.splitlines())
    if not state.get("ActiveState"):
        return [(label, NOT_AVAILABLE)]
    text = f"{state['ActiveState']} ({state.get('SubState', '?')})"
    if state["ActiveState"] == "active" and state.get("ActiveEnterTimestamp"):
        text += f" desde {state['ActiveEnterTimestamp']}"
    return [(label, text)]


def report(item: str, data_dir: Path) -> str:
    """Los datos pedidos en una tabla markdown. `item` vacío = todo. ValueError si no es una opción."""
    item = item.strip().lower()
    if item and item not in ITEMS:
        raise ValueError(f"No conozco «{item[:30]}». Opciones: {', '.join(ITEMS)}.")
    rows = []
    for name, rows_of in (("disco", lambda: disk(data_dir)), ("ram", ram), ("uptime", uptime), ("servicio", service)):
        if not item or item == name:
            rows += rows_of()
    return "**Sistema**\n\n| Dato | Valor |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
