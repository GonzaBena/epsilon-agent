from dataclasses import dataclass

from epsilon import tools
from epsilon.ai import chat
from epsilon.settings import config
from epsilon.utils import navegador, navegador_reglas as reglas

# Guardrails del navegador de la IA, aplicados en código por core._run_calls DESPUÉS de los permisos de cada
# herramienta: pueden volver una llamada «preguntar» o «bloquear», nunca «permitir» algo que los permisos
# bloquean. Falla cerrado: si no se puede determinar el sitio, el rótulo o la pestaña, pregunta.
# Ver docs/superpowers/specs/2026-10-22-navegador-design.md (guardrails 3, 7, 8 y 9).

MAX_BROWSER_ACTIONS = 20  # acciones del navegador por mensaje
MAX_NEW_TABS = 3  # pestañas nuevas por mensaje
ACTING = ("nav_abrir", "nav_clic", "nav_escribir")


@dataclass(frozen=True)
class Review:
    verdict: str = "run"  # run | ask | block
    message: str = ""  # qué se le contesta al modelo si se bloquea
    shown: str = ""  # lo que ve la persona en la pregunta, armado por el código (no por el modelo)


def _trusted(host: str) -> bool:
    return reglas.matches(host, config.site_list("BROWSER_TRUSTED_SITES"))


def _detail(tool_id: str, args: str) -> tuple[bool, str]:
    """(¿hay que preguntar?, qué se muestra) de una acción que toca el navegador. Si algo no se puede
    comprobar, pregunta."""
    try:
        if tool_id == "nav_abrir":
            url = args.strip()
            host = reglas.host_of(url)
            return not _trusted(host), f"Abrir {url}\nSitio: {host or 'desconocido'}"
        head, _, text = args.partition("\n")
        try:
            ref, n = navegador.parse_ref(head if tool_id == "nav_escribir" else args)
        except navegador.NavegadorError:
            return False, ""  # mal formada: el corredor la rechaza antes de tocar nada, no hay nada que aprobar
        host = navegador.tab_host(ref)
        label = navegador.element(ref, n).label
    except navegador.NavegadorError:
        return True, f"No pude comprobar el sitio ni el elemento.\nPedido: {' '.join(args.split())[:200]}"
    shown = f"{'Escribir' if tool_id == 'nav_escribir' else 'Hacer clic'} en «{label}»\nSitio: {host}"
    if tool_id == "nav_escribir":
        shown += f"\nTexto: {text}"
    return (not _trusted(host)) or reglas.is_sensitive_label(label), shown


def review(tool: tools.Tool, args: str, turn: chat.Turn) -> Review:
    """Qué hace falta además de lo que dicen los permisos: nada, preguntar o bloquear."""
    tool_id = tools.ai_id(tool)
    nav = tool.ai_group == "navegador"
    if nav and turn.browser_actions >= MAX_BROWSER_ACTIONS:
        return Review("block", "Llegué al tope de acciones del navegador para este mensaje.")
    if tool_id == "nav_abrir" and turn.browser_tabs >= MAX_NEW_TABS:
        return Review("block", "Llegué al tope de pestañas nuevas para este mensaje.")
    ask, shown = _detail(tool_id, args) if tool_id in ACTING else (False, "")
    if turn.tainted and tool.ai != "read":
        ask = True  # leyó una página de afuera: lo que haga desde acá lo aprueba la persona
    return Review("ask", shown=shown) if ask else Review()


def after_run(tool: tools.Tool, args: str, turn: chat.Turn) -> None:
    """Lleva la cuenta tras una acción del navegador y marca el turno como contaminado si leyó un sitio de afuera."""
    if tool.ai_group != "navegador":
        return
    turn.browser_actions += 1
    tool_id = tools.ai_id(tool)
    if tool_id == "nav_abrir":
        turn.browser_tabs += 1
    elif tool_id == "nav_leer":
        try:
            turn.tainted = turn.tainted or not _trusted(navegador.tab_host(args.strip()))
        except navegador.NavegadorError:
            turn.tainted = True


def _site_of(tool_id: str, args: str) -> str:
    """El sitio de una acción, para el registro; vacío si no se puede saber."""
    if tool_id == "nav_abrir":
        return reglas.host_of(args.strip())
    if tool_id == "nav_pestanas":
        return ""
    try:
        return navegador.tab_host(args.split()[0] if args.split() else "")
    except navegador.NavegadorError:
        return ""


def record(tool: tools.Tool, args: str, result: str, asked: bool) -> None:
    """Deja en el registro de acciones lo que hizo una herramienta del navegador (las demás no dejan nada)."""
    if tool.ai_group == "navegador":
        tool_id = tools.ai_id(tool)
        navegador.audit(tool_id, _site_of(tool_id, args), result.partition("\n")[2] or result, asked)
