import base64
import os

import anthropic

from epsilon.ai.messages import ModelError, File, Message, Step, ToolSpec, call_arguments, parse_call
from epsilon.ai.providers.base import Capabilities, ChatResult, GenerateResult, Provider

# ÚNICO lugar que habla con Anthropic. El cliente se guarda en una variable antes de usarlo. Los errores no
# incluyen ni la clave ni el cuerpo de la respuesta, solo qué pasó en palabras de uso.

TIMEOUT = 120.0


class _Turn(list):
    """Los bloques de un turno del modelo tal cual los devolvió Anthropic (`Message.raw`): se reenvían idénticos
    (con sus ids de herramienta y su razonamiento) mientras el proveedor sea el mismo."""


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ModelError("Falta la clave de Anthropic. Cargala en la configuración.")
    return anthropic.Anthropic(api_key=key, timeout=TIMEOUT)


def _explain(error: Exception, model: str = "") -> str:
    """Qué significa cada error de Anthropic, en palabras de uso (nunca el texto crudo: podría traer la clave)."""
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "Anthropic rechazó la clave. Revisá que esté bien copiada."
    if isinstance(error, anthropic.NotFoundError):
        return f"El modelo {model} no existe o tu clave no tiene acceso a él." if model else "Anthropic no encontró lo pedido."
    if isinstance(error, anthropic.RateLimitError):
        return "Anthropic dice que superaste el límite de uso. La clave es válida; probá más tarde."
    if isinstance(error, anthropic.APIStatusError):
        return f"Anthropic respondió con un error (código {error.status_code})."
    return f"No pude conectar con Anthropic ({type(error).__name__}). Revisá tu conexión a internet."


def _call(fn, model: str = ""):
    """Corre un pedido al SDK y vuelve todo error esperable un ModelError."""
    try:
        return fn()
    except anthropic.AnthropicError as e:
        # "from None": que el error original no viaje.
        raise ModelError(_explain(e, model)) from None


def _file(file: File) -> dict:
    data = base64.standard_b64encode(file.data).decode()
    kind = "document" if file.mime == "application/pdf" else "image"
    return {"type": kind, "source": {"type": "base64", "media_type": file.mime, "data": data}}


def _parts(parts: list) -> list[dict]:
    return [{"type": "text", "text": p} if isinstance(p, str) else _file(p) for p in parts if p != ""]


def _messages(history: list[Message]) -> list[dict]:
    """El historial neutro como mensajes de Anthropic. Cada `tool_result` necesita el id de su `tool_use`: sale del
    turno anterior del modelo (si es de Anthropic lo trae; si no, se inventan ids que coinciden en los dos lados)."""
    out: list[dict] = []
    pending: list[tuple[str, str]] = []  # (nombre, id) de las herramientas pedidas y todavía sin resultado
    counter = 0
    for message in history:
        if message.role == "model":
            if isinstance(message.raw, _Turn):
                content = list(message.raw)
                pending = [(b["name"], b["id"]) for b in content if b.get("type") == "tool_use"]
            else:
                content = _parts(message.parts)
                pending = []
                for call in message.calls:
                    counter += 1
                    pending.append((call.id, f"toolu_{counter}"))
                    content.append({"type": "tool_use", "id": f"toolu_{counter}", "name": call.name, "input": call_arguments(call)})
            if content:
                out.append({"role": "assistant", "content": content})
            continue
        content = []
        for result in message.results:
            found = next((p for p in pending if p[0] == result.name), None)
            if found:
                pending.remove(found)
                tool_id = found[1]
            else:
                counter += 1
                tool_id = f"toolu_{counter}"
            content.append({"type": "tool_result", "tool_use_id": tool_id, "content": result.result})
        content += _parts(message.parts)
        if content:
            out.append({"role": "user", "content": content})
    return out


def _tool(tool: ToolSpec) -> dict:
    # Cada herramienta recibe un parámetro de texto, "args" (y, un grupo, la "accion" a elegir).
    return {"name": tool.name, "description": tool.description, "input_schema": tool.parameters()}


def _turn(content) -> _Turn:
    """Los bloques de la respuesta como parámetros para reenviar. El razonamiento va tal cual vino."""
    blocks = _Turn()
    for block in content:
        if block.type == "text":
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        elif block.type in ("thinking", "redacted_thinking"):
            blocks.append(block.model_dump(exclude_none=True))
    return blocks


def _tokens(response) -> tuple[int, int]:
    """Los tokens gastados. Lo que se lee de la caché se suma a la entrada (sin precio aparte, por ahora)."""
    usage = response.usage
    cached = (getattr(usage, "cache_read_input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return (usage.input_tokens or 0) + cached, usage.output_tokens or 0


class AnthropicProvider(Provider):
    name = "anthropic"

    def generate(self, model, parts, prompt, max_tokens, json_mode):
        client = _client()
        if json_mode:
            prompt += "\n\nRespondé solo con JSON válido, sin texto antes ni después."
        content = [*_parts(parts), {"type": "text", "text": prompt}]
        response = _call(
            lambda: client.messages.create(
                model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": content}]
            ),
            model,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return GenerateResult(text, *_tokens(response))

    def chat(self, model, history, system, tools, max_tokens, allow_calls, on_text=None):
        client = _client()
        extra = {}
        if tools:
            extra["tools"] = [_tool(t) for t in tools]
            if not allow_calls:
                extra["tool_choice"] = {"type": "none"}
        if system:
            extra["system"] = system
        messages = _messages(history)
        if on_text is None:
            response = _call(lambda: client.messages.create(model=model, max_tokens=max_tokens, messages=messages, **extra), model)
        else:

            def streamed():
                # Salir del `with` (también si `on_text` corta) cierra la conexión. Las herramientas y los tokens
                # salen del mensaje final, igual que sin streaming.
                with client.messages.stream(model=model, max_tokens=max_tokens, messages=messages, **extra) as stream:
                    for piece in stream.text_stream:
                        if piece:
                            on_text(piece)
                    return stream.get_final_message()

            response = _call(streamed, model)
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [parse_call(b.name, b.input) for b in response.content if b.type == "tool_use"]
        turn = Message("model", [text] if text else [], calls, raw=_turn(response.content))
        return ChatResult(Step(text, [(c.id, c.args) for c in calls], turn), *_tokens(response))

    def list_models(self):
        client = _client()
        return [m.id for m in _call(lambda: list(client.models.list(limit=100)))]

    def check(self, model):
        # Pide los metadatos del modelo (o la lista, sin modelo): si la clave o el nombre están mal, falla. No gasta.
        client = _client()
        if not model:
            return f"{len(self.list_models())} modelos"
        _call(lambda: client.models.retrieve(model), model)
        return model

    def capabilities(self, model):
        return Capabilities(tools=True, images=True, pdf=True)
