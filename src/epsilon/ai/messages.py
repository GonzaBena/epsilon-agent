from dataclasses import dataclass, field

# El idioma común entre Epsilon y los proveedores de IA: ningún tipo de Gemini ni de Ollama sale de
# `ai/providers/`. Cada proveedor traduce estos mensajes a su API y devuelve el mismo formato.


class ModelError(ValueError):
    """Un problema esperable con el modelo (sin conexión, sin clave, no soporta algo): su mensaje es para el usuario."""


# Lo que se le dice a la persona cuando el modelo se trabó (no contestó nada, o pidió una herramienta con un formato
# roto): no es un fallo de Epsilon y reintentar solo costaría plata con un modelo pago, así que se explica y se sugiere.
WEAK_MODEL_HINT = "Suele pasarles a los modelos chicos: probá de nuevo o elegí un modelo más grande."


@dataclass
class File:
    """Un archivo (imagen o PDF) que viaja con el mensaje."""

    data: bytes
    mime: str


@dataclass
class Call:
    """Una herramienta que el modelo pidió: su nombre y el único argumento de texto.
    Si es un grupo de herramientas, `action` dice cuál de sus acciones pidió."""

    name: str
    args: str
    action: str = ""

    @property
    def id(self) -> str:
        """Cómo la ve el núcleo: «nombre», o «grupo.acción» si pidió una acción de un grupo."""
        return f"{self.name}.{self.action}" if self.action else self.name


def parse_call(name: str, arguments: object) -> Call:
    """La llamada que mandó un proveedor: sus argumentos tienen «args» y, en un grupo, «accion».
    Lo que no sea texto se ignora."""
    data = arguments if isinstance(arguments, dict) else {}
    args, action = data.get("args"), data.get("accion")
    return Call(name, args if isinstance(args, str) else "", action if isinstance(action, str) else "")


def call_arguments(call: Call) -> dict:
    """Los argumentos de una llamada tal como los declara `ToolSpec.parameters` (el inverso de parse_call)."""
    return {"accion": call.action, "args": call.args} if call.action else {"args": call.args}


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
    """Una herramienta que el modelo puede pedir: nombre, para qué sirve y qué va en su argumento.
    Un grupo de herramientas lleva además `actions`: el modelo elige una con el parámetro «accion»."""

    name: str
    description: str
    arg_description: str
    actions: tuple[str, ...] = ()

    def parameters(self) -> dict:
        """El esquema JSON de sus parámetros, igual para todos los proveedores."""
        properties = {"args": {"type": "string", "description": self.arg_description}}
        if not self.actions:
            return {"type": "object", "properties": properties, "required": ["args"]}
        action = {"type": "string", "enum": list(self.actions)}
        return {"type": "object", "properties": {"accion": action, **properties}, "required": ["accion", "args"]}


@dataclass
class Step:
    """Lo que contestó el modelo: texto y/o llamadas pedidas [(id, argumento)], con `Call.id` como id.

    `turn` es el turno del modelo: hay que volver a mandarlo en el historial."""

    text: str
    calls: list[tuple[str, str]]
    turn: Message
