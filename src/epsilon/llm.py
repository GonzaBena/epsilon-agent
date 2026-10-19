import os
from google import genai
from google.genai import types

from epsilon import usage


def generate(
    parts: list, prompt: str, max_tokens: int = 2000, json_mode: bool = False
) -> str:
    """Manda las partes (texto, imagen, PDF) + el prompt y devuelve el texto.

    Con json_mode=True obliga al modelo a responder JSON válido."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    response = client.models.generate_content(
        model=model,
        contents=[*parts, prompt],  # el * "desarma" la lista de partes
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=max_tokens,  # tope de salida, para controlar el costo
            response_mime_type="application/json" if json_mode else None,
        ),
    )

    # Anotamos los tokens gastados (para /uso). Si la respuesta no trae el dato, 0.
    meta = response.usage_metadata
    input_tokens = (meta.prompt_token_count if meta else 0) or 0
    # El "pensamiento" del modelo se cobra como salida, así que lo sumamos.
    output_tokens = (meta.candidates_token_count if meta else 0) or 0
    output_tokens += (getattr(meta, "thoughts_token_count", 0) if meta else 0) or 0
    usage.record(model, input_tokens, output_tokens)

    return response.text
