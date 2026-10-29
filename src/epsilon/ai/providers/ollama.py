import base64
import json
from collections.abc import Callable, Iterator
from contextlib import closing
from urllib.parse import urlsplit

import httpx

from epsilon.ai.messages import (
    WEAK_MODEL_HINT,
    ModelError,
    File,
    Message,
    Step,
    ToolSpec,
    call_arguments,
    parse_call,
)
from epsilon.ai.providers.base import Capabilities, ChatResult, GenerateResult, Provider
from epsilon.settings import config

# ÚNICO lugar que habla con Ollama (el servidor local de modelos), por HTTP directo. No hay clave: la
# dirección es OLLAMA_URL. Los errores no incluyen ni la ruta ni el cuerpo de la respuesta, solo host y puerto.

DEFAULT_URL = "http://127.0.0.1:11434"


def _base_url() -> str:
    return (config.value("OLLAMA_URL") or DEFAULT_URL).rstrip("/")


def _num_ctx() -> int:
    return config.number("OLLAMA_NUM_CTX")


def _unreachable() -> ModelError:
    # Solo el host y el puerto: el texto del error podría traer la dirección completa.
    return ModelError(f"No pude conectar con Ollama en {urlsplit(_base_url()).netloc}. ¿Está andando?")


def _api_error(detail: str, status: int | None = None) -> ModelError:
    """El error que Ollama cuenta (en el cuerpo de una respuesta con error, o en una línea de un stream) como un
    ModelError claro: el motivo corto que dio Ollama, nunca el cuerpo crudo."""
    if "failed to parse" in detail.lower():  # el modelo escribió una llamada a herramienta rota: Ollama no la entiende
        return ModelError(f"La IA pidió una herramienta con un formato que no entiendo. {WEAK_MODEL_HINT}")
    where = f" (código {status})" if status else ""
    return ModelError(f"Ollama respondió con un error{where}{': ' + detail if detail else ''}.")


def _check(response, body: dict | None) -> None:
    """Convierte un código de error HTTP en un ModelError claro (sin el cuerpo crudo de la respuesta)."""
    if response.status_code == 404 and body and body.get("model"):
        model = body["model"]
        raise ModelError(
            f"El modelo «{model}» no está en Ollama. Bajalo con `ollama pull {model}`."
        )
    if response.status_code >= 400:
        detail = ""
        try:
            err = response.json().get("error")
            if err and isinstance(err, str):
                detail = err
        except Exception:
            pass
        raise _api_error(detail, response.status_code)


def _request(
    method: str, path: str, body: dict | None = None, timeout: float = 120.0
) -> dict:
    """Un punto de red: pide a Ollama y devuelve el JSON. ValueError con un mensaje claro si falla."""
    try:
        response = httpx.request(method, _base_url() + path, json=body, timeout=timeout)
    except httpx.HTTPError:
        raise _unreachable() from None
    _check(response, body)
    try:
        return response.json()
    except ValueError:
        raise ModelError("Ollama respondió algo que no entiendo.") from None


def _request_stream(
    method: str, path: str, body: dict | None = None, timeout: float = 120.0
) -> Iterator[dict]:
    """El otro punto de red: igual que `_request` pero con la respuesta por trozos (una línea JSON cada uno).
    Si la conexión se corta a mitad, es el mismo error que si Ollama estuviera apagado."""
    try:
        with httpx.stream(method, _base_url() + path, json=body, timeout=timeout) as response:
            if response.status_code >= 400:
                response.read()  # el cuerpo del error hay que leerlo antes de mirar su JSON
            _check(response, body)
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    raise ModelError("Ollama respondió algo que no entiendo.") from None
    except httpx.HTTPError:
        raise _unreachable() from None


def _image(file: File) -> str:
    if not file.mime.startswith("image/"):
        what = "PDFs" if file.mime == "application/pdf" else f"archivos {file.mime}"
        raise ModelError(
            f"Ollama no lee {what}: solo imágenes. Usá otro modelo para este archivo."
        )
    return base64.b64encode(file.data).decode()


def _messages(history: list[Message], system: str | None = None) -> list[dict]:
    """El historial neutro como mensajes de Ollama. Un `raw` de otro proveedor no se usa."""
    out = [{"role": "system", "content": system}] if system else []
    for message in history:
        for result in message.results:
            out.append(
                {"role": "tool", "tool_name": result.name, "content": result.result}
            )
        images = [_image(p) for p in message.parts if isinstance(p, File)]
        if not (message.text or images or message.calls):
            continue
        entry = {
            "role": "assistant" if message.role == "model" else "user",
            "content": message.text,
        }
        if images:
            entry["images"] = images
        if message.calls:
            entry["tool_calls"] = [
                {"function": {"name": c.name, "arguments": call_arguments(c)}}
                for c in message.calls
            ]
        out.append(entry)
    return out


