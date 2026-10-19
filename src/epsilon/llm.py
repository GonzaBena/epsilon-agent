import os
from google import genai
from google.genai import types


def generate(
    parts: list, prompt: str, max_tokens: int = 2000, json_mode: bool = False
) -> str:
    """Manda las partes (texto, imagen, PDF) + el prompt y devuelve el texto.

    Con json_mode=True obliga al modelo a responder JSON válido."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        contents=[*parts, prompt],  # el * "desarma" la lista de partes
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=max_tokens,  # tope de salida, para controlar el costo
            response_mime_type="application/json" if json_mode else None,
        ),
    )
    return response.text
