import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from typing import Callable, Protocol

from epsilon.connectors import store
from epsilon.settings import config

# Los procesos de los conectores MCP. Todos viven en UN hilo con su ciclo asyncio (`_loop`), creado la primera vez
# que hace falta; las funciones públicas son sincrónicas y le mandan el trabajo con `run_coroutine_threadsafe`.
# Perezoso: un conector arranca la primera vez que se lo usa y se cierra solo tras IDLE_CLOSE s sin uso (`tick`).
# El único punto que lanza un proceso es `_open` (los tests lo bloquean y usan un conector falso).

IDLE_CLOSE = 600       # segundos sin uso hasta cerrarlo
MAX_RUNNING = 3        # conectores abiertos a la vez (PCs chicas)
CALL_TIMEOUT = 30      # segundos por llamada (y para descubrir herramientas o leer un recurso)
OPEN_TIMEOUT = 60      # arrancar puede tardar más: `uvx`/`npx` bajan el paquete la primera vez
MAX_DESCRIPTION = 300  # caracteres de la descripción de una herramienta (la escribe el conector: es dato)
MAX_RESULT_CHARS = 4000  # el mismo tope que core.MAX_RESULT_CHARS (un test vigila que coincidan)
MAX_ERROR = 300        # caracteres del motivo de un error
TICK_EVERY = 30        # cada cuánto el hilo mira los inactivos
BASE_ENV = ("PATH", "HOME", "LANG")  # lo único del entorno de Epsilon que hereda un conector

log = logging.getLogger(__name__)


class ConnectorError(Exception):
    """El conector no pudo hacer lo pedido; el texto va para la persona (o la IA) y ya está redactado."""


class Link(Protocol):
    """Un conector abierto. El real habla MCP con el proceso; en los tests, uno falso (tests/connectors/fakes.py).
    `call` tira ConnectorError si la HERRAMIENTA contestó un error (el proceso sigue bien); cualquier otra
    excepción quiere decir que la conexión se rompió."""

    @property
    def alive(self) -> bool: ...
    async def list_tools(self) -> list[dict]: ...
    async def call(self, name: str, args: dict) -> str: ...
    async def read_resource(self, uri: str) -> str | None: ...
    async def close(self) -> None: ...


class _Slot:
    """Lo que se sabe en memoria de un conector: su enlace (si está abierto), cuántas llamadas tiene en curso y
    cuándo se usó por última vez. Solo se toca desde el hilo del ciclo."""

    def __init__(self) -> None:
        self.link: Link | None = None
        self.lock = asyncio.Lock()  # abrir y cerrar, de a uno (las llamadas en sí pueden ir a la vez)
        self.busy = 0
        self.opening = 0  # aperturas en curso: cuentan para MAX_RUNNING aunque todavía no haya enlace
        self.last_used = 0.0


_slots: dict[str, _Slot] = {}
_changed_hook: Callable[[str, list[str]], None] | None = None
_loop_ref: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_ticker_future = None  # el reloj de inactividad (stop_all lo frena; `_loop` lo vuelve a poner)
_ticker_task: asyncio.Task | None = None
_loop_lock = threading.Lock()


# --- El hilo y su ciclo -----------------------------------------------------------------------------------------


def _loop() -> asyncio.AbstractEventLoop:
    """El ciclo compartido; lo crea (con su hilo) la primera vez, y pone el reloj de inactividad si no corre."""
    global _loop_ref, _loop_thread, _ticker_future
    with _loop_lock:
        if _loop_ref is None:
            loop = asyncio.new_event_loop()
            _loop_thread = threading.Thread(target=loop.run_forever, name="connectors", daemon=True)
            _loop_thread.start()
            _loop_ref = loop
        if _ticker_future is None or _ticker_future.done():
            _ticker_future = asyncio.run_coroutine_threadsafe(_ticker(), _loop_ref)
        return _loop_ref


async def _ticker() -> None:
    global _ticker_task
    _ticker_task = asyncio.current_task()
    while True:
        await asyncio.sleep(TICK_EVERY)
        try:
            await _tick(time.monotonic())
        except Exception:  # el reloj nunca se muere
            log.exception("Falló la pasada de los conectores inactivos")


