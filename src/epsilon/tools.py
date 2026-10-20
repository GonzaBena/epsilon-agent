from dataclasses import dataclass

# Registro de herramientas: cada una se declara UNA vez, acá, y de acá salen la ayuda
# (core.HELP), el menú de Telegram, el panel de la web (GET /api/tools) y la lista de
# comandos que la web ejecuta sola desde un enlace. No importa nada del proyecto: lo
# usan el núcleo y los canales, y no al revés. Las funciones que hacen el trabajo siguen
# en core.COMMANDS; un test vigila que ambos lados coincidan.


@dataclass(frozen=True)
class Tool:
    label: str  # cómo se nombra: «/ver», «+ texto», «Imagen a CSV»
    name: str = ""  # nombre del comando (sin «/»); vacío si no es un comando
    usage: str = ""  # cómo se escribe en la ayuda, si lleva argumento: «/ver [nota]»
    help: str | None = None  # línea de la ayuda (core.HELP); None = no aparece
    panel: str | None = None  # descripción en el panel de la web; None = no hay fila (ver abajo)
    menu: str | None = None  # descripción en el menú de Telegram; None = no aparece
    insert: str = ""  # lo que el panel escribe en el cuadro de texto al tocar la fila
    action: str = ""  # atajo especial del panel que no escribe texto (hoy: «csv»)
    aliases: tuple[str, ...] = ()  # otros nombres que el núcleo también entiende
    uses_ai: bool = False  # gasta tokens: el panel lo marca con «usa IA»
    writes: bool = False  # escribe en DATA_DIR o cambia el estado de la sesión
    links: tuple[str, ...] = ()  # pedidos completos que /?cmd=... puede ejecutar solos
    channel: str = ""  # si lo atiende un solo canal (no el núcleo): su nombre, p. ej. «telegram»


# El panel de la web muestra solo lo que no tiene otra puerta de entrada: lo que ya está en el
# menú de la izquierda (/ver, /archivos, /uso) o en los atajos sobre el cuadro de texto
# («Guardar nota») no lleva `panel`. Las que gastan tokens quedan, para poder marcarlas «usa IA».
TOOLS = (
    Tool(
        "+ texto",
        help="guardar una nota en inbox",
        insert="+ ",
        writes=True,
    ),
    Tool(
        "Imagen a CSV",
        panel="Pasar una imagen a una tabla: adjuntala y escribí «nombre: campos»",
        action="csv",
        uses_ai=True,
        writes=True,
    ),
    Tool(
        "/ver",
        name="ver",
        usage="/ver [nota]",
        help="leer una nota (por defecto inbox)",
        menu="Ver una nota (por defecto, inbox)",
        insert="/ver ",
        links=("/ver",),
    ),
    Tool(
        "/buscar",
        name="buscar",
        usage="/buscar palabra",
        help="buscar en las notas",
        panel="Buscar una palabra en tus notas",
        menu="Buscar texto en todas las notas",
        insert="/buscar ",
    ),
    Tool(
        "/archivos",
        name="archivos",
        usage="/archivos [texto]",
        help="listar lo guardado (los 10 más nuevos)",
        menu="Listar lo guardado (opcional: filtrar por nombre)",
        insert="/archivos ",
        links=("/archivos", "/archivos tickets"),
    ),
    Tool(
        "/resumir",
        name="resumir",
        usage="/resumir nombre",
        help="resumir una nota o archivo guardado",
        panel="Resumir una nota o archivo guardado",
        menu="Resumir una nota o archivo guardado",
        insert="/resumir ",
        uses_ai=True,
        writes=True,
    ),
    Tool(
        "/ticket",
        name="ticket",
        help="guardar la próxima imagen o PDF en tickets/ (mandala como archivo)",
        panel="Guardar la próxima imagen en tickets/",
        menu="Guardar la próxima imagen como ticket",
        insert="/ticket",
        writes=True,
    ),
    Tool(
        "/cancelar",
        name="cancelar",
        menu="Cancelar el /ticket en espera",
        writes=True,
    ),
    Tool(
        "/contexto",
        name="contexto",
        help="lo que guardaste desde que arrancó el bot",
        menu="Lo que guardaste desde que arrancó el bot",
    ),
    Tool(
        "/recordar",
        name="recordar",
        usage="/recordar texto",
        help="anotar un dato o preferencia tuya en la memoria",
        panel="Anotar un dato o preferencia tuya en la memoria",
        menu="Anotar algo en la memoria",
        insert="/recordar ",
        writes=True,
    ),
    Tool(
        "/memoria",
        name="memoria",
        help="ver lo que anotaste en la memoria",
        menu="Ver lo que anotaste en la memoria",
        insert="/memoria",
    ),
    Tool(
        "/olvidar",
        name="olvidar",
        usage="/olvidar número",
        help="borrar una línea de la memoria (pide confirmar)",
        panel="Borrar una línea de la memoria",
        menu="Borrar una línea de la memoria",
        insert="/olvidar ",
        writes=True,
    ),
    Tool(
        "/editar",
        name="editar",
        usage="/editar número texto",
        help="cambiar una línea de la memoria",
        panel="Cambiar el texto de una línea de la memoria",
        menu="Cambiar una línea de la memoria",
        insert="/editar ",
        writes=True,
    ),
    Tool(
        "/uso",
        name="uso",
        help="tokens y precio gastados (hoy y total)",
        menu="Tokens y precio gastados (hoy y total)",
        insert="/uso",
        aliases=("usage",),
        links=("/uso",),
    ),
    Tool(
        "/menu",
        name="menu",
        help="ajustes del bot, como la voz (con botones)",
        menu="Ajustes del bot (voz y más)",
        writes=True,
    ),
    Tool(
        "/nuevo",
        name="nuevo",
        menu="Empezar una conversación nueva (lo anterior queda guardado)",
        channel="telegram",
    ),
    Tool(
        "/ayuda",
        name="ayuda",
        menu="Cómo se usa el bot",
        aliases=("start",),
    ),
)


def help_lines() -> list[str]:
    """Una línea «uso → qué hace» por cada herramienta que figura en la ayuda."""
    return [f"{tool.usage or tool.label} → {tool.help}" for tool in TOOLS if tool.help]


def command_names() -> set[str]:
    """Todos los nombres de comando que el núcleo tiene que entender, alias incluidos.
    Los que atiende un solo canal (/nuevo en Telegram) no cuentan: el núcleo no los conoce."""
    names = set()
    for tool in TOOLS:
        if tool.name and not tool.channel:
            names.add(tool.name)
        if not tool.channel:
            names.update(tool.aliases)
    return names


def link_commands() -> list[str]:
    """Los pedidos que la web ejecuta sola al llegar por un enlace /?cmd=...: solo lecturas."""
    return [command for tool in TOOLS for command in tool.links]
