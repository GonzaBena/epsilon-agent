from dataclasses import dataclass
import html
import re
import unicodedata

# Del markdown del núcleo al HTML de Telegram. Es de ESTE canal (el núcleo no sabe que existe y otro canal puede
# traducir el mismo markdown a su manera): telegram.py lo usa al mandar y nada más. Telegram no dibuja tablas ni
# casi nada de markdown, así que acá se traduce lo que el núcleo y la IA escriben:
#   **negrita**, *cursiva*, `código`, ~~tachado~~, [texto](https://…), títulos, listas, citas y bloques de código;
#   tablas de 2 columnas -> líneas «**clave**: valor»; tablas angostas -> bloque monoespaciado alineado;
#   tablas anchas -> una «ficha» por fila (en el celular una tabla ancha se desarma).
# Todo texto que no es una etiqueta nuestra pasa por html.escape: lo que escriba un archivo, una página o el modelo
# nunca puede meter HTML. `to_html` parte el texto en varios mensajes SIN cortar una tabla ni una etiqueta por la
# mitad (Telegram no acepta más de 4096 caracteres). Si Telegram igual rechaza el HTML, telegram.py manda `plain_of`.
# Puro: sin Telegram, sin red, sin reloj.

LIMIT = 3800  # largo máximo de cada mensaje armado (Telegram admite 4096; queda margen para lo que agrega Telegram)
MAX_PRE_WIDTH = 38  # una tabla más ancha que esto (en caracteres) no cabe en una línea del celular: pasa a fichas
SHORT_FIRST_COLUMN = 4  # una primera columna así de corta («1», «N») es un número de fila: se junta con la segunda
GENERIC_HEADERS = {"dato", "valor", "campo", "clave", "detalle"}  # encabezados que no dicen nada: se omiten
EMPTY = "(vacío)"


@dataclass
class Block:
    """Un bloque del mensaje: sus unidades (líneas, filas, fichas) se pueden repartir en varios mensajes."""

    units: list[str]
    sep: str = "\n"
    opening: str = ""  # etiqueta que abre (y, en una tabla, su encabezado): se repite en cada mensaje
    closing: str = ""


# --- Texto en línea -----------------------------------------------------------------------------------

INLINE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<link>\[[^\]\n]+\]\(https?://[^\s)]+\))"
    r"|(?P<bold>\*\*(?=\S)[^\n]+?(?<=\S)\*\*)"
    r"|(?P<strike>~~(?=\S)[^\n]+?(?<=\S)~~)"
    r"|(?P<italic>(?<![\w*])\*(?=[^\s*])[^\n]+?(?<=[^\s*])\*(?![\w*])|(?<![\w_])_(?=[^\s_])[^\n]+?(?<=[^\s_])_(?![\w_]))"
)
LINK_PARTS = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def inline(text: str) -> str:
    """Un renglón de markdown -> HTML de Telegram. Lo que no se reconoce queda como texto (escapado)."""
    out, pos = [], 0
    for match in INLINE.finditer(text):
        out.append(escape(text[pos:match.start()]))
        token, kind = match.group(), match.lastgroup
        if kind == "code":
            out.append(f"<code>{escape(token[1:-1])}</code>")
        elif kind == "link":
            label, url = LINK_PARTS.fullmatch(token).groups()
            out.append(f'<a href="{html.escape(url, quote=True)}">{inline(label)}</a>')
        elif kind == "bold":
            out.append(f"<b>{inline(token[2:-2])}</b>")
        elif kind == "strike":
            out.append(f"<s>{inline(token[2:-2])}</s>")
        else:
            out.append(f"<i>{inline(token[1:-1])}</i>")
        pos = match.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def plain(text: str) -> str:
    """Un renglón de markdown sin sus marcas («**a** [b](u)» -> «a b»): para medir columnas y para lo que va en un bloque."""
    text = LINK_PARTS.sub(r"\1", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|~~(?=\S)(.+?)(?<=\S)~~", lambda m: m.group(1) or m.group(2), text)
    return re.sub(r"`([^`\n]+)`", r"\1", text).strip()


# --- Tablas ----------------------------------------------------------------------------------------------

SEPARATOR_ROW = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def split_cells(line: str) -> list[str]:
    line = line.strip()
    line = line.removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in line.split("|")]


