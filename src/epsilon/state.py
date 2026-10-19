import threading

# Estado de cada canal (web, telegram...) para mostrarlo en la configuración y
# en la consola. Lo escriben main y los canales; lo lee la web. Vive en memoria:
# cada arranque empieza de cero.
#
# Estados: starting (conectando), running (activo), skipped (sin configurar),
# stopped (detenido; el detalle explica por qué).

LABELS = {
    "starting": "conectando…",
    "running": "activo",
    "skipped": "sin configurar, se omite",
    "stopped": "detenido",
}

_lock = threading.Lock()  # los canales corren en hilos distintos
_channels: dict[str, dict] = {}


def set(name: str, status: str, detail: str = "") -> None:
    """Anota el estado de un canal y lo muestra en la consola (queda en el journal)."""
    with _lock:
        _channels[name] = {"state": status, "detail": detail}
    line = f"Canal {name}: {LABELS.get(status, status)}"
    print(f"{line}. {detail}" if detail else line)


def snapshot() -> dict[str, dict]:
    """Copia del estado de todos los canales (modificarla no afecta al original)."""
    with _lock:
        return {name: dict(info) for name, info in _channels.items()}


def reset() -> None:
    """Borra todo. Solo lo usan los tests."""
    with _lock:
        _channels.clear()
