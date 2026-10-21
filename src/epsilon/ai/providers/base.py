from dataclasses import dataclass

from epsilon.ai.messages import Message, Step, ToolSpec


@dataclass
class Capabilities:
    """Lo que un modelo puede hacer: usar herramientas, leer imágenes, leer PDFs."""

    tools: bool
    images: bool
    pdf: bool


@dataclass
class GenerateResult:
    text: str
    input_tokens: int
    output_tokens: int


@dataclass
class ChatResult:
    step: Step
    input_tokens: int
    output_tokens: int


class Provider:
    """Lo que tiene que saber hacer cada proveedor de IA. Los errores esperables son ModelError (un ValueError) con un
    mensaje en español y sin claves (ni cuerpos crudos de respuesta)."""

    name = ""

    def generate(self, model: str, parts: list, prompt: str, max_tokens: int, json_mode: bool) -> GenerateResult:
        """Un disparo: las partes (str o File) + el prompt, y devuelve el texto."""
        raise NotImplementedError

    def chat(
        self, model: str, history: list[Message], system: str, tools: list[ToolSpec], max_tokens: int, allow_calls: bool
    ) -> ChatResult:
        """Un paso de conversación con herramientas. El modelo solo PIDE las llamadas."""
        raise NotImplementedError

    def check(self, model: str) -> str:
        """Prueba de conexión SIN gasto. Devuelve el modelo o lanza ValueError."""
        raise NotImplementedError

    def list_models(self) -> list[str]:
        """Los modelos disponibles para elegir (sin gasto)."""
        raise NotImplementedError

    def capabilities(self, model: str) -> Capabilities:
        raise NotImplementedError
