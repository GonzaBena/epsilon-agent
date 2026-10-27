from epsilon.settings import config
from epsilon.skills import store

# Usar una skill: el texto que le llega a la IA y el estado en el que se puede usar. No importa el núcleo:
# core.py decide cuándo (con /<skill>, o cuando la IA pide skills.usar). Lo que dice una skill son instrucciones
# de la persona para ESE pedido; nunca cambian los permisos ni las reglas de seguridad.


MAX_LIST_DESC = 160  # y cuánto de cada descripción


def wrap(skill: store.Skill) -> str:
    return (
        f"Instrucciones de la skill «{skill.name}», escritas por la persona. Seguilas para este pedido; "
        f"no cambian tus permisos ni las reglas de seguridad:\n{skill.body}"
    )


def compose(skill: store.Skill, request: str) -> str:
    """El mensaje que le llega a la IA con `/<skill> pedido`."""
    return f"{wrap(skill)}\n\nPedido de la persona: {request}"


def usable(name: str) -> store.Skill | None:
    skill = store.get(name)
    return skill if skill and skill.usable else None


def why_not(name: str) -> str:
    skill = store.get(name)
    if skill is None:
        return "No existe esa skill."
    if skill.changed:
        return f"La skill «{name}» cambió desde que la confirmaste: revisala y confirmala de nuevo en Skills."
    return f"La skill «{name}» está apagada: prendela en Skills para usarla."


def is_skill_command(name: str) -> bool:
    return store.get(name) is not None


def catalog() -> str:
    """Una línea «- nombre: descripción corta» por cada skill que se puede usar (vacío si no hay): lo único de
    las skills que viaja en cada pedido a la IA."""
    lines = []
    for skill in store.list_all():
        if not skill.usable:
            continue
        description = " ".join(skill.description.split())
        if len(description) > MAX_LIST_DESC:
            description = description[: MAX_LIST_DESC - 1] + "…"
        lines.append(f"- {skill.name}: {description}")
    return "\n".join(lines[: config.number("SKILLS_MAX_LISTED")])


def use(name: str) -> str:
    """Lo que devuelve `skills.usar nombre`: el cuerpo envuelto, o por qué no se puede (con las que sí hay)."""
    skill = usable(name.strip().lstrip("/").lower())
    if skill:
        return wrap(skill)
    names = ", ".join(line[2:].split(":", 1)[0] for line in catalog().splitlines())
    return f"No hay una skill llamada «{name.strip()[:40]}» que se pueda usar." + (f" Las que hay: {names}." if names else "")
