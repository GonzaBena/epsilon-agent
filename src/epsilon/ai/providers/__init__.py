from epsilon.ai.providers.base import Provider
from epsilon.ai.providers.gemini import GeminiProvider
from epsilon.ai.providers.ollama import OllamaProvider

# Los proveedores de IA, por nombre. Agregar uno nuevo = un archivo en esta carpeta y una línea acá.
_PROVIDERS: dict[str, Provider] = {"gemini": GeminiProvider(), "ollama": OllamaProvider()}


def names() -> list[str]:
    return list(_PROVIDERS)


def get(name: str) -> Provider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Proveedor de IA desconocido: {name}.") from None