def _tool(tool: ToolSpec) -> dict:
    # Cada herramienta recibe un parámetro de texto, "args" (y, un grupo, la "accion" a elegir).
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters(),
        },
    }


def _tokens(data: dict) -> tuple[int, int]:
    return data.get("prompt_eval_count") or 0, data.get("eval_count") or 0


def _collect(chunks: Iterator[dict], on_text: Callable[[str], None]) -> tuple[str, list, dict]:
    """Junta los trozos de una respuesta por streaming: el texto (avisando cada trozo a `on_text`), las llamadas a
    herramientas (pueden venir en cualquier trozo) y los conteos de tokens (vienen en el último). Lo que Ollama
    llama «thinking» (el razonamiento) no es parte de la respuesta y se ignora."""
    text, calls, counts = "", [], {}
    # closing: si `on_text` corta (por ejemplo, la persona canceló), la conexión se cierra YA y Ollama deja de generar.
    with closing(chunks) as stream:
        for chunk in stream:
            if error := chunk.get("error"):  # el modelo se cayó a mitad de la respuesta: lo escrito hasta ahí no vale
                raise _api_error(error if isinstance(error, str) else "")
            message = chunk.get("message") or {}
            if piece := message.get("content"):
                text += piece
                on_text(piece)
            calls.extend(message.get("tool_calls") or [])
            if chunk.get("done"):
                counts = chunk
    if not counts:  # el flujo terminó sin el trozo final: la respuesta quedó a medias
        raise ModelError("La respuesta de Ollama llegó cortada.")
    return text, calls, counts


class OllamaProvider(Provider):
    name = "ollama"

    def generate(self, model, parts, prompt, max_tokens, json_mode):
        texts = [p for p in parts if isinstance(p, str)]
        message = {"role": "user", "content": "\n\n".join([*texts, prompt])}
        images = [_image(p) for p in parts if isinstance(p, File)]
        if images:
            message["images"] = images
        body = {
            "model": model,
            "messages": [message],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0,
                "num_ctx": _num_ctx(),
            },
        }
        if json_mode:
            body["format"] = "json"
        data = _request("POST", "/api/chat", body)
        return GenerateResult(
            data.get("message", {}).get("content", ""), *_tokens(data)
        )

    def chat(self, model, history, system, tools, max_tokens, allow_calls, on_text=None):
        body = {
            "model": model,
            "messages": _messages(history, system),
            "stream": on_text is not None,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0,
                "num_ctx": _num_ctx(),
            },
        }
        if tools and allow_calls:
            body["tools"] = [_tool(t) for t in tools]
        if on_text is None:
            data = _request("POST", "/api/chat", body)
            message = data.get("message", {})
            text, raw_calls, counts = message.get("content") or "", message.get("tool_calls") or [], data
        else:
            text, raw_calls, counts = _collect(_request_stream("POST", "/api/chat", body), on_text)
        calls = []
        for call in raw_calls:
            function = call.get("function", {})
            calls.append(
                parse_call(function.get("name", ""), function.get("arguments"))
            )
        turn = Message("model", [text] if text else [], calls)
        return ChatResult(
            Step(text, [(c.id, c.args) for c in calls], turn), *_tokens(counts)
        )

    def list_models(self):
        return [
            m["name"] for m in _request("GET", "/api/tags", timeout=3).get("models", [])
        ]

    def check(self, model):
        # Pide la lista de modelos: no genera nada ni gasta. Sin modelo, solo comprueba que responda.
        names = self.list_models()
        if not model:
            return f"{len(names)} modelos"
        if model not in names and f"{model}:latest" not in names:
            raise ModelError(
                f"Ollama está andando pero no tiene el modelo «{model}». Bajalo con `ollama pull {model}`."
            )
        return model

    def capabilities(self, model):
        reported = (
            _request("POST", "/api/show", {"model": model}, timeout=5).get(
                "capabilities"
            )
            or []
        )
        # PDF nunca: Ollama no los lee. Sin la lista (Ollama viejo), por seguridad no se asume nada.
        return Capabilities(
            tools="tools" in reported, images="vision" in reported, pdf=False
        )
