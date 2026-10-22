import os
import subprocess
from typing import Callable

from epsilon.settings import config

# Cómo Epsilon te avisa por su cuenta: una notificación de escritorio (`notify-send`) y los canales que se sumen como
# «sumideros» (Telegram se registra al conectarse y se da de baja al apagarse). Este módulo no conoce ningún canal. Un
# aviso que falla en un lado no impide que llegue por el otro: `send` devuelve qué falló, no lanza. `_notify_send` es
# el ÚNICO punto que lanza el proceso (argumentos fijos, sin shell, con tiempo máximo); los tests lo bloquean.

MAX_CHARS = 500
TIMEOUT = 5.0
ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY")

_sinks: dict[str, Callable[[str], None]] = {}


def set_sink(name: str, send: Callable[[str], None]) -> None:
    _sinks[name] = send


def remove_sink(name: str) -> None:
    _sinks.pop(name, None)


def _notify_send(text: str) -> None:
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    # «--» para que un texto que empiece con «-» no se tome por una opción.
    subprocess.run(["notify-send", "-a", "Epsilon", "--", "Epsilon", text], timeout=TIMEOUT, env=env, check=True, capture_output=True)


def send(text: str) -> list[str]:
    """Avisa por todos los medios. Devuelve lo que falló («escritorio: …»); vacío si todo llegó."""
    text = config.redact(" ".join(text.split()))[:MAX_CHARS]
    failed = []
    for name, deliver in (("escritorio", _notify_send), *_sinks.items()):
        try:
            deliver(text)
        except Exception as e:  # un medio caído no frena a los demás
            failed.append(f"{name}: {config.redact(str(e) or type(e).__name__)[:80]}")
    return failed
