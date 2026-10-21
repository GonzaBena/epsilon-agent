import contextvars
import os
from contextlib import contextmanager

from epsilon.ai import providers, usage
from epsilon.ai.messages import Call, File, Message, Result, Step, ToolSpec  # noqa: F401 (los usan los llamadores)

# El ÚNICO lugar que el resto del código usa para hablar con un modelo. Elige el proveedor y el modelo
# según la tarea en curso, le pasa el pedido, anota el gasto y devuelve el resultado. Los proveedores
# (`ai/providers/`) son los únicos que conocen la API de cada uno.

DEFAULT_MODEL = "gemini-3.1-flash-lite"
TASKS = ("chat", "ask", "summary", "csv")

# La tarea en curso (chat, ask, summary, csv): como `usage.action`, vale solo para el hilo que la pone, así
# los llamadores no tienen que pasarla por cada función.
_task: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_task", default=None)


@contextmanager
def task(name: str):
    """Mientras dure el bloque, las llamadas al modelo son de esta tarea (ver TASKS)."""
    token = _task.set(name)
    try:
        yield
    finally:
        _task.reset(token)


def current_task() -> str | None:
    return _task.get()


def parse_model(model_id: str) -> tuple[str, str]:
    """«ollama:llama3.1:8b» -> («ollama», «llama3.1:8b»). Sin proveedor conocido, el nombre entero es de Gemini."""
    prefix, separator, rest = model_id.partition(":")
    if separator and prefix in providers.names():
        return prefix, rest
    return "gemini", model_id


def _full(model_id: str) -> str:
    provider, name = parse_model(model_id)
    return f"{provider}:{name}"


def model_for(task_name: str | None = None) -> str:
    """El id completo («proveedor:modelo») del modelo de una tarea (por defecto, la que está en curso).

    «or» y no el valor por defecto de get(): si la configuración borra el modelo queda "" (vacío)."""
    return _full(os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL)


def current_model(task_name: str | None = None) -> str:
    """El modelo configurado (para saber, por ejemplo, si tiene precio cargado)."""
    return model_for(task_name)


def _record(model_id: str, input_tokens: int, output_tokens: int) -> None:
    usage.record(usage.stored_name(model_id), input_tokens, output_tokens)


def generate(parts: list, prompt: str, max_tokens: int = 2000, json_mode: bool = False) -> str:
    """Manda las partes (texto o File) + el prompt y devuelve el texto.

    Con json_mode=True obliga al modelo a responder JSON válido."""
    model_id = model_for()
    provider, model = parse_model(model_id)
    result = providers.get(provider).generate(model, parts, prompt, max_tokens, json_mode)
    _record(model_id, result.input_tokens, result.output_tokens)
    return result.text


def chat_tools(
    history: list, system: str, tools: list[ToolSpec], max_tokens: int = 1500, allow_calls: bool = True
) -> Step:
    """Un paso de conversación con herramientas. `history` son Message (text_turn / results_turn / Step.turn).

    El modelo solo PIDE las llamadas: Epsilon decide si las ejecuta. Con allow_calls=False las herramientas se
    siguen declarando (el historial las nombra) pero el modelo no puede pedir ninguna: tiene que cerrar con texto."""
    model_id = model_for()
    provider, model = parse_model(model_id)
    result = providers.get(provider).chat(model, history, system, tools, max_tokens, allow_calls)
    _record(model_id, result.input_tokens, result.output_tokens)
    return result.step


def text_turn(role: str, text: str) -> Message:
    """Un turno de texto ("user" o "model") para el historial de chat_tools."""
    return Message(role, [text])


def parts_turn(role: str, parts: list) -> Message:
    """Un turno con varias partes: texto (str) y archivos (File), en ese orden."""
    return Message(role, list(parts))


def results_turn(results: list[tuple[str, str]]) -> Message:
    """El turno del usuario con lo que devolvió cada herramienta: [(nombre, resultado)]."""
    return Message("user", results=[Result(name, result) for name, result in results])


def check() -> str:
    """Prueba la conexión con el proveedor del modelo configurado SIN generar texto (no gasta tokens).

    Devuelve el modelo, o lanza ValueError con un mensaje claro (sin claves)."""
    provider, model = parse_model(model_for())
    return providers.get(provider).check(model)
