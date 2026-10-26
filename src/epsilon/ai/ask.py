from itertools import count

from epsilon.ai import chat, llm
from epsilon.settings import config
from epsilon.skills import runtime, store

# /ia: una pregunta suelta a la IA. Es un turno de modo IA de un solo mensaje: sin la charla de antes, pero con
# la memoria y con las herramientas, que pasan por los mismos permisos que en el chat (core._run_calls). En vez
# de una conversación guardada tiene un «ámbito» en memoria, único por llamada: los permisos y el «Sí, por el
# resto de la conversación» valen solo para esa pregunta. No conoce ningún canal.

MAX_QUESTION = 1000  # caracteres: sin límite de costo por conversación, el tope de entrada acota el gasto
SCOPE_PREFIX = "ia:"
_calls = count(1)


def is_scope(conversation_id: str | None) -> bool:
    """¿Este id es el ámbito de una pregunta de /ia (y no una conversación guardada)?"""
    return bool(conversation_id) and conversation_id.startswith(SCOPE_PREFIX)


def begin(question: str) -> chat.Turn:
    """El turno de una pregunta. Levanta ValueError si es demasiado larga."""
    question = question.strip()
    if len(question) > MAX_QUESTION:
        raise ValueError(f"La pregunta es muy larga ({len(question)} de {MAX_QUESTION} caracteres).")
    history = [llm.text_turn("user", config.redact(question))]
    return chat.Turn(f"{SCOPE_PREFIX}{next(_calls)}", history, label="Pregunta", task="ask")


def check_request(request: str) -> str:
    """El pedido de `/<skill>` sin espacios de más. Levanta ValueError si es demasiado largo."""
    request = request.strip()
    if len(request) > MAX_QUESTION:
        raise ValueError(f"El pedido es muy largo ({len(request)} de {MAX_QUESTION} caracteres).")
    return request


def begin_skill(skill: store.Skill, request: str) -> chat.Turn:
    """El turno de `/<skill> pedido` fuera de un chat de modo IA. El tope de MAX_QUESTION vale para el pedido
    (el cuerpo de la skill ya tiene el suyo)."""
    history = [llm.text_turn("user", config.redact(runtime.compose(skill, check_request(request))))]
    return chat.Turn(f"{SCOPE_PREFIX}{next(_calls)}", history, label="Skill", task="ask")
