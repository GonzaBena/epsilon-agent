from dataclasses import dataclass
from urllib.parse import urlsplit

from epsilon import tools
from epsilon.ai import chat
from epsilon.settings import config
from epsilon.utils import navegador, navegador_reglas as reglas

# Guardrails del navegador de la IA, aplicados en código por core._run_calls DESPUÉS de los permisos de cada
# herramienta: pueden volver una llamada «preguntar» o «bloquear», nunca «permitir» algo que los permisos
# bloquean. Falla cerrado: si no se puede determinar el sitio, el rótulo o la pestaña, pregunta.
# Ver docs/superpowers/specs/2026-10-22-navegador-design.md (guardrails 3, 7, 8 y 9).

MAX_BROWSER_ACTIONS = 40  # acciones del navegador por mensaje
MAX_NEW_TABS = 5  # pestañas nuevas por mensaje
ACTING = ("nav_abrir", "nav_clic", "nav_escribir")
CONTEXT_SEPARATOR = " — "  # navegador.py le suma al rótulo de un botón repetido el texto de su tarjeta, tras este separador
LINK_BLOCKED = "Ese enlace lleva a un sitio bloqueado o a esta red: no lo toco."


@dataclass(frozen=True)
class Review:
    verdict: str = "run"  # run | ask | block
    message: str = ""  # qué se le contesta al modelo si se bloquea
    shown: str = ""  # lo que ve la persona en la pregunta, armado por el código (no por el modelo)


def _trusted(host: str) -> bool:
    return navegador.trusted(host)


def _open(args: str) -> Review:
    url = args.strip()
    host = reglas.host_of(url)
    shown = f"Sitio: {host or 'desconocido'}\nAbrir {url[:300]}"
    return Review("ask" if (not _trusted(host) or reglas.has_embedded_url(url)) else "run", shown=shown)


def _link(href: str) -> tuple[str, str]:
    """(veredicto, línea para la pregunta) según a dónde lleva un enlace."""
    scheme = urlsplit(href).scheme.lower()
    if not href or scheme == "javascript":
        return "run", ""
    line = f"\nDestino: {href[:200]}"
    if scheme not in reglas.SCHEMES:
        return "ask", line
    if reglas.matches(reglas.host_of(href), config.site_list("BROWSER_BLOCKED_SITES")) or reglas.local_problem(href):
        return "block", line
    if not _trusted(reglas.host_of(href)) or reglas.has_embedded_url(href):
        return "ask", line
    return "run", line


def _element_action(tool_id: str, args: str) -> Review:
    """clic / escribir: el sitio real de la pestaña, el rótulo del elemento y a dónde lleva si es un enlace."""
    head, _, text = args.partition("\n")
    try:
        ref, n = navegador.parse_ref(head if tool_id == "nav_escribir" else args)
    except navegador.NavegadorError:
        return Review()  # mal formada: el corredor la rechaza antes de tocar nada, no hay nada que aprobar
    try:
        host = navegador.tab_host(ref)
        element = navegador.element(ref, n)
    except navegador.NavegadorError:
        return Review("ask", shown=f"No pude comprobar el sitio ni el elemento.\nPedido: {' '.join(args.split())[:200]}")
    verb = "Escribir" if tool_id == "nav_escribir" else "Hacer clic"
    shown = f"Sitio: {host}\n{verb} en «{element.label}»"
    if tool_id == "nav_escribir":
        shown += f"\nTexto: {text}"
    link, line = _link(element.href)
    if link == "block":
        return Review("block", LINK_BLOCKED)
    ask = not _trusted(host) or reglas.is_sensitive_label(element.label.split(CONTEXT_SEPARATOR, 1)[0]) or link == "ask"
    return Review("ask" if ask else "run", shown=shown + line)


def review(tool: tools.Tool, args: str, turn: chat.Turn) -> Review:
    """Qué hace falta además de lo que dicen los permisos: nada, preguntar o bloquear. `shown` va siempre que
    hay algo que mostrar, para que la pregunta de permiso diga el sitio y el elemento reales aunque el permiso sea «preguntar»."""
    tool_id = tools.ai_id(tool)
    if tool.ai_group == "navegador" and turn.browser_actions >= MAX_BROWSER_ACTIONS:
        return Review("block", "Llegué al tope de acciones del navegador para este mensaje.")
    if tool_id == "nav_abrir" and turn.browser_tabs >= MAX_NEW_TABS:
        return Review("block", "Llegué al tope de pestañas nuevas para este mensaje.")
    acting = Review()
    if not config.enabled("BROWSER_GUARD"):
        # Guardia apagada (BROWSER_GUARD=no): no pregunta por sitios ni rótulos. Siguen los topes de arriba y los bloqueos duros
        # (sitios bloqueados, red local, esquemas raros), que viven en navegador.py y en el verdicto «block» de los enlaces.
        if tool_id in ACTING and tool_id != "nav_abrir":
            acting = _element_action(tool_id, args)
        return Review(shown=acting.shown) if acting.verdict != "block" else acting
    if tool_id == "nav_abrir":
        acting = _open(args)
    elif tool_id in ACTING:
        acting = _element_action(tool_id, args)
    if acting.verdict == "block":
        return acting
    if turn.tainted and tool.ai != "read":
        return Review("ask", shown=acting.shown)  # leyó una página de afuera: lo que haga desde acá lo aprueba la persona
    return acting


def after_run(tool: tools.Tool, args: str, turn: chat.Turn) -> None:
    """Lleva la cuenta tras una acción del navegador y marca el turno como contaminado si lo que leyó o dejó abierto
    es de un sitio de afuera (el sitio que se LEYÓ, no el que quedó después: una página puede navegar justo al terminar)."""
    if tool.ai_group != "navegador":
        return
    turn.browser_actions += 1
    tool_id = tools.ai_id(tool)
    if tool_id == "nav_abrir":
        turn.browser_tabs += 1
        turn.tainted = turn.tainted or not _trusted(reglas.host_of(args.strip()))
    elif tool_id == "nav_leer":
        hosts = navegador.last_read_hosts()
        turn.tainted = turn.tainted or not hosts or not all(_trusted(host) for host in hosts)
    elif tool_id in ("nav_clic", "nav_escribir"):
        try:
            turn.tainted = turn.tainted or not _trusted(navegador.tab_host(args.split()[0] if args.split() else ""))
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
