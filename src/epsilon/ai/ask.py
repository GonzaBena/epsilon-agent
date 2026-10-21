from epsilon.ai import chat, llm, usage
from epsilon.memory import store as memory
from epsilon.settings import config

# /ia: una pregunta suelta al modelo. Una sola llamada, sin historial de la conversación y sin
# herramientas; lo único que se suma a la pregunta es la memoria. No conoce ningún canal.

MAX_QUESTION = 1000  # caracteres: sin límite de costo por conversación, el tope de entrada acota el gasto


def ask(question: str) -> str:
    """Contesta una pregunta. Levanta ValueError si es demasiado larga."""
    question = question.strip()
    if len(question) > MAX_QUESTION:
        raise ValueError(f"La pregunta es muy larga ({len(question)} de {MAX_QUESTION} caracteres).")
    prompt = f"{chat.system_prompt()}\n\nPregunta del usuario:\n{config.redact(question)}"
    with usage.action("Pregunta"):
        return llm.generate([], prompt, chat.MAX_TOKENS)
