from collections.abc import Callable
from dataclasses import dataclass
import re
import unicodedata

from epsilon.routines import steps

# De lo que hizo la IA en una corrida a una «receta»: la cadena de pasos que la repite gastando lo mínimo. PURO: sin
# modelo, sin hilos, sin archivos. Cada llamada se clasifica mirando DE DÓNDE salió su argumento:
#   fijo   — vacío, o el mismo texto que ya estaba en la instrucción: se repite tal cual, sin modelo.
#   elegir — el argumento es un ítem de la salida de un paso anterior (que su herramienta declara con `Tool.items`):
#            se inserta antes un paso `elegir` (UNA llamada corta al modelo que devuelve un ítem) y la llamada queda
#            fija, con `{{paso N}}` en vez del ítem.
#   libre  — ni una cosa ni la otra (lo que el modelo inventó): la persona decide si se deja fijo o a cargo de la IA.
# Conservador a propósito: una herramienta que no declara ítems nunca produce un `elegir`.


@dataclass(frozen=True)
class Call:
    tool: str
    args: str
    output: str
    ok: bool


def _plain(text: str) -> str:
    """En minúsculas, sin tildes y con los espacios normalizados."""
    folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return " ".join(folded.split())


def _items(pattern: re.Pattern | None, output: str) -> list[str]:
    """Los ítems distintos de una salida, en orden de aparición."""
    if pattern is None:
        return []
    return list(dict.fromkeys(match for match in pattern.findall(output) if match))


def classify(instruction: str, calls: list[Call], items: Callable[[str], re.Pattern | None]) -> list[dict]:
    """Los pasos de la receta, numerados desde 1 por posición: `{"tool", "args", "kind", "text"}` (los `elegir` también
    llevan `source` y `source_tool`). Las llamadas que fallaron se ignoran."""
    said = _plain(instruction)
    recipe: list[dict] = []
    done: list[tuple[int, Call]] = []  # (número de paso, llamada) de lo ya clasificado
    chosen: dict[tuple[int, str], int] = {}  # (paso fuente, ítem) -> el paso `elegir` que ya lo elige

    for call in calls:
        if not call.ok:
            continue
        step = {"tool": call.tool, "args": call.args, "kind": _kind(call.args, said)}
        found = _chosen(call.args, done, items)
        if found:
            number, source, item = found
            if (number, item) not in chosen:
                pick = {
                    "tool": "elegir", "args": instruction, "kind": "elegir", "source": number, "source_tool": source.tool,
                }
                pick["text"] = steps.describe_step(pick)
                recipe.append(pick)
                chosen[(number, item)] = len(recipe)
            step = {"tool": call.tool, "args": call.args.replace(item, f"{{{{paso {chosen[(number, item)]}}}}}"), "kind": "fijo"}
        step["text"] = steps.describe_step(step)
        recipe.append(step)
        done.append((len(recipe), call))
    return recipe


def _kind(args: str, said: str) -> str:
    plain = _plain(args)
    return "fijo" if not plain or plain in said else "libre"


def _chosen(args: str, done: list[tuple[int, Call]], items: Callable[[str], re.Pattern | None]) -> tuple[int, Call, str] | None:
    """(paso fuente, su llamada, ítem) si el argumento lleva EXACTAMENTE un ítem de la salida de una llamada anterior que
    tenía dos o más para elegir; None si no (dos ítems en un argumento no es una elección: queda libre)."""
    for number, source in reversed(done):  # la fuente más cercana gana
        options = _items(items(source.tool), source.output)
        if len(options) < 2:  # con una sola opción no hay nada que elegir
            continue
        present = [item for item in options if item in args]
        if len(present) == 1:
            return number, source, present[0]
        if len(present) > 1:
            return None
    return None