def width(text: str) -> int:
    """Cuántas columnas ocupa en un bloque monoespaciado (los caracteres anchos, como algunos emojis, ocupan 2)."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad(text: str, size: int, right: bool) -> str:
    gap = " " * max(size - width(text), 0)
    return gap + text if right else text + gap


def table_block(header: list[str], aligns: list[bool], rows: list[list[str]]) -> Block:
    """Una tabla -> el bloque que mejor se lee en el celular. `aligns[i]` es True si la columna va a la derecha."""
    count = len(header)
    rows = [(row + [""] * count)[:count] for row in rows]
    if count == 2:
        units = [f"<b>{escape(plain(key))}</b>: {inline(value)}" for key, value in rows]
        if not all(plain(h).lower() in GENERIC_HEADERS for h in header):
            units.insert(0, f"<i>{escape(plain(header[0]))} · {escape(plain(header[1]))}</i>")
        return Block(units or [EMPTY], "\n")

    names = [plain(h) for h in header]
    cells = [[plain(c) for c in row] for row in rows]
    sizes = [max([width(names[i]), *(width(row[i]) for row in cells)]) for i in range(count)]
    if sum(sizes) + 2 * (count - 1) <= MAX_PRE_WIDTH:
        def line(values: list[str]) -> str:
            return escape("  ".join(pad(v, sizes[i], aligns[i]) for i, v in enumerate(values)).rstrip())

        rule = "─" * (sum(sizes) + 2 * (count - 1))
        return Block([line(row) for row in cells] or [""], "\n", f"<pre>{line(names)}\n{rule}\n", "</pre>")

    # Ancha: una ficha por fila. Si la primera columna es un número de fila, va junto con la segunda: «2 · kitty».
    merge = count > 2 and all(width(row[0]) <= SHORT_FIRST_COLUMN for row in cells)
    head = 2 if merge else 1
    cards = []
    for n, row in enumerate(cells):
        title = " · ".join(value for value in row[:head] if value)
        lines = [f"<b>{escape(title)}</b>"] if title else []
        lines += [f"{escape(names[i])}: {inline(rows[n][i])}" for i in range(head, count) if row[i]]  # con formato: viene de `rows`
        cards.append("\n".join(lines))
    return Block(cards or [EMPTY], "\n\n")


# --- Bloques ---------------------------------------------------------------------------------------------

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
CHECKBOX = re.compile(r"^\[([ xX])\]\s+(.*)$")
MAX_SOURCE_LINE = 1500  # una línea más larga se parte antes de dar formato (así nunca se corta una etiqueta)


def split_long(line: str) -> list[str]:
    parts = []
    while len(line) > MAX_SOURCE_LINE:
        cut = line.rfind(" ", 0, MAX_SOURCE_LINE)
        cut = cut if cut > 0 else MAX_SOURCE_LINE
        parts.append(line[:cut])
        line = line[cut:].lstrip()
    return parts + [line]


def list_item(line: str) -> str | None:
    bullet, numbered = BULLET.match(line), NUMBERED.match(line)
    if bullet:
        indent, text = "  " * (len(bullet.group(1)) // 2), bullet.group(2)
        box = CHECKBOX.match(text)
        marker, text = ("☑ " if box.group(1) in "xX" else "☐ ", box.group(2)) if box else ("• ", text)
        return indent + marker + inline(text)
    if numbered:
        return "  " * (len(numbered.group(1)) // 2) + f"{numbered.group(2)}. " + inline(numbered.group(3))
    return None


def parse(text: str) -> list[Block]:
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        nonlocal paragraph, items
        if paragraph:
            blocks.append(Block([inline(part) for line in paragraph for part in split_long(line)], "\n"))
        if items:
            blocks.append(Block(items, "\n"))
        paragraph, items = [], []

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
        elif FENCE.match(line):  # bloque de código, hasta el cierre (o el final)
            flush()
            fence, code = FENCE.match(line).group(1), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(escape(lines[i]))
                i += 1
            blocks.append(Block(code or [""], "\n", "<pre>", "</pre>"))
        elif "|" in line and i + 1 < len(lines) and SEPARATOR_ROW.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            header = split_cells(line)
            aligns = [cell.strip().endswith(":") for cell in split_cells(lines[i + 1])]
            aligns += [False] * (len(header) - len(aligns))
            rows, i = [], i + 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(split_cells(lines[i]))
                i += 1
            blocks.append(table_block(header, aligns, rows))
            continue
        elif RULE.match(line):
            flush()
            blocks.append(Block(["──────────"]))
        elif HEADING.match(line):
            flush()
            blocks.append(Block([f"<b>{inline(HEADING.match(line).group(1).replace('**', ''))}</b>"]))
        elif QUOTE.match(line):
            flush()
            quote = []
            while i < len(lines) and QUOTE.match(lines[i]):
                quote.append(inline(QUOTE.match(lines[i]).group(1)))
                i += 1
            blocks.append(Block(quote, "\n", "<blockquote>", "</blockquote>"))
            continue
        elif (item := list_item(line)) is not None:
            if paragraph:
                flush()
            items.append(item)
        else:
            if items:
                flush()
            paragraph.append(line)
        i += 1
    flush()
    return blocks


# --- Armado de los mensajes ------------------------------------------------------------------------------


def fit(unit: str, room: int) -> list[str]:
    """Una unidad que no cabe se parte por sus líneas (cada una es HTML completo); una línea suelta demasiado larga se corta."""
    if len(unit) <= room:
        return [unit]
    parts = []
    for line in unit.split("\n"):
        parts += [line[n:n + room] for n in range(0, len(line), room)] or [""]
    return parts


def pieces(block: Block, limit: int) -> list[str]:
    """El bloque como uno o más trozos que caben, cada uno con su etiqueta de apertura y de cierre."""
    room = limit - len(block.opening) - len(block.closing)
    out, group, size = [], [], 0
    for unit in block.units:
        for part in fit(unit, room):
            extra = len(part) + (len(block.sep) if group else 0)
            if group and size + extra > room:
                out.append(block.opening + block.sep.join(group) + block.closing)
                group, size, extra = [], 0, len(part)
            group.append(part)
            size += extra
    if group:
        out.append(block.opening + block.sep.join(group) + block.closing)
    return out


def to_html(text: str) -> list[str]:
    """El markdown del núcleo como uno o más mensajes de Telegram (HTML), cada uno de a lo sumo LIMIT caracteres.
    Se reparte por bloques: una tabla o una etiqueta nunca queda cortada a la mitad."""
    messages: list[str] = []
    current = ""
    for block in parse(text):
        for piece in pieces(block, LIMIT):
            if current and len(current) + 2 + len(piece) > LIMIT:
                messages.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        messages.append(current)
    return messages or [EMPTY]


def plain_of(message: str) -> str:
    """Un mensaje armado por `to_html` como texto plano legible (sin etiquetas): lo que se manda si Telegram rechaza el HTML."""
    message = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2 (\1)", message, flags=re.S)
    return html.unescape(re.sub(r"</?(?:b|i|s|u|code|pre|blockquote|a)\b[^>]*>", "", message)) or EMPTY
