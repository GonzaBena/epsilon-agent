from dataclasses import asdict
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from epsilon import core

# La web es el canal por defecto de Epsilon y es de una sola persona: escucha
# solo en 127.0.0.1 y no tiene login. Como no hay login, cualquier página que
# tengas abierta en el navegador podría intentar hablarle a localhost; por eso
# guard() valida Host y Origin en cada request.
#
# Este archivo solo TRADUCE entre HTTP y el núcleo (core.py): no decide nada.

# Una sola sesión: recargar la página no pierde un «Guardar» pendiente.
SESSION_ID = "web"

# Mismo tope que Telegram, para que ambos canales acepten lo mismo.
MAX_UPLOAD = 20 * 1024 * 1024

# Únicos nombres con los que se acepta que alguien llame a la web.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}

# La web solo escucha en esta dirección y NO es configurable a propósito: no
# tiene login, así que no se expone a la red (para el celular se usa Telegram).
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# La página: index.html, style.css y app.js, cada uno en su archivo.
STATIC_DIR = Path(__file__).parent / "web_static"

# Política de contenido: solo recursos de esta misma web, sin scripts ni
# estilos en línea, y la página no puede mostrarse dentro de otra.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


# --- Defensas ----------------------------------------------------------------


def hostname(host_header: str) -> str:
    """'localhost:8765' -> 'localhost'; '[::1]:8765' -> '[::1]'."""
    if host_header.startswith("["):  # IPv6 entre corchetes
        return host_header.partition("]")[0] + "]"
    return host_header.partition(":")[0]


def is_allowed(host: str, origin: str | None) -> bool:
    """¿Este request viene de la propia web, en esta misma máquina?

    - Host: tiene que ser un nombre local. Frena el DNS rebinding (un dominio
      ajeno que apunta a 127.0.0.1).
    - Origin: si el navegador lo manda, tiene que ser EXACTAMENTE esta web
      (mismo host y mismo puerto). Frena a las páginas ajenas, incluso las
      de otro puerto de localhost. Sin Origin (curl, la propia navegación)
      se acepta."""
    if hostname(host) not in LOCAL_HOSTS:
        return False
    return origin is None or origin == f"http://{host}"


# --- Formato de la API -------------------------------------------------------


class MessageIn(BaseModel):
    text: str = Field("", max_length=10_000)
    action: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def needs_something(self):
        if not self.text.strip() and not self.action:
            raise ValueError("Falta el texto o la acción")
        return self


def to_json(reply: core.Reply) -> dict:
    return asdict(reply)  # {"text": ..., "actions": [{"id": ..., "label": ...}]}


# --- La app ------------------------------------------------------------------


def create_app() -> FastAPI:
    # Sin /docs ni /openapi.json: no hay nada que explorar desde afuera.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if is_allowed(host, request.headers.get("origin")):
            response = await call_next(request)
        else:
            response = JSONResponse({"detail": "No permitido"}, status_code=403)

        # Segunda barrera para la regla «el contenido es dato»: el navegador
        # solo ejecuta scripts y estilos de esta misma web, nunca en línea.
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Las páginas no se cachean: tras actualizar Epsilon no queda un app.js viejo.
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # Los endpoints son "def" (no "async def") a propósito: FastAPI los corre
    # en un hilo aparte, así core.handle() puede esperar a Gemini sin trabar
    # a los demás requests.

    @app.post("/api/message")
    def message(body: MessageIn):
        msg = core.Message(text=body.text, action=body.action, session_id=SESSION_ID)
        return to_json(core.handle(msg))

    @app.post("/api/upload")
    def upload(file: UploadFile = File(...), text: str = Form("", max_length=10_000)):
        mime = file.content_type or ""
        # Mismos tipos que acepta Telegram: imágenes y PDFs.
        if not (mime.startswith("image/") or mime == "application/pdf"):
            raise HTTPException(415, "Solo imágenes y PDFs")

        # Leemos un byte de más para saber si se pasó del tope sin cargar todo.
        data = file.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Ese archivo pesa más de 20 MB")

        attachment = core.Attachment(
            data=data, name=file.filename or "archivo", mime=mime
        )
        msg = core.Message(text=text, attachment=attachment, session_id=SESSION_ID)
        return to_json(core.handle(msg))

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    # Se monta al final: las rutas de /api tienen prioridad. StaticFiles ya
    # impide salirse de la carpeta (../).
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


# --- Arranque ----------------------------------------------------------------


def read_port(env) -> int:
    """Puerto de la web (WEB_PORT, por defecto 8765). Lanza ValueError si no sirve."""
    value = env.get("WEB_PORT", str(DEFAULT_PORT))
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError("WEB_PORT tiene que ser un número entre 1 y 65535")
    return int(value)


def is_configured(env) -> bool:
    """La web es el canal por defecto: no necesita ninguna clave, siempre arranca."""
    return True


def run():
    """Levanta el servidor y se queda esperando (bloquea hasta que lo cortan)."""
    import uvicorn  # acá adentro: importarlo es lento y solo hace falta al arrancar

    try:
        port = read_port(os.environ)
    except ValueError as e:
        raise SystemExit(str(e))

    print(f"Web en marcha: http://{HOST}:{port}")
    # uvicorn maneja solo Ctrl+C y la señal de systemd (corre en el hilo principal).
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    run()