def _run(coro, timeout: float):
    """Corre `coro` en el hilo de los conectores y espera su resultado (con un tope por si algo se cuelga).
    Desde el propio hilo no se puede: esperaría a un ciclo que está esperándolo a él (se congelaría todo)."""
    if threading.current_thread() is _loop_thread:
        coro.close()
        raise RuntimeError("No se puede usar el manager de conectores desde su propio hilo.")
    future = asyncio.run_coroutine_threadsafe(coro, _loop())
    try:
        return future.result(timeout)
    except TimeoutError:
        future.cancel()
        raise ConnectorError("El conector no respondió a tiempo.") from None


def _wait() -> float:
    return OPEN_TIMEOUT + 2 * CALL_TIMEOUT + 15  # abrir + descubrir + la llamada, con margen para cerrar


# --- Texto seguro -------------------------------------------------------------------------------------------------


def _clean(text: str, env: dict[str, str], limit: int) -> str:
    """Saca los valores del entorno del conector (los más largos primero) y los secretos de Epsilon, y recorta."""
    for value in sorted((v for v in env.values() if len(v) >= 3), key=len, reverse=True):
        text = text.replace(value, "***")
    text = config.redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _reason(error: BaseException) -> str:
    """El motivo de una excepción, de la hoja más interna si vino agrupada (anyio usa grupos de excepciones)."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return str(error) or type(error).__name__


def minimal_env(extra: dict[str, str]) -> dict[str, str]:
    """El entorno del proceso: PATH, HOME y LANG de Epsilon más lo que cargó la persona. Nunca las claves de Epsilon."""
    env = {key: os.environ[key] for key in BASE_ENV if key in os.environ}
    return {**env, **extra}


# --- El único punto que lanza procesos ---------------------------------------------------------------------------


def _text_of(blocks) -> str:
    """Une los bloques de texto de una respuesta MCP; si vino algo más (imágenes, audio…), lo avisa."""
    texts = [b.text for b in blocks if getattr(b, "type", None) == "text" and isinstance(getattr(b, "text", None), str)]
    text = "\n".join(texts)
    if len(texts) < len(blocks):
        text += "\n(El conector mandó también contenido que no es texto; no se muestra.)"
    return text


class _McpLink:
    """Un proceso MCP de verdad. Los contextos del SDK (anyio) se abren y se cierran en la misma tarea, `_task`,
    que queda esperando `_closing`; las llamadas usan la sesión desde otras tareas del mismo ciclo."""

    def __init__(self) -> None:
        self._session = None
        self._closing = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done() and not self._closing.is_set()

    def _broken(self, error) -> Exception:
        """Traduce un error del SDK: conexión cerrada → se rompió el enlace; otro → error de la herramienta."""
        from mcp_types import CONNECTION_CLOSED

        if error.error.code == CONNECTION_CLOSED:
            return ConnectionError("Se cerró la conexión con el conector.")
        return ConnectorError(f"El conector respondió con un error: {error.error.message}")

    async def list_tools(self) -> list[dict]:
        from mcp import MCPError, types

        found, cursor = [], None
        try:
            while True:
                params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
                result = await self._session.list_tools(params=params)
                found += result.tools
                cursor = result.next_cursor
                if not cursor or len(found) >= 500:  # un conector no ofrece cientos de herramientas
                    break
        except MCPError as error:
            raise self._broken(error) from None
        return [{"name": t.name, "description": t.description or "", "schema": t.input_schema or {}} for t in found]

    async def call(self, name: str, args: dict) -> str:
        from mcp import MCPError

        try:
            result = await self._session.call_tool(name, args)
        except MCPError as error:
            raise self._broken(error) from None
        text = _text_of(result.content)
        if result.is_error:
            raise ConnectorError(f"La herramienta devolvió un error: {text}")
        return text

    async def read_resource(self, uri: str) -> str | None:
        from mcp import MCPError

        try:
            result = await self._session.read_resource(uri)
        except MCPError as error:
            broken = self._broken(error)
            if isinstance(broken, ConnectionError):
                raise broken from None
            return None  # el conector no tiene ese recurso
        texts = [c.text for c in result.contents if isinstance(getattr(c, "text", None), str)]
        return "\n".join(texts) if texts else None

    async def close(self) -> None:
        self._closing.set()
        if self._task is not None:
            try:  # el SDK acota el cierre (stdin, SIGTERM, SIGKILL); esto es un tope de más
                await asyncio.wait_for(asyncio.shield(self._task), 15)
            except BaseException:  # noqa: BLE001 - cerrar nunca falla hacia afuera
                pass


async def _open(argv: list[str], env: dict[str, str]) -> Link:
    """ÚNICO punto que lanza el proceso de un conector (sin shell) y abre la sesión MCP. Los tests lo bloquean."""
    from mcp import ClientSession, StdioServerParameters, stdio_client

    link = _McpLink()
    ready = asyncio.get_running_loop().create_future()

    async def run() -> None:
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=env)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    link._session = session
                    ready.set_result(None)
                    await link._closing.wait()
        except BaseException as error:  # noqa: BLE001 - si todavía no abrió, el motivo va para quien lo esperaba
            if not ready.done():
                ready.set_exception(ConnectionError(_reason(error)))
            elif not isinstance(error, asyncio.CancelledError):
                log.warning("Un conector se cerró con error: %s", config.redact(_reason(error)))

    link._task = asyncio.create_task(run())
    try:
        await ready
    except BaseException:
        await link.close()  # se canceló la espera o no arrancó: que el proceso no quede suelto
        raise
    return link


# --- Abrir, descubrir, llamar, cerrar ---------------------------------------------------------------------------


def _entry(name: str) -> dict:
    entry = store.get(name)
    if entry is None:
        raise ConnectorError(f"No hay un conector llamado «{name}».")
    if not entry.get("enabled"):
        raise ConnectorError(f"El conector «{name}» está apagado.")
    return entry


def _slot(name: str) -> _Slot:
    return _slots.setdefault(name, _Slot())


def _mark_error(name: str, text: str) -> None:
    try:
        store.update(name, error=text)
    except store.StoreError:
        pass  # lo borraron mientras tanto


async def _close(slot: _Slot) -> None:
    link, slot.link = slot.link, None
    if link is not None:
        try:
            await link.close()
        except Exception:  # noqa: BLE001 - un cierre que falla no frena nada
            log.warning("No pude cerrar un conector limpio")


async def _fail(name: str, slot: _Slot, link: Link, message: str) -> ConnectorError:
    """El conector se colgó o se murió: queda «detenido» con el motivo (ya redactado) y se cierra."""
    async with slot.lock:
        if slot.link is link:
            await _close(slot)
    _mark_error(name, message)
    log.warning("%s", message)
    return ConnectorError(message)


def _tool_hash(tool: dict) -> str:
    raw = tool["name"] + json.dumps(tool["schema"], sort_keys=True, ensure_ascii=False) + tool["description"]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(found: object) -> list[dict]:
    """Las herramientas tal como las cuenta el conector, filtradas (nombre de texto, esquema objeto) y recortadas."""
    tools = []
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            continue
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
        tool = {"name": item["name"][:100], "description": description[:MAX_DESCRIPTION], "schema": schema}
        tool["hash"] = _tool_hash(tool)
        tools.append(tool)
    return tools


async def _discover(name: str, slot: _Slot, link: Link, env: dict[str, str]) -> list[str]:
    """Pide la lista de herramientas, la guarda y devuelve las nuevas o cambiadas (y avisa al gancho)."""
    try:
        found = await asyncio.wait_for(link.list_tools(), CALL_TIMEOUT)
    except ConnectorError as error:
        raise ConnectorError(_clean(str(error), env, MAX_ERROR)) from None
    except BaseException as error:  # noqa: BLE001
        if isinstance(error, asyncio.CancelledError):
            raise
        raise await _fail(name, slot, link, _clean(
            f"El conector «{name}» no pudo decir qué herramientas tiene: {_reason(error)}", env, MAX_ERROR)) from None
    tools = _normalize(found)
    before = {t.get("name"): t.get("hash") for t in (store.get(name) or {}).get("tools", []) if isinstance(t, dict)}
    changed = [t["name"] for t in tools if before.get(t["name"]) != t["hash"]]
    try:
        store.update(name, tools=tools, error="")
    except store.StoreError as error:
        raise ConnectorError(str(error)) from None
    if _changed_hook is not None:
        try:  # fuera del ciclo: el gancho puede tardar o volver a usar el manager sin congelar a nadie
            await asyncio.get_running_loop().run_in_executor(None, _changed_hook, name, changed)
        except Exception:  # noqa: BLE001 - el gancho es de otro módulo: no rompe el arranque
            log.exception("Falló el aviso de herramientas cambiadas")
    return changed


async def _reserve(name: str, slot: _Slot) -> None:
    """Reserva un lugar para abrir `name` (sube `slot.opening`). Si ya hay MAX_RUNNING abiertos o abriéndose,
    cierra el que hace más que no se usa (y no está trabajando). Entre contar y reservar no hay ningún `await`:
    así dos aperturas a la vez no ven las dos el mismo lugar libre."""
    while True:
        running = [(n, s) for n, s in _slots.items() if n != name and (s.link is not None or s.opening)]
        if len(running) < MAX_RUNNING:
            slot.opening += 1
            return
        idle = [(s.last_used, n, s) for n, s in running if s.link is not None and s.busy == 0 and not s.opening]
        if not idle:
            raise ConnectorError(f"Ya hay {MAX_RUNNING} conectores trabajando; probá de nuevo en un rato.")
        _, _, victim = min(idle, key=lambda item: item[0])
        async with victim.lock:
            if victim.busy == 0:
                await _close(victim)


async def _ensure(name: str, slot: _Slot, entry: dict) -> tuple[Link, list[str] | None]:
    """Devuelve el enlace abierto (lo abre si hace falta; si lo abre, descubre las herramientas)."""
    env = entry.get("env", {})
    if slot.link is not None and slot.link.alive:
        return slot.link, None
    await _reserve(name, slot)
    try:
        async with slot.lock:
            if slot.link is not None and slot.link.alive:  # otra llamada lo abrió mientras esperábamos
                return slot.link, None
            await _close(slot)  # uno que se murió solo
            try:
                link = await asyncio.wait_for(_open(list(entry["argv"]), minimal_env(env)), OPEN_TIMEOUT)
            except BaseException as error:  # noqa: BLE001
                if isinstance(error, asyncio.CancelledError):
                    raise
                reason = "tardó demasiado en arrancar" if isinstance(error, TimeoutError) else _reason(error)
                message = _clean(f"No pude arrancar el conector «{name}»: {reason}", env, MAX_ERROR)
                _mark_error(name, message)
                log.warning("%s", message)
                raise ConnectorError(message) from None
            slot.link = link
            slot.last_used = time.monotonic()
    finally:
        slot.opening -= 1
    return link, await _discover(name, slot, link, env)


async def _use(name: str, work):
    """Abre el conector si hace falta y corre `work(link)` con su tope; se encarga de los errores y del uso."""
    entry = _entry(name)
    env = entry.get("env", {})
    slot = _slot(name)
    slot.busy += 1  # antes de esperar nada: así un `tick` que llega ahora no lo cierra
    try:
        link, _ = await _ensure(name, slot, entry)
        slot.last_used = time.monotonic()
        try:
            return await asyncio.wait_for(work(link), CALL_TIMEOUT)
        except ConnectorError as error:  # error de la herramienta: el conector sigue bien
            raise ConnectorError(_clean(str(error), env, MAX_ERROR)) from None
        except TimeoutError:
            raise await _fail(name, slot, link, f"El conector «{name}» tardó más de {CALL_TIMEOUT} s y lo detuve.") from None
        except Exception as error:  # noqa: BLE001 - se rompió la conexión (el proceso se murió)
            if slot.link is not link:  # lo cerró otro a propósito (stop) mientras tanto
                raise ConnectorError(f"El conector «{name}» se apagó mientras trabajaba.") from None
            raise await _fail(name, slot, link, _clean(
                f"El conector «{name}» dejó de responder: {_reason(error)}", env, MAX_ERROR)) from None
    finally:
        slot.busy -= 1
        slot.last_used = time.monotonic()


async def _start(name: str) -> list[str]:
    entry = _entry(name)
    slot = _slot(name)
    slot.busy += 1
    try:
        link, changed = await _ensure(name, slot, entry)
        if changed is None:  # ya estaba abierto: igual vuelve a mirar las herramientas
            changed = await _discover(name, slot, link, entry.get("env", {}))
        return changed
    finally:
        slot.busy -= 1
        slot.last_used = time.monotonic()


async def _stop(name: str) -> None:
    slot = _slots.get(name)
    if slot is not None:
        async with slot.lock:
            await _close(slot)


async def _tick(now: float) -> None:
    for slot in list(_slots.values()):
        if slot.link is None or slot.busy or now - slot.last_used <= IDLE_CLOSE:
            continue
        async with slot.lock:
            if slot.link is not None and not slot.busy and now - slot.last_used > IDLE_CLOSE:
                await _close(slot)


# --- Lo que usa el resto de Epsilon (sincrónico) -----------------------------------------------------------------


def start(name: str) -> list[str]:
    """Arranca el conector si hace falta, guarda sus herramientas y devuelve las nuevas o cambiadas."""
    return _run(_start(name), _wait())


def call(name: str, tool: str, args: dict) -> str:
    """Llama a una herramienta del conector y devuelve su texto (redactado y recortado). ConnectorError si no se pudo."""
    env = _entry(name).get("env", {})
    text = _run(_use(name, lambda link: link.call(tool, args)), _wait())
    return _clean(text if isinstance(text, str) else "", env, MAX_RESULT_CHARS)


def read_resource(name: str, uri: str) -> str | None:
    """El texto de un recurso del conector, o None si no lo tiene."""
    text = _run(_use(name, lambda link: link.read_resource(uri)), _wait())
    return text if isinstance(text, str) else None


def stop(name: str) -> None:
    """Cierra el proceso del conector (si estaba abierto). No lo apaga: vuelve a arrancar la próxima vez que se use."""
    if _loop_ref is not None:
        _run(_stop(name), 30)


def stop_all() -> None:
    """Cierra todos (uno que no se deja cerrar no frena a los demás) y frena el reloj de inactividad."""
    global _ticker_future
    with _loop_lock:
        if _ticker_future is not None and _loop_ref is not None:
            # El futuro ya está «corriendo» y no se deja cancelar desde afuera: se cancela la tarea, en su ciclo.
            _loop_ref.call_soon_threadsafe(lambda: _ticker_task is not None and _ticker_task.cancel())
        _ticker_future = None
    for name in list(_slots):
        try:
            stop(name)
        except Exception as error:  # noqa: BLE001
            log.warning("No pude cerrar el conector «%s»: %s", name, config.redact(_reason(error)))


def tick(now: float) -> None:
    """Cierra los conectores que llevan más de IDLE_CLOSE s sin uso (y sin llamadas en curso)."""
    if _loop_ref is not None:
        _run(_tick(now), 30)


def status(name: str) -> str:
    """«apagado» (la persona lo apagó), «activo» (proceso abierto), «detenido» (falló, con motivo) o «dormido»."""
    entry = store.get(name)
    if entry is None:
        raise ConnectorError(f"No hay un conector llamado «{name}».")
    if not entry.get("enabled"):
        return "apagado"
    slot = _slots.get(name)
    if slot is not None and slot.link is not None and slot.link.alive:
        return "activo"
    return "detenido" if entry.get("error") else "dormido"


def set_changed_hook(fn: Callable[[str, list[str]], None] | None) -> None:
    """Quién se entera cuando un conector trae herramientas nuevas o cambiadas (p. ej. para volverlas a «preguntar»)."""
    global _changed_hook
    _changed_hook = fn


def _reset() -> None:
    """Para los tests: cierra todo y olvida el estado en memoria (el hilo sigue, es uno solo)."""
    stop_all()
    _slots.clear()
    set_changed_hook(None)
