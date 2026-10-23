from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict
import json
import os
import shlex
from pathlib import Path
import socket
from queue import Queue
import tempfile
import threading
import time

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from epsilon import core, state, tools
from epsilon.ai import chat, favorites, llm, permissions, providers, usage
from epsilon.connectors import bridge, buttons as connector_buttons, manager as connector_manager, store as connector_store
from epsilon.data import conversations, library, notes
from epsilon.routines import rules as routine_rules, store as routine_store
from epsilon.settings import checks, config
from epsilon.utils import clock
from epsilon.voice import transcribe

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
MODELS_TIMEOUT = 3.0  # segundos que se espera a cada proveedor para listar sus modelos
# Cuánto se recuerda la lista de modelos de un proveedor (segundos). Los remotos (Gemini) cambian poco y cada
# consulta es una llamada a internet; los locales (Ollama) no se recuerdan: bajar un modelo tiene que verse al toque.
MODELS_TTL = {"gemini": 30 * 60.0}
_models_cache: dict[str, tuple[float, list[str]]] = {}  # proveedor -> (cuándo se pidió, modelos); solo éxitos

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


class CancelIn(BaseModel):
    conversation_id: str = Field(max_length=40)


class MessageIn(BaseModel):
    text: str = Field("", max_length=10_000)
    action: str | None = Field(None, max_length=100)
    # Opcionales: sin conversation_id no se guarda nada (así se puede usar la API con curl).
    conversation_id: str | None = Field(None, max_length=40)
    message_id: str | None = Field(None, max_length=100)  # el mensaje cuyo botón se tocó
    # Solo cuenta en el primer mensaje: el modo de una conversación se elige al crearla.
    mode: str | None = Field(None, pattern="^(normal|ai)$")

    @model_validator(mode="after")
    def needs_something(self):
        if not self.text.strip() and not self.action:
            raise ValueError("Falta el texto o la acción")
        return self


class ConfigIn(BaseModel):
    """Cambios parciales: solo lo que se manda se toca. Valor vacío = no tocar."""

    changes: dict[str, str] = Field(default_factory=dict, max_length=20)
    clear: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def sane(self):
        # Topes de tamaño: ninguna clave ni valor razonable se acerca a esto.
        if any(len(k) > 64 or len(v) > 500 for k, v in self.changes.items()):
            raise ValueError("Valor demasiado largo")
        if any(len(k) > 64 for k in self.clear):
            raise ValueError("Nombre demasiado largo")
        if not self.clear and not any(v.strip() for v in self.changes.values()):
            raise ValueError("No hay nada para guardar")
        return self


class ChannelIn(BaseModel):
    enabled: bool
    confirmed: bool = False  # para prender un plugin: ya aceptó el aviso de que ve tus claves


# Aviso de la primera vez que se prende un plugin (el servidor lo exige; la página lo muestra).
PLUGIN_WARNING = (
    "Este canal corre dentro de Epsilon y puede ver tus claves. "
    "Prendelo solo si confiás en quien lo escribió."
)


class RevealIn(BaseModel):
    path: str = Field(max_length=300)


class LimitIn(BaseModel):
    usd: object  # el número lo valida conversations.set_limit (mensaje claro, sin repetir el valor)


class PermissionsIn(BaseModel):
    states: dict[str, str] = Field(max_length=50)  # id de herramienta -> allow | ask | deny


# permissions.save escribe por un archivo temporal de nombre fijo: un guardado a la vez.
PERMISSIONS_LOCK = threading.Lock()


def permissions_view() -> dict:
    """Lo que la página de permisos dibuja: cada herramienta de la IA con su estado."""
    saved = permissions.load()
    return {
        "states": list(permissions.STATES),
        "tools": [
            {
                "id": tools.ai_id(tool),
                "label": tool.label,
                "desc": tool.ai_help or tool.help or tool.panel or "",
                "kind": tool.ai,
                "state": saved.get(tools.ai_id(tool), permissions.default_state(tool)),  # un conector pudo sumar una recién
                "default": permissions.default_state(tool),
            }
            for tool in tools.ai_tools()
        ],
    }


class RoutineIn(BaseModel):
    text: str = Field(max_length=300)  # «cuándo | qué», la misma sintaxis que /rutina


class RoutineEnabledIn(BaseModel):
    enabled: bool


