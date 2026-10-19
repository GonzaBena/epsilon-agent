from pathlib import Path

from dotenv import load_dotenv

# Ruta explícita junto al código: así el .env se encuentra aunque el bot se
# arranque con "python -m" o desde systemd, donde la carpeta actual es otra.
load_dotenv(Path(__file__).parent / ".env")
