import os
from dataclasses import dataclass

from google import genai
from google.genai import errors, types

from epsilon.ai import usage

DEFAULT_MODEL = "gemini-3.1-flash-lite"


def _model() -> str:
    # "or" y no el valor por defecto de get(): si la configuración borra el
    # modelo queda "" (vacío), y un modelo vacío no sirve.
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def current_model() -> str:
    """El modelo configurado (para saber, por ejemplo, si tiene precio cargado)."""
    return _model()


def _record_usage(model: str, response) -> None:
    """Anota los tokens gastados (para /uso). Si la respuesta no trae el dato, 0."""
    meta = response.usage_metadata
    input_tokens = (meta.prompt_token_count if meta else 0) or 0
    # El "pensamiento" del modelo se cobra como salida, así que lo sumamos.
    output_tokens = (meta.candidates_token_count if meta else 0) or 0
    output_tokens += (getattr(meta, "thoughts_token_count", 0) if meta else 0) or 0
    usage.record(model, input_tokens, output_tokens)


def generate(
    parts: list, prompt: str, max_tokens: int = 2000, json_mode: bool = False
) -> str:
    """Manda las partes (texto, imagen, PDF) + el prompt y devuelve el texto.

    Con json_mode=True obliga al modelo a responder JSON válido."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = _model()
    response = client.models.generate_content(
        model=model,
        contents=[*parts, prompt],  # el * "desarma" la lista de partes
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=max_tokens,  # tope de salida, para controlar el costo
            response_mime_type="application/json" if json_mode else None,
        ),
    )

    _record_usage(model, response)

    return response.text


def chat(turns: list[tuple[str, str]], system: str, max_tokens: int = 1500) -> str:
    """Conversación de varios turnos. turns = [("user" | "model", texto), ...], el último del usuario.

    `system` son las instrucciones fijas. Anota el gasto igual que generate."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = _model()
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role=role, parts=[types.Part(text=text)]) for role, text in turns],
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,  # tope de salida, para controlar el costo
        ),
    )
    _record_usage(model, response)
    return response.text


@dataclass
class ToolSpec:
    """Una herramienta que el modelo puede pedir: nombre, para qué sirve y qué va en su argumento."""

    name: str
    description: str
    arg_description: str


@dataclass
class Step:
    """Lo que contestó el modelo: texto y/o llamadas pedidas [(nombre, argumento)].

    `turn` es el turno del modelo tal cual vino (opaco): hay que volver a mandarlo en el historial."""

    text: str
    calls: list[tuple[str, str]]
    turn: object


def text_turn(role: str, text: str):
    """Un turno de texto ("user" o "model") para el historial de chat_tools."""
    return types.Content(role=role, parts=[types.Part(text=text)])


def results_turn(results: list[tuple[str, str]]):
    """El turno del usuario con lo que devolvió cada herramienta: [(nombre, resultado)]."""
    parts = [
        types.Part.from_function_response(name=name, response={"result": result})
        for name, result in results
    ]
    return types.Content(role="user", parts=parts)


def _declaration(tool: ToolSpec) -> types.FunctionDeclaration:
    # Cada herramienta recibe un único parámetro de texto, "args".
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema={
            "type": "object",
            "properties": {"args": {"type": "string", "description": tool.arg_description}},
            "required": ["args"],
        },
    )


def chat_tools(
    history: list, system: str, tools: list[ToolSpec], max_tokens: int = 1500
) -> Step:
    """Un paso de conversación con herramientas. `history` son turnos (text_turn / results_turn / Step.turn).

    El modelo solo PIDE las llamadas: Epsilon decide si las ejecuta (por eso no hay llamada automática)."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = _model()
    response = client.models.generate_content(
        model=model,
        contents=history,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,  # tope de salida, para controlar el costo
            tools=[types.Tool(function_declarations=[_declaration(t) for t in tools])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    _record_usage(model, response)

    calls = []
    for call in response.function_calls or []:
        arg = (call.args or {}).get("args")
        calls.append((call.name, arg if isinstance(arg, str) else ""))
    # Con llamadas, .text puede fallar o venir vacío: lo tratamos como "".
    try:
        text = response.text or ""
    except Exception:
        text = ""
    return Step(text=text, calls=calls, turn=response.candidates[0].content)


def check() -> str:
    """Comprueba la clave y el modelo SIN generar texto (no gasta tokens).

    Pide los metadatos del modelo: si la clave o el nombre están mal, falla.
    Devuelve el modelo, o lanza ValueError con un mensaje claro (sin la clave)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta la clave de Gemini. Cargala en la configuración.")

    model = _model()
    # El cliente va en una variable: si se encadena (Client(...).models.get(...))
    # se libera antes de terminar y cierra su conexión.
    client = genai.Client(api_key=key)
    try:
        client.models.get(model=model)
    except errors.APIError as e:
        # "from None": que el error original (que puede traer la clave) no viaje.
        raise ValueError(_explain(e.code, model)) from None
    except Exception as e:
        # Solo el tipo del error: el texto podría traer la clave.
        raise ValueError(
            f"No pude conectar con Gemini ({type(e).__name__}). Revisá tu conexión a internet."
        ) from None
    return model


def _explain(code: int, model: str) -> str:
    """Qué significa cada código de error de Gemini, en palabras de uso."""
    if code in (400, 401, 403):
        return "Gemini rechazó la clave. Revisá que esté bien copiada."
    if code == 404:
        return f"El modelo {model} no existe o tu clave no tiene acceso a él."
    if code == 429:
        return "Gemini dice que superaste el límite de uso. La clave es válida; probá más tarde."
    return f"Gemini respondió con un error (código {code})."