def routines_view() -> dict:
    """Lo que la página de rutinas dibuja: si el interruptor general está prendido y cada regla. Una regla rota
    (alguien editó routines.json a mano) no tira la página: se omite."""
    shown = []
    for rule in routine_store.rules():
        try:
            shown.append(routine_rules.view(rule))
        except (KeyError, ValueError, TypeError):
            continue
    return {"enabled": config.enabled("ROUTINES_ENABLED"), "rules": shown}


def routines_now():
    """La hora de la pared, sin zona: así se guardan y comparan las rutinas."""
    return clock.now().replace(tzinfo=None)


class ConnectorPreviewIn(BaseModel):
    command: str = Field(max_length=500)


class ConnectorIn(BaseModel):
    name: str = Field(max_length=40)
    command: str = Field(max_length=500)
    env: dict[str, str] = Field(default_factory=dict, max_length=20)
    confirm: bool = False


class ButtonIn(BaseModel):
    label: str = Field(max_length=100)
    connector: str = Field(max_length=40)
    tool: str = Field(max_length=100)
    args: dict = Field(default_factory=dict)


class ConnectorChangeIn(BaseModel):
    enabled: bool | None = None
    autostart: bool | None = None
    env: dict[str, str] | None = Field(default=None, max_length=20)


def split_command(command: str) -> list[str]:
    """El comando como lista (shlex, NUNCA por un shell). ValueError en español si no se entiende."""
    try:
        argv = shlex.split(command)
    except ValueError:
        raise ValueError("No entiendo el comando: revisá las comillas.") from None
    if not argv:
        raise ValueError("Escribí el comando que arranca el conector.")
    return argv


def connectors_view() -> dict:
    """Lo que la página de conectores dibuja. Nunca lleva los valores de `env`: solo si está cargada y su largo."""
    shown = []
    for entry in connector_store.list_all():
        try:
            public = connector_store.public(entry)
            taken: set[str] = set()
            found = []
            for item in public.get("tools", []):
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                action = bridge.action_name(item["name"], taken)
                taken.add(action)
                description = item.get("description") if isinstance(item.get("description"), str) else ""
                # «changed»: sin hash guardado, la herramienta quedó pendiente de que la vuelvas a autorizar.
                found.append({"action": action, "description": config.redact(description)[:200],
                              "changed": not item.get("hash")})
            shown.append({
                "name": entry["name"], "command": shlex.join(entry["argv"]), "enabled": bool(entry.get("enabled")),
                "autostart": bool(entry.get("autostart")), "status": connector_manager.status(entry["name"]),
                "error": config.redact(str(entry.get("error") or "")), "env": public["env"], "tools": found,
            })
        except (KeyError, ValueError, TypeError, connector_manager.ConnectorError):
            continue  # una ficha rota (archivo editado a mano) no tira la página
    return {"connectors": shown}


def connector_start(name: str) -> None:
    """Arranca el conector; si falla, queda guardado «detenido» con el motivo (ya redactado)."""
    try:
        connector_manager.start(name)
    except Exception as error:  # noqa: BLE001 - cualquier falla deja el conector detenido, no tira la página
        reason = config.redact(str(error)) or "No pude arrancarlo."
        try:
            connector_store.update(name, error=reason[:300])
        except connector_store.StoreError:
            pass


class TitleIn(BaseModel):
    title: str = Field(max_length=200)


class FavoritesIn(BaseModel):
    favorites: list[str] = Field(max_length=favorites.MAX_FAVORITES)


class PinIn(BaseModel):
    pinned: bool


def read_image_or_pdf(file: UploadFile) -> tuple[bytes, str]:
    """Los bytes y el tipo de un archivo subido. Mismos tipos que acepta Telegram: imágenes y PDFs, hasta MAX_UPLOAD."""
    mime = file.content_type or ""
    if not (mime.startswith("image/") or mime == "application/pdf"):
        raise HTTPException(415, "Solo imágenes y PDFs")
    # Leemos un byte de más para saber si se pasó del tope sin cargar todo.
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Ese archivo pesa más de 20 MB")
    return data, mime


def conversation_of(raw: str | None) -> str | None:
    """None si no se mandó conversación (no se guarda nada); 422 si el id no es válido."""
    if not raw:
        return None
    if not conversations.is_valid_id(raw):
        raise HTTPException(422, "Id de conversación no válido")
    return raw


def ensure_writable(conversation_id: str | None) -> None:
    """La web solo lee las conversaciones de otros canales (Telegram): escribir ahí mandaría
    mensajes que ese canal nunca vería. Lo vigila el servidor, no solo el botón."""
    if conversation_id and conversations.channel(conversation_id) not in (None, "web"):
        raise HTTPException(409, "Esa conversación es de Telegram: respondé desde Telegram.")


