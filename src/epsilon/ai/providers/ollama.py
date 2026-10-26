import base64
import os
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

# ÚNICO lugar que habla con Ollama (el servidor local de modelos), por HTTP directo. No hay clave: la
# dirección es OLLAMA_URL. Los errores no incluyen ni la ruta ni el cuerpo de la respuesta, solo host y puerto.

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 8192


def _base_url() -> str:
    return (os.environ.get("OLLAMA_URL") or DEFAULT_URL).rstrip("/")


def _num_ctx() -> int:
    try:
        val = int(os.environ.get("OLLAMA_NUM_CTX") or DEFAULT_NUM_CTX)
        return val if val > 0 else DEFAULT_NUM_CTX
    except ValueError:
        return DEFAULT_NUM_CTX


def _request(
    method: str, path: str, body: dict | None = None, timeout: float = 120.0
) -> dict:
    """El ÚNICO punto de red: pide a Ollama y devuelve el JSON. ValueError con un mensaje claro si falla."""
    try:
        response = httpx.request(method, _base_url() + path, json=body, timeout=timeout)
    except httpx.HTTPError:
        # Solo el host y el puerto: el texto del error podría traer la dirección completa.
        raise ModelError(
            f"No pude conectar con Ollama en {urlsplit(_base_url()).netloc}. ¿Está andando?"
        ) from None
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
                detail = f": {err}"
        except Exception:
            pass
        if "failed to parse" in detail.lower():  # el modelo escribió una llamada a herramienta rota: Ollama no la entiende
            raise ModelError(f"La IA pidió una herramienta con un formato que no entiendo. {WEAK_MODEL_HINT}")
        raise ModelError(
            f"Ollama respondió con un error (código {response.status_code}){detail}."
        )
    try:
        return response.json()
    except ValueError:
        raise ModelError("Ollama respondió algo que no entiendo.") from None


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

    def chat(self, model, history, system, tools, max_tokens, allow_calls):
        body = {
            "model": model,
            "messages": _messages(history, system),
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0,
                "num_ctx": _num_ctx(),
            },
        }
        if tools and allow_calls:
            body["tools"] = [_tool(t) for t in tools]
        data = _request("POST", "/api/chat", body)
        message = data.get("message", {})
        text = message.get("content") or ""
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            calls.append(
                parse_call(function.get("name", ""), function.get("arguments"))
            )
        turn = Message("model", [text] if text else [], calls)
        return ChatResult(
            Step(text, [(c.id, c.args) for c in calls], turn), *_tokens(data)
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
