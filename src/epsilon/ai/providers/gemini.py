import os

from google import genai
from google.genai import errors, types

from epsilon.ai.messages import ModelError, File, Message, Step, ToolSpec, call_arguments, parse_call
from epsilon.ai.providers.base import Capabilities, ChatResult, GenerateResult, Provider

# ÚNICO lugar que habla con Gemini. El cliente se guarda en una variable antes de usarlo:
# encadenar `genai.Client(...).models.x()` lo cierra antes de tiempo.


def _client():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ModelError("Falta la clave de Gemini. Cargala en la configuración.")
    return genai.Client(api_key=key)


def _tokens(response) -> tuple[int, int]:
    """Los tokens gastados. Si la respuesta no trae el dato, 0. El «pensamiento» se cobra como salida."""
    meta = response.usage_metadata
    input_tokens = (meta.prompt_token_count if meta else 0) or 0
    output_tokens = (meta.candidates_token_count if meta else 0) or 0
    output_tokens += (getattr(meta, "thoughts_token_count", 0) if meta else 0) or 0
    return input_tokens, output_tokens


def _part(part):
    if isinstance(part, File):
        return types.Part.from_bytes(data=part.data, mime_type=part.mime)
    return types.Part(text=part)


def _content(message: Message):
    """El turno de Gemini: el original si es de Gemini y, si no, traducido desde lo neutro."""
    if isinstance(message.raw, types.Content):
        return message.raw
    parts = [_part(p) for p in message.parts]
    parts += [types.Part(function_call=types.FunctionCall(name=c.name, args=call_arguments(c))) for c in message.calls]
    parts += [types.Part.from_function_response(name=r.name, response={"result": r.result}) for r in message.results]
    return types.Content(role=message.role, parts=parts)


def _declaration(tool: ToolSpec) -> types.FunctionDeclaration:
    # Cada herramienta recibe un parámetro de texto, "args" (y, un grupo, la "accion" a elegir).
    return types.FunctionDeclaration(name=tool.name, description=tool.description, parameters_json_schema=tool.parameters())


def _thinking_config() -> types.ThinkingConfig | None:
    effort = (os.environ.get("AI_THINKING_EFFORT") or "medio").strip().lower()
    if effort in ("desactivado", "off", "none", "0"):
        return types.ThinkingConfig(thinking_budget=0)
    if hasattr(types, "ThinkingLevel"):
        levels = {
            "bajo": types.ThinkingLevel.LOW,
            "medio": types.ThinkingLevel.MEDIUM,
            "alto": types.ThinkingLevel.HIGH,
        }
        if effort in levels:
            return types.ThinkingConfig(thinking_level=levels[effort])
    budgets = {"bajo": 1024, "medio": 4096, "alto": 8192}
    return types.ThinkingConfig(thinking_budget=budgets.get(effort, 4096))


class GeminiProvider(Provider):
    name = "gemini"

    def generate(self, model, parts, prompt, max_tokens, json_mode):
        client = _client()
        contents = [p if isinstance(p, str) else _part(p) for p in parts]
        response = client.models.generate_content(
            model=model,
            contents=[*contents, prompt],
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=max_tokens,  # tope de salida, para controlar el costo
                response_mime_type="application/json" if json_mode else None,
                thinking_config=_thinking_config(),
            ),
        )
        return GenerateResult(response.text, *_tokens(response))

    def chat(self, model, history, system, tools, max_tokens, allow_calls):
        client = _client()
        response = client.models.generate_content(
            model=model,
            contents=[_content(m) for m in history],
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                tools=[types.Tool(function_declarations=[_declaration(t) for t in tools])] if tools else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                tool_config=None
                if allow_calls
                else types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="NONE")),
                thinking_config=_thinking_config(),
            ),
        )
        calls = [parse_call(call.name, call.args) for call in response.function_calls or []]
        # Con llamadas, .text puede fallar o venir vacío: lo tratamos como "".
        try:
            text = response.text or ""
        except Exception:
            text = ""
        turn = Message(
            "model",
            [text] if text else [],
            calls,
            raw=response.candidates[0].content,
        )
        return ChatResult(Step(text, [(c.id, c.args) for c in calls], turn), *_tokens(response))

    def check(self, model):
        # Pide los metadatos del modelo: si la clave o el nombre están mal, falla. No gasta tokens.
        client = _client()
        try:
            client.models.get(model=model)
        except errors.APIError as e:
            # "from None": que el error original (que puede traer la clave) no viaje.
            raise ModelError(_explain(e.code, model)) from None
        except Exception as e:
            # Solo el tipo del error: el texto podría traer la clave.
            raise ModelError(
                f"No pude conectar con Gemini ({type(e).__name__}). Revisá tu conexión a internet."
            ) from None
        return model

    def list_models(self):
        client = _client()
        try:
            found = list(client.models.list())
        except errors.APIError as e:
            raise ModelError(_explain(e.code, "")) from None
        except Exception as e:
            raise ModelError(
                f"No pude conectar con Gemini ({type(e).__name__}). Revisá tu conexión a internet."
            ) from None
        return [
            m.name.removeprefix("models/") for m in found if "generateContent" in (m.supported_actions or [])
        ]

    def capabilities(self, model):
        return Capabilities(tools=True, images=True, pdf=True, thinking=True)


def _explain(code: int, model: str) -> str:
    """Qué significa cada código de error de Gemini, en palabras de uso."""
    if code in (400, 401, 403):
        return "Gemini rechazó la clave. Revisá que esté bien copiada."
    if code == 404:
        return f"El modelo {model} no existe o tu clave no tiene acceso a él."
    if code == 429:
        return "Gemini dice que superaste el límite de uso. La clave es válida; probá más tarde."
    return f"Gemini respondió con un error (código {code})."
