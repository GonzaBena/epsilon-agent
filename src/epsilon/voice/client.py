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

SCRIPT = f"""#!/bin/sh
# Lo escribe Epsilon (Voice Companion). Manda una palabra (toggle, cancel, status) al socket de Epsilon.
exec python3 -I -c '
import os, socket, subprocess, sys
runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
path = os.path.join(runtime, "{SOCKET_NAME}")
word = sys.argv[1] if len(sys.argv) > 1 else "toggle"
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


def socket_path() -> Path:
    """Dónde escucha Epsilon: en la carpeta de ejecución del usuario (privada, en memoria)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / SOCKET_NAME


def write_script(folder: Path) -> Path:
    """Escribe el script del atajo en `folder` y lo deja ejecutable. Devuelve su ruta."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SCRIPT_NAME
    temp = path.with_name(SCRIPT_NAME + ".tmp")
    temp.write_text(SCRIPT, encoding="utf-8")
    temp.chmod(0o755)
    temp.replace(path)
    return path
