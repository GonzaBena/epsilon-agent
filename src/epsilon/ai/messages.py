from dataclasses import dataclass, field

# El idioma común entre Epsilon y los proveedores de IA: ningún tipo de Gemini ni de Ollama sale de
# `ai/providers/`. Cada proveedor traduce estos mensajes a su API y devuelve el mismo formato.


@dataclass
class File:
    """Un archivo (imagen o PDF) que viaja con el mensaje."""

    data: bytes
    mime: str


@dataclass
class Call:
    """Una herramienta que el modelo pidió: su nombre y el único argumento de texto."""

    name: str
    args: str


@dataclass
class Result:
    """Lo que devolvió una herramienta, para volver a mandárselo al modelo."""

    name: str
    result: str


@dataclass
class Message:
    """Un turno de la charla: `user` o `model`, con texto y archivos en orden, llamadas y resultados.

    `raw` es el turno tal cual lo devolvió un proveedor (Gemini lo necesita para reenviar sus firmas de
    razonamiento): cada proveedor lo reutiliza solo si es suyo y, si no, traduce desde lo demás."""

    role: str
    parts: list[str | File] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)
    raw: object = None

    @property
    def text(self) -> str:
        return "".join(p for p in self.parts if isinstance(p, str))


@dataclass
class ToolSpec:
    """Una herramienta que el modelo puede pedir: nombre, para qué sirve y qué va en su argumento."""

    name: str
    description: str
    arg_description: str


@dataclass
class Step:
    """Lo que contestó el modelo: texto y/o llamadas pedidas [(nombre, argumento)].

    `turn` es el turno del modelo: hay que volver a mandarlo en el historial."""

    text: str
    calls: list[tuple[str, str]]
    turn: Message
