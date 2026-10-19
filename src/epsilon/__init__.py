import os
from pathlib import Path

from dotenv import load_dotenv

def env_file_path(env) -> Path:
    """Qué archivo de configuración se usa: EPSILON_ENV_FILE si está, y si no, el de al lado del código.

    Sirve para guardar la configuración en otro lugar y para probar sin tocar la real."""
    chosen = env.get("EPSILON_ENV_FILE")
    return Path(chosen).expanduser() if chosen else Path(__file__).parent / ".env"


# Ruta explícita (no relativa a la carpeta actual): así el archivo se encuentra
# aunque se arranque con "python -m" o desde systemd.
ENV_FILE = env_file_path(os.environ)
load_dotenv(ENV_FILE)
