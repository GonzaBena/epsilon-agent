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
    ai: str = ""  # si la IA la puede usar: «read», «write» o «delete» (el tipo); vacío = la IA no la ve
    ai_name: str = ""  # nombre para el modelo cuando no tiene `name` (p. ej. «nota»)
    ai_help: str = ""  # lo que el modelo lee de la herramienta, si no hay `help` ni `panel` (solo para la IA)
    ai_arg: str = ""  # qué va en su argumento, dicho sin la sintaxis del comando («+», «/recordar»); si falta, `usage`
    ai_default: str = ""  # estado inicial del permiso, si no es el que sale del tipo (p. ej. «deny»)


# El panel de la web muestra solo lo que no tiene otra puerta de entrada: lo que ya está en el
# menú de la izquierda (/ver, /archivos, /uso) o en los atajos sobre el cuadro de texto
# («Guardar nota») no lleva `panel`. Las que gastan tokens quedan, para poder marcarlas «usa IA».
TOOLS = (
    Tool(
        "+ texto",
        help="guardar una nota en inbox",
        insert="+ ",
        writes=True,
        ai="write",
        ai_name="nota",
        ai_help="guardar una nota rápida en inbox, SIN nombre. Si el usuario dice cómo se llama la nota, usá nota_nombrada",
        ai_arg="solo el texto de la nota, sin prefijos",
    ),
    Tool(
        "Nota con nombre",
        usage="nombre de la nota en la primera línea y el texto en las siguientes",
        ai_help="guardar una nota CON nombre propio, cuando el usuario dice cómo se llama o a qué nota va (se crea si no existe; si existe, agrega al final)",
        ai_arg="primera línea: el nombre de la nota; desde la segunda línea: el texto",
        writes=True,
        ai="write",
        ai_name="nota_nombrada",
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
        ai="read",
        ai_arg="nombre de la nota; vacío para inbox",
    ),
    Tool(
        "/buscar",
        name="buscar",
        usage="/buscar palabra",
        help="buscar en las notas",
        panel="Buscar una palabra en tus notas",
        menu="Buscar texto en todas las notas",
        insert="/buscar ",
        ai="read",
        ai_arg="la palabra a buscar",
    ),
    Tool(
        "/archivos",
        name="archivos",
        usage="/archivos [texto]",
        help="listar lo guardado (los 10 más nuevos)",
        menu="Listar lo guardado (opcional: filtrar por nombre)",
        insert="/archivos ",
        links=("/archivos", "/archivos tickets"),
        ai="read",
        ai_arg="texto para filtrar por nombre; puede ir vacío",
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
        ai="write",
        ai_arg="nombre de la nota o archivo",
        ai_default="deny",
    ),
    Tool(
        "/ia",
        name="ia",
        usage="/ia pregunta",
        help="hacerle una pregunta suelta a la IA (sin el resto de la charla)",
        panel="Hacerle una pregunta suelta a la IA",
        menu="Preguntarle algo a la IA (una sola pregunta)",
        insert="/ia ",
        uses_ai=True,
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
        ai="write",
        ai_arg="solo el dato a recordar, sin prefijos",
    ),
    Tool(
        "/memoria",
        name="memoria",
        help="ver lo que anotaste en la memoria",
        menu="Ver lo que anotaste en la memoria",
        insert="/memoria",
        ai="read",
        ai_arg="no lleva argumento: mandalo vacío",
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
        ai="delete",
        ai_arg="el número de la línea",
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
        ai="write",
        ai_arg="el número de la línea y después el texto nuevo, p. ej. «3 mi gato se llama Tom»",
    ),
    Tool(
        "/fecha",
        name="fecha",
        help="fecha y hora de ahora",
        menu="Fecha y hora de ahora",
        insert="/fecha",
        aliases=("hora",),
        links=("/fecha",),
        ai="read",
        ai_arg="no lleva argumento: mandalo vacío",
    ),
    Tool(
        "/dolar",
        name="dolar",
        help="cotización del dólar hoy (oficial, blue, MEP…)",
        menu="Cotización del dólar hoy",
        insert="/dolar",
        links=("/dolar",),
        ai="read",
        ai_arg="no lleva argumento: mandalo vacío",
    ),
    Tool(
        "/clima",
        name="clima",
        usage="/clima [ciudad]",
        help="el clima ahora y los próximos días (por defecto, tu ciudad)",
        menu="El clima de tu ciudad (o de otra)",
        insert="/clima ",
        links=("/clima",),
        ai="read",
        ai_arg="el nombre de la ciudad, sin prefijos; vacío para la ciudad configurada",
    ),
    Tool(
        "/tareas",
        name="tareas",
        usage="/tareas [todas]",
        help="ver tus tareas pendientes (con «todas», también las hechas)",
        menu="Ver tus tareas pendientes",
        insert="/tareas",
        links=("/tareas",),
        ai="read",
        ai_arg="vacío para las pendientes, o «todas» para incluir las hechas",
    ),
    Tool(
        "/todo",
        name="todo",
        usage="/todo texto",
        help="agregar una tarea pendiente",
        panel="Agregar una tarea pendiente",
        menu="Agregar una tarea pendiente",
        insert="/todo ",
        writes=True,
        ai="write",
        ai_arg="solo el texto de la tarea, sin prefijos",
    ),
    Tool(
        "/hecho",
        name="hecho",
        usage="/hecho número",
        help="marcar una tarea como hecha (el número sale de /tareas)",
        panel="Marcar una tarea como hecha",
        menu="Marcar una tarea como hecha",
        insert="/hecho ",
        writes=True,
        ai="write",
        ai_arg="el número de la tarea pendiente, como aparece en tareas",
    ),
    Tool(
        "/enviar",
        name="enviar",
        usage="/enviar nombre",
        help="mandarte por Telegram una nota o archivo guardado (en la web ya está en la biblioteca)",
        menu="Mandarme una nota o archivo guardado",
        insert="/enviar ",
        ai="read",
        ai_arg="el nombre del archivo o parte de él, como aparece en archivos (puede incluir la carpeta)",
    ),
    Tool(
        "/csv",
        name="csv",
        usage="/csv nombre [filtros]",
        help="consultar un CSV guardado: filtrar filas y, con «| suma columna», calcular (sin IA)",
        panel="Consultar un CSV guardado: /csv nombre precio>100 | suma precio",
        menu="Consultar un CSV guardado (filtrar, sumar…)",
        insert="/csv ",
        ai="read",
        ai_help=(
            "consultar un CSV guardado SIN leerlo entero: filtra filas y calcula. Primero llamala solo con el nombre "
            "para ver las columnas. Ejemplos: «gastos fecha>=2026-10-01 comercio~coto | suma total», "
            "«gastos | suma total por comercio», «gastos | ordenar total desc»"
        ),
        ai_arg=(
            "nombre del CSV, después filtros columna<op>valor (op: = != > >= < <= ~ que es «contiene»; valores con "
            "espacios entre comillas) y, si querés un cálculo, «| suma|promedio|min|max|cuenta|ordenar columna» "
            "(con «por columna» para agrupar). Sin filtros ni cálculo devuelve las columnas y ejemplos"
        ),
    ),
    Tool(
        "/sistema",
        name="sistema",
        usage="/sistema [qué]",
        help="estado de la máquina: disco, ram, uptime o servicio (sin opción, todo)",
        menu="Estado de la máquina (disco, memoria…)",
        insert="/sistema",
        ai="read",
        ai_default="ask",
        ai_arg="disco, ram, uptime o servicio; vacío para ver todo",
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


def uses_ai_command(name: str) -> bool:
    """¿Este comando (o alias) llama al modelo? Un nombre desconocido no."""
    return any(tool.uses_ai for tool in TOOLS if name == tool.name or name in tool.aliases)


def ai_id(tool: Tool) -> str:
    """El nombre con que el modelo conoce la herramienta."""
    return tool.ai_name or tool.name


def ai_tools() -> tuple[Tool, ...]:
    """Las herramientas que la IA puede usar, en el orden de TOOLS."""
    return tuple(tool for tool in TOOLS if tool.ai)
