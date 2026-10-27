import json
import re

from epsilon.settings import config

# Los pasos de una rutina: PURO (sin hilos, reloj, archivos ni procesos). Un paso es `{"tool", "args"}`: una herramienta
# de Epsilon con sus argumentos puestos de antemano. Los argumentos pueden llevar plantillas: `{{paso N}}` (lo que devolvió
# el paso N, que tiene que ser anterior) y `{{disparador}}` (qué hizo saltar una rutina de condición). Lo que se sustituye
# es dato: se recorta; el redactado lo hace quien ejecuta.

MAX_STEPS = 10
MAX_OUTPUT = 1000  # caracteres de la salida de un paso que viajan a los siguientes

# Pasos que solo existen dentro de las rutinas (no son comandos ni herramientas de la IA).
LOCAL = {
    "avisar": {"label": "Avisar", "hint": "el texto de la notificación", "done": "Avisó"},
    "esperar": {"label": "Esperar", "hint": "segundos", "done": "Esperó"},
}


def max_wait() -> int:
    """Segundos que puede esperar un paso `esperar` (ROUTINES_MAX_WAIT_SECONDS)."""
    return config.number("ROUTINES_MAX_WAIT_SECONDS")


def hint(tool_id: str) -> str:
    """Qué va en el argumento de un paso propio de las rutinas (el de `esperar` dice hasta cuánto)."""
    if tool_id == "esperar":
        return f"segundos (1 a {max_wait()})"
    return LOCAL[tool_id]["hint"]
ALIASES = {"abrir": "nav_abrir"}

TEMPLATE = re.compile(r"\{\{\s*(paso\s+\d+|disparador)\s*\}\}")


def refs(text: str) -> list[int | str]:
    """Las referencias de un texto: `{{paso 2}}` -> 2, `{{disparador}}` -> «disparador»."""
    found: list[int | str] = []
    for match in TEMPLATE.finditer(text):
        word = match.group(1)
        found.append("disparador" if word == "disparador" else int(word.split()[1]))
    return found


def render(text: str, outputs: dict[int, str], trigger: str = "") -> str:
    """Sustituye las plantillas. Una salida que falta queda vacía; cada valor se recorta a MAX_OUTPUT."""
    def fill(match: re.Match) -> str:
        word = match.group(1)
        value = trigger if word == "disparador" else outputs.get(int(word.split()[1]), "")
        return value[:MAX_OUTPUT]

    return TEMPLATE.sub(fill, text)


def _render_json(value: object, outputs: dict[int, str], trigger: str) -> object:
    if isinstance(value, str):
        return render(value, outputs, trigger)
    if isinstance(value, list):
        return [_render_json(item, outputs, trigger) for item in value]
    if isinstance(value, dict):
        return {key: _render_json(item, outputs, trigger) for key, item in value.items()}
    return value


def render_args(args: str, outputs: dict[int, str], trigger: str = "") -> str:
    """Como `render`, pero si los argumentos son un objeto JSON se sustituye en los valores ya parseados (una salida con
    comillas o saltos de línea nunca rompe el JSON ni agrega claves) y se vuelve a serializar."""
    try:
        data = json.loads(args)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return json.dumps(_render_json(data, outputs, trigger), ensure_ascii=False)
    return render(args, outputs, trigger)


def validate_chain(chain: list[dict], known: set[str]) -> None:
    """ValueError en español si la cadena no vale: vacía, larga, herramienta desconocida o una referencia a un paso que
    no es anterior. `known`: ids de herramientas permitidas (las propias de las rutinas se suman solas)."""
    if not chain:
        raise ValueError("La rutina necesita al menos un paso.")
    if len(chain) > MAX_STEPS:
        raise ValueError(f"Una rutina puede tener hasta {MAX_STEPS} pasos.")
    allowed = known | set(LOCAL)
    for number, step in enumerate(chain, 1):
        tool = step.get("tool")
        if tool not in allowed:
            raise ValueError(f"El paso {number} usa «{str(tool)[:40]}», que no está disponible para una rutina.")
        for ref in refs(str(step.get("args", ""))):
            if isinstance(ref, int) and not 1 <= ref < number:
                raise ValueError(f"El paso {number} usa la salida del paso {ref}, que no es anterior.")


def parse_chain(text: str, known: set[str]) -> list[dict]:
    """«volumen 15 > abrir yt > esperar 5» -> [{"tool", "args"}, ...]. ValueError si no se entiende."""
    chain = []
    for part in text.split(" > "):
        tool, _, args = " ".join(part.split()).partition(" ")
        tool = ALIASES.get(tool.lower(), tool.lower())
        chain.append({"tool": tool, "args": args})
    validate_chain(chain, known)
    return chain


def describe_step(step: dict) -> str:
    """«volumen 15», «avisar: hola»."""
    if step["tool"] == "avisar":
        return f"avisar: {step['args']}"
    return f"{step['tool']} {step['args']}".strip()
