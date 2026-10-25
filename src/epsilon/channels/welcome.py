"""El saludo y las ideas de la pantalla de bienvenida de la web.

Puro: sin modelo, sin red, sin archivos ni reloj propio (la hora llega de afuera). Cuesta cero: el saludo
es una frase de una lista fija. `channels/web.py` lo expone en `GET /api/welcome`."""

import random
from collections.abc import Callable, Sequence

TIPS = "Aquí hay algunos consejos sobre lo que podemos hacer:"

# Cada idea: título y subtítulo para la card, y el texto que queda escrito (sin enviar) en un chat con IA.
CARDS = (
    {
        "title": "Recordame cosas",
        "subtitle": "Avisos a una hora, sin gastar IA",
        "prompt": "Recordame mañana a las 9 que ",
    },
    {
        "title": "Resumí un documento",
        "subtitle": "PDF, imagen o nota",
        "prompt": "Resumí este documento: @",
    },
    {
        "title": "Armá mi rutina diaria",
        "subtitle": "Clima, dólar y pendientes cada mañana",
        "prompt": "Creá una rutina que cada mañana a las 8 me avise el clima y el dólar",
    },
    {
        "title": "Ordená mis notas",
        "subtitle": "Guardá, buscá y editá",
        "prompt": "Mostrame qué notas tengo y proponé cómo ordenarlas",
    },
    {
        "title": "Manejá mi compu",
        "subtitle": "Volumen, música y ventanas",
        "prompt": "Bajá el volumen al 20 y decime qué está sonando",
    },
    {
        "title": "Conectá tus apps",
        "subtitle": "Correo, calendario y más",
        "prompt": "¿Qué conectores puedo agregar y cómo se hace?",
    },
)

# La frase del medio: una pregunta suelta, sin saludo ni nombre (eso va en la primera línea).
ANY_HOUR = ("¿En qué vamos a trabajar hoy?", "¿Qué hacemos hoy?")
MORNING = ("¿Cómo arrancó la mañana?",)  # 5 a 11
AFTERNOON = ("¿Qué tenemos para esta tarde?",)  # 12 a 19
NIGHT = ("¿Algo para dejar listo antes de cerrar el día?",)  # 20 a 4


def salute(hour: int) -> str:
    """El saludo de las visitas siguientes, según la hora local (0 a 23)."""
    if 5 <= hour <= 11:
        return "Buen día"
    if 12 <= hour <= 19:
        return "Buenas tardes"
    return "Buenas noches"


def pool(hour: int) -> tuple[str, ...]:
    """Las frases posibles a esa hora (0 a 23): las de cualquier hora y, al final, la de su franja."""
    if 5 <= hour <= 11:
        return ANY_HOUR + MORNING
    if 12 <= hour <= 19:
        return ANY_HOUR + AFTERNOON
    return ANY_HOUR + NIGHT


def greeting(
    name: str,
    assistant: str,
    hour: int,
    first: bool,
    pick: Callable[[Sequence[str]], str] = random.choice,
) -> str:
    """El saludo, en tres líneas separadas por «\\n» (la web las respeta con `white-space: pre-line`):
    «Hola <nombre>,» · la frase · «aquí hay algunos consejos…». La primera vez la frase presenta al
    asistente; después, el saludo sigue la hora y la frase rota."""
    name = name.strip()
    if first:
        first_line = f"Hola {name}," if name else "Hola,"
        return "\n".join((first_line, f"soy {assistant}, tu nueva asistente,", TIPS[0].lower() + TIPS[1:]))
    first_line = f"{salute(hour)} {name}," if name else f"{salute(hour)},"
    return "\n".join((first_line, pick(pool(hour)), TIPS))