def session_for(conversation_id: str | None) -> str:
    """Cada conversación tiene su propio estado pendiente (por ejemplo, un CSV sin confirmar)."""
    return f"web:{conversation_id}" if conversation_id else SESSION_ID


def mode_json(conversation_id: str) -> dict:
    """El modo de la conversación y, si es de modo IA, contexto / costo / límite para la barra."""
    mode = conversations.mode(conversation_id)
    return {"mode": mode, "chat": chat.status(conversation_id)} if mode == "ai" else {"mode": mode}


def reply_json(reply: core.Reply, conversation_id: str | None) -> dict:
    """La respuesta para el navegador; si hay conversación, también la guarda."""
    data = to_json(reply)
    if conversation_id:
        # El cliente necesita el id del mensaje para decir después qué botón tocó.
        data["message_id"] = conversations.add_message(
            conversation_id, "bot", reply.text, actions=data["actions"], artifacts=data["artifacts"], ai=reply.ai
        )
        data.update(mode_json(conversation_id))
    return data


def to_json(reply: core.Reply) -> dict:
    data = asdict(reply)  # {"text": ..., "actions": [...], "artifacts": [{"kind": ..., "path": ...}]}
    data.pop("ai")  # solo importa al guardar la conversación (ver reply_json)
    data.pop("stacked")  # cómo se acomodan los botones es cosa de cada canal; la web no lo usa
    data.pop("files")  # archivos para ENVIAR: solo Telegram los envía; en la web todo está en la biblioteca
    return data


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
        # La API ni siquiera se guarda en caché (la configuración es sensible).
        is_api = request.url.path.startswith("/api/")
        response.headers["Cache-Control"] = "no-store" if is_api else "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # FastAPI, por defecto, devuelve en el 422 el cuerpo que recibió
        # ("input"): ahí podría volver una clave. Acá solo decimos dónde y qué falló.
        problems = [
            {"where": ".".join(str(p) for p in e["loc"]), "what": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            {"detail": "Pedido no válido", "problems": problems}, status_code=422
        )

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/tools")
    def tools_list():
        # Lo que el panel de herramientas dibuja y los pedidos que la web ejecuta sola
        # desde un enlace (/?cmd=...). Todo sale de tools.py.
        return {
            "tools": [
                {
                    "label": tool.label,
                    "desc": tool.panel,
                    "insert": tool.insert,
                    "action": tool.action,
                    "uses_ai": tool.uses_ai,
                }
                for tool in tools.TOOLS
                if tool.panel
            ],
            # Los comandos que sugiere el cuadro de texto al escribir «/».
            "commands": [
                {
                    "name": tool.name,
                    "usage": tool.usage or tool.label,
                    "desc": tool.help or tool.menu or tool.panel or "",
                }
                for tool in tools.TOOLS
                if tool.name and not tool.channel
            ],
            "link_commands": tools.link_commands(),
        }

    # Los endpoints son "def" (no "async def") a propósito: FastAPI los corre
    # en un hilo aparte, así core.handle() puede esperar a Gemini sin trabar
    # a los demás requests.

    def prepare(body: MessageIn) -> tuple[core.Message, str | None]:
        """Valida, guarda lo que escribiste y arma el mensaje para el núcleo (lo comparten los dos endpoints)."""
        cid = conversation_of(body.conversation_id)
        ensure_writable(cid)
        if cid:
            conversations.start(cid, body.mode or "normal", chat.default_limit())
        if cid and body.action:
            # Un botón: se anota en QUÉ mensaje se tocó. No es un mensaje tuyo.
            if body.message_id:
                conversations.add_action(cid, body.message_id, body.action)
        elif cid:
            conversations.add_message(cid, "user", body.text)
        return core.Message(text=body.text, action=body.action, session_id=session_for(cid), conversation_id=cid), cid

    @app.post("/api/message")
    def message(body: MessageIn):
        msg, cid = prepare(body)
        return reply_json(core.handle(msg), cid)

    @app.post("/api/message/stream")
    def message_stream(body: MessageIn):
        """Lo mismo que /api/message, pero mientras la IA trabaja manda el progreso (qué paso, qué herramienta).
        Una línea JSON por evento; la última es {"type": "reply", "data": ...} (o {"type": "error"}). Los errores
        de validación salen como siempre, antes de empezar a transmitir."""
        msg, cid = prepare(body)
        events: Queue = Queue()

        def work():
            # La respuesta se arma y se guarda acá, no en el generador: si el navegador corta, no se pierde.
            try:
                events.put({"type": "reply", "data": reply_json(core.handle(msg, on_event=events.put), cid)})
            except Exception as e:
                print(f"Error en el mensaje: {config.redact(repr(e))}")  # el detalle, a la terminal
                events.put({"type": "error", "detail": "No pude responder."})
            events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def lines():
            while (event := events.get()) is not None:
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.post("/api/cancel")
    def cancel_message(body: CancelIn):
        """El cuadradito del chat: frena el turno de IA que está corriendo en esa conversación (ver core.cancel).
        La respuesta del turno ("Cancelé lo que estaba haciendo.") se guarda sola en el hilo."""
        cid = conversation_of(body.conversation_id)
        return {"cancelled": bool(cid and core.cancel(cid))}

    @app.post("/api/upload")
    def upload(
        file: UploadFile = File(...),
        text: str = Form("", max_length=10_000),
        conversation_id: str = Form("", max_length=40),
        mode: str = Form("", pattern="^(normal|ai)?$"),
    ):
        cid = conversation_of(conversation_id)
        ensure_writable(cid)
        data, mime = read_image_or_pdf(file)
        name = file.filename or "archivo"
        if cid:
            conversations.start(cid, mode or "normal", chat.default_limit())
            # Lo que se ve en el chat: el nombre del archivo y, si hay, tu pedido.
            conversations.add_message(cid, "user", f"{name}\n{text}" if text.strip() else name)

        attachment = core.Attachment(data=data, name=name, mime=mime)
        msg = core.Message(text=text, attachment=attachment, session_id=session_for(cid), conversation_id=cid)
        return reply_json(core.handle(msg), cid)

    @app.post("/api/files")
    def save_file(file: UploadFile = File(...)):
        """Sube un archivo a la biblioteca SIN llamar al modelo: el chat con IA lo menciona después con «@»."""
        data, _ = read_image_or_pdf(file)
        path = notes.save_file(file.filename or "archivo", data)
        return {"path": path.relative_to(notes.DATA_DIR).as_posix()}

    @app.post("/api/transcribe")
    def transcribe_audio(file: UploadFile = File(...)):
        """Voz -> texto para el cuadro de escritura. NO ejecuta nada: el navegador deja el
        texto en el cuadro y vos decidís si lo mandás. El audio es dato: no se guarda."""
        if not (file.content_type or "").startswith("audio/"):
            raise HTTPException(415, "Solo audio")
        data = file.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Ese audio pesa más de 20 MB")

        path = None
        try:
            # Archivo temporal FUERA de DATA_DIR (Whisper lo lee por ruta); se borra siempre.
            # El nombre lo elige el sistema: nada del cliente forma parte de la ruta.
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp:
                temp.write(data)
                path = Path(temp.name)
            return {"text": transcribe.audio_to_text(path)}
        except transcribe.VoiceError as e:
            raise HTTPException(422, str(e)) from None  # ya viene redactado para el usuario
        except Exception as e:
            print(f"Error con un audio: {config.redact(repr(e))}")  # el detalle, a la terminal
            raise HTTPException(500, "No pude procesar el audio.") from None
        finally:
            if path:
                path.unlink(missing_ok=True)

    # --- Conversaciones guardadas ---------------------------------------------
    # Un id que no tiene la forma exacta (o que no existe) es siempre un 404:
    # nunca se arma una ruta con lo que llega en la URL.

    @app.get("/api/conversations")
    def list_conversations(limit: int = Query(50, ge=1, le=200)):
        # limit: cuántas «Recientes» devolver (la web pide más con «Ver más»; las ancladas y las de canal
        # salen siempre); total: cuántas hay en realidad.
        return {"conversations": conversations.list_all(limit), "total": conversations.count()}

    @app.post("/api/conversations")
    def create_conversation():
        # Solo reserva un id: el archivo nace con el primer mensaje, así
        # «Nuevo chat» no deja archivos vacíos.
        return {"id": conversations.new_id()}

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        found = conversations.messages(conversation_id) if conversations.is_valid_id(conversation_id) else None
        if found is None:
            raise HTTPException(404, "No existe esa conversación")
        return {
            "id": conversation_id,
            "title": conversations.title(conversation_id),
            "channel": conversations.channel(conversation_id),  # «web» o «telegram» (solo lectura)
            "messages": found,
            "artifacts": conversations.artifacts(conversation_id),  # lo guardado, para «Contexto»
            **mode_json(conversation_id),  # «normal» o «ai» (con contexto, costo y límite)
        }

    @app.get("/api/permissions")
    def get_permissions():
        return permissions_view()

    @app.put("/api/permissions")
    def put_permissions(body: PermissionsIn):
        """Guarda el estado de todas las herramientas; si algo no cierra, no escribe nada."""
        try:
            with PERMISSIONS_LOCK:
                permissions.save(body.states)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return permissions_view()

    @app.get("/api/routines")
    def get_routines():
        return routines_view()

    @app.post("/api/routines")
    def create_routine(body: RoutineIn):
        """Crea una rutina con la misma sintaxis y validación que /rutina; si no se entiende, 422 con el motivo."""
        try:
            routine_store.add(routine_rules.build(body.text.strip(), routines_now()))
        except (ValueError, routine_store.StoreError) as e:
            raise HTTPException(422, str(e)) from None
        return routines_view()

    @app.put("/api/routines/{rule_id}")
    def pause_or_resume_routine(rule_id: int, body: RoutineEnabledIn):
        try:
            found = routine_store.set_enabled(rule_id, body.enabled, routines_now())
        except routine_store.StoreError as e:  # una «sola vez» que ya pasó se borra al reanudarla
            raise HTTPException(422, str(e)) from None
        if found is None:
            raise HTTPException(404, "No existe esa rutina")
        return routines_view()

    @app.delete("/api/routines/{rule_id}")
    def delete_routine(rule_id: int):
        if not routine_store.remove(rule_id):
            raise HTTPException(404, "No existe esa rutina")
        return routines_view()

    @app.get("/api/connectors")
    def get_connectors():
        return connectors_view()

    @app.post("/api/connectors/preview")
    def preview_connector(body: ConnectorPreviewIn):
        """Cómo se entiende el comando, sin correr nada."""
        try:
            return {"argv": split_command(body.command)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/api/connectors")
    def create_connector(body: ConnectorIn):
        """Guarda y arranca un conector. Exige la confirmación explícita: corre un programa de la PC."""
        if body.confirm is not True:
            raise HTTPException(422, "Confirmá que querés correr este programa.")
        try:
            argv = split_command(body.command)
            connector_store.add(body.name, argv, body.env)
        except (ValueError, connector_store.StoreError) as e:
            raise HTTPException(422, str(e)) from None
        connector_start(body.name)
        return connectors_view()

    @app.put("/api/connectors/{name}")
    def change_connector(name: str, body: ConnectorChangeIn):
        fields = {}
        if body.autostart is not None:
            fields["autostart"] = body.autostart
        if body.enabled is not None:
            fields["enabled"] = body.enabled
        try:
            if connector_store.get(name) is None:
                raise HTTPException(404, "No existe ese conector")
            if body.env is not None:  # se suman a las que ya había; un valor vacío saca la variable
                merged = {**connector_store.get(name).get("env", {}), **body.env}
                fields["env"] = {k: v for k, v in merged.items() if v != ""}
            if fields:
                connector_store.update(name, **fields)
        except connector_store.StoreError as e:
            raise HTTPException(422, str(e)) from None
        if body.enabled is False:
            connector_manager.stop(name)
        elif body.enabled is True:
            connector_start(name)
        return connectors_view()

    @app.delete("/api/connectors/{name}")
    def delete_connector(name: str):
        if connector_store.get(name) is None:
            raise HTTPException(404, "No existe ese conector")
        try:
            connector_manager.stop(name)
        except Exception:  # noqa: BLE001 - un cierre que falla no impide quitarlo
            pass
        connector_store.remove(name)
        try:
            connector_buttons.remove_for(name)  # sus botones ya no sirven
        except connector_store.StoreError:
            pass
        return connectors_view()

    @app.get("/api/buttons")
    def get_buttons():
        # Solo lo guardado: las sugerencias se piden aparte por conector (pueden arrancar un proceso dormido).
        return {"buttons": connector_buttons.list_all()}

    @app.get("/api/connectors/{name}/suggestions")
    def get_suggestions(name: str):
        if connector_store.get(name) is None:
            raise HTTPException(404, "No existe ese conector")
        return {"suggestions": connector_buttons.suggestions(name)}

    @app.post("/api/buttons")
    def create_button(body: ButtonIn):
        try:
            connector_buttons.add(body.label, body.connector, body.tool, body.args)
        except connector_store.StoreError as e:
            raise HTTPException(422, str(e)) from None
        return {"buttons": connector_buttons.list_all()}

    @app.delete("/api/buttons/{button_id}")
    def delete_button(button_id: int):
        try:
            found = connector_buttons.remove(button_id)
        except connector_store.StoreError as e:
            raise HTTPException(422, str(e)) from None
        if not found:
            raise HTTPException(404, "No existe ese botón")
        return {"buttons": connector_buttons.list_all()}

    @app.post("/api/buttons/{button_id}/run")
    def run_button(button_id: int):
        """Ejecuta el botón sin modelo y sin tocar ninguna conversación."""
        try:
            return {"text": connector_buttons.run(button_id)}
        except connector_store.StoreError as e:
            raise HTTPException(422, str(e)) from None

    @app.put("/api/conversations/{conversation_id}/title")
    def rename_conversation(conversation_id: str, body: TitleIn):
        if not conversations.is_valid_id(conversation_id):
            raise HTTPException(404, "No existe esa conversación")
        try:
            return {"title": conversations.set_title(conversation_id, body.title)}
        except FileNotFoundError:
            raise HTTPException(404, "No existe esa conversación") from None

    @app.put("/api/conversations/{conversation_id}/pin")
    def pin_conversation(conversation_id: str, body: PinIn):
        if not conversations.is_valid_id(conversation_id):
            raise HTTPException(404, "No existe esa conversación")
        try:
            return {"pinned": conversations.set_pinned(conversation_id, body.pinned)}
        except FileNotFoundError:
            raise HTTPException(404, "No existe esa conversación") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.put("/api/conversations/{conversation_id}/limit")
    def set_conversation_limit(conversation_id: str, body: LimitIn):
        """Sube (o baja) el límite de costo de una conversación de modo IA."""
        if not conversations.is_valid_id(conversation_id) or conversations.mode(conversation_id) != "ai":
            raise HTTPException(404, "No existe esa conversación de modo IA")
        try:
            conversations.set_limit(conversation_id, body.usd)
        except FileNotFoundError:
            raise HTTPException(404, "No existe esa conversación de modo IA") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"chat": chat.status(conversation_id)}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str):
        """Elimina la conversación de verdad (su archivo). Lo que guardó en tus notas y archivos
        queda: eso no es de la conversación."""
        if not conversations.is_valid_id(conversation_id) or not conversations.delete(conversation_id):
            raise HTTPException(404, "No existe esa conversación")
        # Un «Guardar» o un archivo pendientes de ESA conversación ya no tienen a dónde ir.
        core.SESSIONS.pop(session_for(conversation_id), None)
        return {"ok": True}

    # --- Canales: ver su estado y prender / apagar los que se pueden --------------
    # main se importa acá adentro: él importa a la web (por nombre) y no al revés.

    def channels_state() -> dict:
        from epsilon import main

        main.refresh_plugins()  # solo lee manifiestos: no ejecuta nada de los plugins
        snapshot = state.snapshot()
        found = []
        for name in main.CHANNELS:
            info = snapshot.get(name, {"state": "skipped", "detail": ""})
            plugin = main.is_plugin(name)
            manifest = main.plugin_info(name)
            found.append(
                {
                    "name": name,
                    "state": info["state"],  # running / starting / skipped / stopped / off
                    "detail": info["detail"],
                    "switchable": main.switchable(name),  # la web no se puede apagar a sí misma
                    "enabled": not main.switched_off(name),
                    "source": "plugin" if plugin else "builtin",
                    "label": manifest.label if manifest else None,  # los incluidos los nombra la página
                    "about": manifest.about if manifest else None,
                    "warning": PLUGIN_WARNING if plugin else None,
                }
            )
        for folder, reason in main.INVALID.items():
            found.append(
                {
                    "name": folder,
                    "state": "invalid",
                    "detail": reason,
                    "switchable": False,
                    "enabled": False,
                    "source": "plugin",
                    "label": folder,
                    "about": None,
                    "warning": None,
                }
            )
        return {"channels": found}

    @app.get("/api/channels")
    def get_channels():
        return channels_state()

    @app.put("/api/channels/{name}")
    def set_channel(name: str, body: ChannelIn):
        """Prende o apaga un canal de verdad (el bot deja de escuchar) y deja la elección guardada,
        así el servicio la respeta al reiniciar."""
        from epsilon import main

        main.refresh_plugins()
        if name not in main.CHANNELS or not main.switchable(name):
            raise HTTPException(404, "Ese canal no se puede apagar ni prender desde acá.")
        if body.enabled and main.is_plugin(name) and not body.confirmed:
            raise HTTPException(428, PLUGIN_WARNING)
        key = f"{name.upper()}_ENABLED"
        try:
            if body.enabled:
                if not main.check_configured(name, os.environ):
                    raise main.ChannelError(main.not_configured_message(name))
                config.update({key: "si"}, [])
                main.start_channel(name)
            else:
                config.update({key: "no"}, [])
                main.stop_channel(name)
        except main.ChannelError as e:
            raise HTTPException(409, str(e)) from None
        return channels_state()

    def config_state() -> dict:
        from epsilon import main

        main.refresh_plugins()  # un plugin copiado con Epsilon andando ya muestra sus ajustes
        # Sin secretos: de las claves solo sale si están cargadas y su largo.
        return {
            "settings": config.status(),
            "pending_restart": config.pending_restart(),
            "channels": state.snapshot(),  # activo / detenido (con su motivo) / omitido
            "voice": transcribe.status(),  # apagada / cargado / descargado / descargando / sin descargar / no instalado
            # Bytes del modelo que Epsilon bajó y se puede borrar; None si no hay.
            "voice_model_bytes": transcribe.deletable_model(),
            "model": llm.default_model(),  # el predeterminado («proveedor:modelo»), para el botón del encabezado
        }

    @app.get("/api/usage")
    def get_usage():
        """Gasto de IA: por período y las peticiones de hoy. Solo lecturas del registro."""
        data = usage.report()
        raw = os.environ.get("USD_ARS", "").strip()
        try:
            rate = float(raw) if raw else None
        except ValueError:
            rate = None
        data["ars_rate"] = rate if rate and rate > 0 else None  # None: sin cotización cargada
        return data

    # --- Biblioteca: notas, archivos y tickets, solo lectura -----------------

    def library_file(path: str):
        """La ruta real de un archivo de la biblioteca, o 404 (nunca sale de DATA_DIR)."""
        try:
            return library.resolve(path)
        except ValueError:
            raise HTTPException(404, "Ese archivo no existe") from None

    @app.get("/api/library")
    def get_library(
        q: str = Query("", max_length=100),
        kind: str = Query("", pattern="^(note|file|ticket)?$"),
        offset: int = Query(0, ge=0),
        limit: int = Query(40, ge=1, le=100),
    ):
        data = library.search(q, kind, offset, limit)
        data["items"] = [asdict(e) for e in data["items"]]
        data["root"] = library.root()
        data["can_reveal"] = library.can_reveal()
        return data

    @app.get("/api/library/text")
    def get_library_text(path: str = Query(max_length=300)):
        library_file(path)
        try:
            return library.read_text(path)
        except ValueError:
            raise HTTPException(415, "Ese archivo no es de texto") from None

    @app.get("/api/library/file")
    def get_library_file(path: str = Query(max_length=300)):
        """Imágenes y PDF, con su tipo fijado por la extensión (el navegador no adivina nada)."""
        file = library_file(path)
        media = library.media_type(file)
        if media is None:
            raise HTTPException(415, "Ese archivo no se abre así")
        return FileResponse(file, media_type=media, content_disposition_type="inline")

    @app.post("/api/library/reveal")
    def reveal_in_folder(body: RevealIn):
        library_file(body.path)
        try:
            library.reveal(body.path)
        except OSError as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        return config_state()

    @app.get("/api/models")
    def get_models(refresh: bool = Query(False)):
        """Los modelos que se pueden elegir (sin gasto). Un proveedor caído no aporta modelos ni rompe nada:
        queda en `errors` con su motivo. `providers` son los que respondieron (aunque no tengan modelos).
        La lista de un proveedor remoto se recuerda un rato (MODELS_TTL); `?refresh=1` la pide de nuevo."""
        names = providers.names()
        now = time.monotonic()
        models, up, errors = [], [], {}
        remembered = {}
        for name in names:
            found = _models_cache.get(name)
            if found and not refresh and now - found[0] < MODELS_TTL.get(name, 0.0):
                remembered[name] = found[1]
        pool = ThreadPoolExecutor(max_workers=max(1, len(names)))
        futures = {name: pool.submit(providers.get(name).list_models) for name in names if name not in remembered}
        deadline = time.monotonic() + MODELS_TIMEOUT
        for name in names:
            if name in remembered:
                up.append(name)
                models += [{"id": f"{name}:{model}", "provider": name} for model in remembered[name]]
                continue
            future = futures[name]
            try:
                listed = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                errors[name] = f"El proveedor {name} no respondió a tiempo."
            except ValueError as e:  # error esperable: su mensaje ya es para el usuario
                errors[name] = config.redact(str(e))
            except Exception:
                errors[name] = f"No pude consultar los modelos de {name}."
            else:
                up.append(name)
                models += [{"id": f"{name}:{model}", "provider": name} for model in listed]
                if name in MODELS_TTL:
                    _models_cache[name] = (time.monotonic(), list(listed))  # los errores nunca se recuerdan
        pool.shutdown(wait=False, cancel_futures=True)  # uno lento sigue por su cuenta, sin trabar la respuesta
        return {
            "models": models,
            "providers": up,
            "errors": errors,
            "default": llm.default_model(),
            "favorites": favorites.load(),
            "overrides": [task for task in llm.TASKS if os.environ.get(f"AI_MODEL_{task.upper()}")],
        }

    @app.put("/api/models/favorites")
    def put_favorites(body: FavoritesIn):
        """Guarda los modelos favoritos (la lista entera, en orden)."""
        try:
            return {"favorites": favorites.save(body.favorites)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/api/voice/delete-model")
    def delete_voice_model():
        """Libera el disco del modelo de voz. Solo el que bajó Epsilon, y con la voz apagada."""
        try:
            transcribe.delete_model()
        except transcribe.VoiceError as e:
            raise HTTPException(409, str(e)) from None
        return config_state()

    @app.put("/api/config")
    def put_config(body: ConfigIn):
        from epsilon import main

        main.refresh_plugins()  # que sus ajustes existan antes de validar lo que llega
        _models_cache.clear()  # una clave o una dirección nuevas pueden cambiar qué modelos hay
        try:
            config.update(body.changes, body.clear)
        except config.ConfigError as e:
            # Los mensajes dicen qué está mal por ajuste y nunca repiten el valor.
            return JSONResponse(
                {"detail": "Revisá los campos marcados.", "errors": e.errors},
                status_code=422,
            )
        return config_state()

    @app.post("/api/config/test/{target}")
    def test_connection(target: str):
        if target not in checks.CHECKS:
            raise HTTPException(404, "Esa prueba no existe")
        # Corre en un hilo aparte (def, no async): hace una llamada de red.
        return checks.run(target)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        # Los navegadores lo piden solos en la raíz, aunque la página declare otro.
        return FileResponse(STATIC_DIR / "img" / "favicon.ico", media_type="image/x-icon")

    @app.get("/uso", include_in_schema=False)
    def usage_page():
        return FileResponse(STATIC_DIR / "uso.html")

    @app.get("/permisos", include_in_schema=False)
    def permissions_page():
        return FileResponse(STATIC_DIR / "permisos.html")

    @app.get("/rutinas", include_in_schema=False)
    def routines_page():
        return FileResponse(STATIC_DIR / "rutinas.html")

    @app.get("/conectores", include_in_schema=False)
    def connectors_page():
        return FileResponse(STATIC_DIR / "conectores.html")

    @app.get("/biblioteca", include_in_schema=False)
    def library_page():
        return FileResponse(STATIC_DIR / "biblioteca.html")

    @app.get("/canales", include_in_schema=False)
    def channels_page():
        return FileResponse(STATIC_DIR / "canales.html")

    @app.get("/config", include_in_schema=False)
    def config_page():
        return FileResponse(STATIC_DIR / "config.html")

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


def port_in_use(port: int) -> bool:
    """¿Hay otro programa escuchando en ese puerto?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex((HOST, port)) == 0  # 0 = alguien contestó


def run():
    """Levanta el servidor y se queda esperando (bloquea hasta que lo cortan)."""
    import uvicorn  # acá adentro: importarlo es lento y solo hace falta al arrancar

    try:
        port = read_port(os.environ)
    except ValueError as e:
        raise SystemExit(str(e))

    # Lo comprobamos antes, para dar un mensaje claro en vez del error de uvicorn.
    if port_in_use(port):
        raise SystemExit(
            f"El puerto {port} está ocupado por otro programa. "
            "Cerralo, o cambiá WEB_PORT en la configuración (o en el .env)."
        )

    state.set("web", "running", f"http://{HOST}:{port}")
    # uvicorn maneja solo Ctrl+C y la señal de systemd (corre en el hilo principal).
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    run()
