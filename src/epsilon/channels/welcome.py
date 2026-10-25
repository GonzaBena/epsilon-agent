"""El saludo y las ideas de la pantalla de bienvenida de la web.

Puro: sin modelo, sin red, sin archivos ni reloj propio (la hora llega de afuera). Cuesta cero: el saludo
es una frase de una lista fija. `channels/web.py` lo expone en `GET /api/welcome`."""

import random
import re
from collections.abc import Callable, Sequence

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

# Las frases de «{name}» llevan la coma con el nombre: sin nombre se borran juntos (no queda «Buen día, .»).
ANY_HOUR = ("¿En qué vamos a trabajar hoy?", "¿Qué hacemos hoy, {name}?")
MORNING = ("Buen día, {name}. ¿Cómo arrancó la mañana?",)  # 5 a 11
AFTERNOON = ("Buenas tardes, {name}. ¿Qué tenemos para esta tarde?",)  # 12 a 19
NIGHT = ("Buenas noches, {name}. ¿Algo para dejar listo antes de cerrar el día?",)  # 20 a 4


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
    """El saludo. La primera vez presenta al asistente; después, una frase que rota según la hora."""
    name = name.strip()
    if first:
        who = f"Hola, {name}. Soy {assistant}" if name else f"Hola, soy {assistant}"
        return f"{who}, tu nuevo asistente. Estas son algunas ideas para empezar:"
    phrase = pick(pool(hour))
    if name:
        return phrase.replace("{name}", name)
    return re.sub(r",? ?\{name\}", "", phrase)
