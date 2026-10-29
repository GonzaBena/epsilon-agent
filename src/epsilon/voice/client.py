import os
from pathlib import Path

# El lado del atajo del Voice Companion: el socket por donde se le habla a Epsilon y el script que Hyprland ejecuta
# al apretar la tecla. Epsilon escribe el script él mismo (en su carpeta de datos), así funciona igual instalado de
# cualquier forma. Es un script de `sh` que llama a `python3 -I` solo con la librería estándar: arranca en
# milisegundos (a diferencia de `uv run epsilon …`) y no depende de `nc` ni `socat`. Solo manda UNA palabra
# (`toggle` por defecto); el servidor entiende un puñado y descarta el resto (channels/voice.py).

FOLDER = "voice"  # dentro de DATA_DIR: el script y el .conf del atajo (la biblioteca no la lista)
SOCKET_NAME = "epsilon-voice.sock"
SCRIPT_NAME = "epsilon-voice-toggle"
ASK_SCRIPT_NAME = "epsilon-voice-ask"  # el mismo script, pero su palabra por defecto es `ask` (abre el campo de texto)


def script_text(default_word: str) -> str:
    """El script del atajo; `default_word` es lo que manda si Hyprland no le pasa otra palabra."""
    return f"""#!/bin/sh
# Lo escribe Epsilon (Voice Companion). Manda una palabra (toggle, cancel, status, ask) al socket de Epsilon.
exec python3 -I -c '
import os, socket, subprocess, sys
runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
path = os.path.join(runtime, "{SOCKET_NAME}")
word = sys.argv[1] if len(sys.argv) > 1 else "{default_word}"
try:
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(3)
        conn.connect(path)
        conn.sendall((word + "\\n").encode())
        print(conn.recv(200).decode().strip())
except OSError:
    subprocess.run(["notify-send", "-a", "Epsilon", "--", "Epsilon", "Epsilon no está corriendo."])
    sys.exit(1)
' "$@"
"""


SCRIPT = script_text("toggle")


def socket_path() -> Path:
    """Dónde escucha Epsilon: en la carpeta de ejecución del usuario (privada, en memoria)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / SOCKET_NAME


def write_script(folder: Path, word: str = "toggle") -> Path:
    """Escribe el script del atajo en `folder` y lo deja ejecutable. Devuelve su ruta. `word` es la palabra por defecto:
    `toggle` (voz) o `ask` (campo de texto), cada una con su propio archivo."""
    if word not in ("toggle", "ask"):
        raise ValueError(f"Palabra desconocida para el script del atajo: {word}")
    name = ASK_SCRIPT_NAME if word == "ask" else SCRIPT_NAME
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    temp = path.with_name(name + ".tmp")
    temp.write_text(script_text(word), encoding="utf-8")
    temp.chmod(0o755)
    temp.replace(path)
    return path
