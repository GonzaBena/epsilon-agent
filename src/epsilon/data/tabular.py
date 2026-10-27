import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import shlex
import unicodedata

from epsilon.settings import config

# Consultas simples sobre un CSV guardado: filtrar filas, sumar, promediar, agrupar, ordenar. Sin modelo y sin
# SQL: la consulta es una línea corta que un modelo chico puede escribir sin errores, y lo único que hace es
# leer el archivo. Sin canales ni núcleo: recibe una ruta que ya validó quien llama.
#
#   archivo [columna<op>valor ...] [| operación]
#   operadores: = != > >= < <= ~   (~ = «contiene», sin mayúsculas ni tildes)
#   operaciones: cuenta · suma C · promedio C · min C · max C · ordenar C [desc]  (+ «por C» para agrupar)
#
# Los valores con espacios van entre comillas: comercio~"mercado libre".

MAX_BYTES = 5 * 1024 * 1024  # un CSV de tickets pesa KB; más que esto no es para esta herramienta
MAX_GROUPS = 20
OPERATORS = (">=", "<=", "!=", "=", ">", "<", "~")
FILTER = re.compile(r"(?P<column>.+?)(?P<op>>=|<=|!=|=|>|<|~)(?P<value>.*)", re.DOTALL)
AGGREGATES = {"suma": "Suma", "promedio": "Promedio", "min": "Mínimo", "max": "Máximo"}
EXAMPLE = "/csv nombre precio>100 | suma precio"


@dataclass(frozen=True)
class Table:
    name: str
    columns: list[str]
    rows: list[dict[str, str]]


def _plain(text: str) -> str:
    """Minúsculas y sin tildes, para comparar sin importar cómo se escribió."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


def _number(text: str) -> float | None:
    """«1500», «$ 1.500», «1.234,56», «1,234.56» y «-3,5» son números; lo demás, no.
    Un punto con tres dígitos detrás («1.500») es separador de miles, como se escribe en Argentina."""
    t = text.strip().replace("$", "").replace(" ", "")
    if not re.fullmatch(r"-?[\d.,]+", t):
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
    elif re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _date(text: str) -> date | None:
    """«2026-10-21» o «21/10/2026» (día/mes/año)."""
    t = text.strip()
    found = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    year, month, day = found.groups() if found else (None, None, None)
    if not found:
        found = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
        day, month, year = found.groups() if found else (None, None, None)
    if not found:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _shown(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _cell(text: str) -> str:
    """Un valor listo para una tabla markdown: sin saltos de línea ni barras que la rompan."""
    return " ".join(text.split()).replace("|", "\\|")


def _table(columns: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(_cell(c) for c in columns) + " |\n|" + "---|" * len(columns)
    return head + "".join("\n| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)


def load(path: Path) -> Table:
    """Lee el CSV entero. ValueError con un mensaje claro si no se puede o es demasiado grande."""
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} pesa más de {MAX_BYTES // 1024 // 1024} MB; es demasiado para consultarlo.")
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        columns = [c.strip() for c in (reader.fieldnames or []) if c and c.strip()]
        if not columns:
            raise ValueError(f"{path.name} está vacío o no tiene encabezado.")
        rows = [{c: (row.get(c) or "").strip() for c in columns} for row in reader]
    return Table(path.name, columns, rows)


def describe(table: Table) -> str:
    """Las columnas (con el tipo que parecen tener), la cantidad de filas y tres ejemplos."""
    kinds = []
    for column in table.columns:
        values = [row[column] for row in table.rows if row[column]]
        if values and all(_date(v) for v in values):
            kinds.append("fecha")
        elif values and all(_number(v) is not None for v in values):
            kinds.append("número")
        else:
            kinds.append("texto")
    columns = ", ".join(f"{c} ({k})" for c, k in zip(table.columns, kinds))
    sample = _table(table.columns, [[row[c] for c in table.columns] for row in table.rows[:3]])
    return (
        f"**{table.name}**: {len(table.rows)} filas.\n\nColumnas: {columns}\n\nEjemplos:\n\n{sample}\n\n"
        f"Para filtrar o sumar: `{EXAMPLE}`"
    )


# --- La consulta ---------------------------------------------------------------


def _split_query(text: str) -> tuple[str, str]:
    """Separa «filtros | operación» en la primera barra que no esté entre comillas."""
    quote = None
    for index, char in enumerate(text):
        if quote:
            quote = None if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "|":
            return text[:index], text[index + 1 :]
    return text, ""


def split_name(args: str) -> tuple[str, str]:
    """(nombre del archivo, el resto de la consulta). El nombre puede ir entre comillas."""
    args = args.strip()
    if not args:
        raise ValueError(f"Uso: /csv nombre [filtros] [| operación]\nEjemplo: {EXAMPLE}")
    if args[0] in "\"'":
        end = args.find(args[0], 1)
        if end < 0:
            raise ValueError("Hay comillas sin cerrar.")
        return args[1:end], args[end + 1 :].strip()
    name, _, rest = args.partition(" ")
    return name, rest.strip()


def _column(table: Table, wanted: str) -> str:
    plain = {_plain(c): c for c in table.columns}
    found = plain.get(_plain(wanted))
    if not found:
        raise ValueError(f"No existe la columna «{wanted.strip()}». Columnas: {', '.join(table.columns)}.")
    return found


def _tighten(text: str) -> str:
    """«precio > 100» pasa a «precio>100» (los modelos chicos separan así), sin tocar lo que está entre comillas."""
    parts = re.split(r"(\"[^\"]*\"|'[^']*')", text)
    return "".join(part if index % 2 else re.sub(r"\s*(>=|<=|!=|=|>|<|~)\s*", r"\1", part) for index, part in enumerate(parts))


def _filters(table: Table, text: str) -> list[tuple[str, str, str]]:
    text = _tighten(text)
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        raise ValueError("Hay comillas sin cerrar.") from None
    filters = []
    for token in tokens:
        found = FILTER.fullmatch(token)
        if not found:
            raise ValueError(f"No entiendo «{token}». Un filtro se escribe columna>valor (se puede = != > >= < <= ~).")
        column = _column(table, found["column"])
        op, value = found["op"], found["value"].strip()
        if op in (">", ">=", "<", "<=") and _number(value) is None and _date(value) is None:
            raise ValueError(f"Con «{op}» el valor tiene que ser un número o una fecha, no «{value}».")
        filters.append((column, op, value))
    return filters


def _matches(cell: str, op: str, wanted: str) -> bool:
    if op == "~":
        return _plain(wanted) in _plain(cell)
    cell_date, wanted_date = _date(cell), _date(wanted)
    cell_number, wanted_number = _number(cell), _number(wanted)
    if cell_date and wanted_date:
        left, right = cell_date, wanted_date
    elif cell_number is not None and wanted_number is not None and not wanted_date:
        left, right = cell_number, wanted_number
    elif op in ("=", "!="):
        left, right = _plain(cell), _plain(wanted)
    else:
        return False  # una celda que no es número ni fecha no entra en una comparación de orden
    return {"=": left == right, "!=": left != right, ">": left > right, ">=": left >= right,
            "<": left < right, "<=": left <= right}[op]


def _numbers(rows: list[dict[str, str]], column: str) -> list[float]:
    return [n for row in rows if (n := _number(row[column])) is not None]


def _aggregate(table: Table, rows: list[dict[str, str]], words: list[str]) -> str:
    kind = words[0]
    if kind == "cuenta":
        column = None
        rest = words[1:]
    else:
        if len(words) < 2:
            raise ValueError(f"«{kind}» necesita una columna. Ejemplo: {kind} {table.columns[-1]}")
        column = _column(table, words[1])
        rest = words[2:]
    group = None
    if rest:
        if len(rest) != 2 or _plain(rest[0]) != "por":
            raise ValueError("Después de la operación solo puede ir «por columna» para agrupar.")
        group = _column(table, rest[1])
    label = AGGREGATES.get(kind, "Cantidad")

    def value_of(subset: list[dict[str, str]]) -> str:
        if kind == "cuenta":
            return str(len(subset))
        numbers = _numbers(subset, column)
        if not numbers:
            return "—"
        result = {"suma": sum(numbers), "promedio": sum(numbers) / len(numbers), "min": min(numbers), "max": max(numbers)}[kind]
        return _shown(result)

    what = f"{label} de {column}" if column else label
    if not group:
        text = f"{what}: **{value_of(rows)}** ({len(rows)} filas)"
        if column and (skipped := len(rows) - len(_numbers(rows, column))):
            text += f". {skipped} sin número en «{column}» no se contaron."
        return text
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(row[group], []).append(row)
    ordered = sorted(groups.items(), key=lambda item: -(float(v) if (v := value_of(item[1])) != "—" else float("-inf")))
    shown = [[key or "(vacío)", value_of(subset), str(len(subset))] for key, subset in ordered[:MAX_GROUPS]]
    more = f"\n\n(y {len(ordered) - MAX_GROUPS} grupos más)" if len(ordered) > MAX_GROUPS else ""
    return f"{what} por {group}:\n\n" + _table([group, label, "Filas"], shown) + more


def _sorted(table: Table, rows: list[dict[str, str]], words: list[str]) -> list[dict[str, str]]:
    if len(words) < 2 or len(words) > 3 or (len(words) == 3 and _plain(words[2]) not in ("desc", "asc")):
        raise ValueError("Se escribe: ordenar columna [desc]")
    column = _column(table, words[1])
    descending = len(words) == 3 and _plain(words[2]) == "desc"

    def key(row):
        text = row[column]
        if (d := _date(text)) is not None:
            return (0, d.toordinal(), "")
        if (n := _number(text)) is not None:
            return (0, n, "")
        return (1, 0, _plain(text))  # lo que no es número ni fecha, al final y por orden alfabético

    comparable = [r for r in rows if _number(r[column]) is not None or _date(r[column])]
    others = [r for r in rows if not (_number(r[column]) is not None or _date(r[column]))]
    return sorted(comparable, key=key, reverse=descending) + sorted(others, key=key)


def query(table: Table, text: str) -> str:
    """Corre «[filtros] [| operación]» sobre la tabla y devuelve el resultado en markdown.
    ValueError con un mensaje claro (para el usuario o el modelo) si la consulta no se entiende."""
    filter_text, operation = _split_query(text)
    rows = table.rows
    for column, op, wanted in _filters(table, filter_text):
        rows = [row for row in rows if _matches(row[column], op, wanted)]
    words = operation.split()
    if words and _plain(words[0]) in ("cuenta", *AGGREGATES):
        words[0] = _plain(words[0])
        return _aggregate(table, rows, words)
    if words and _plain(words[0]) == "ordenar":
        rows = _sorted(table, rows, words)
    elif words:
        raise ValueError(f"No conozco la operación «{words[0]}». Hay: cuenta, suma, promedio, min, max, ordenar.")
    if not rows:
        return "Ninguna fila cumple eso."
    shown = config.number("FILES_TABLE_MAX_ROWS")  # filas que se muestran de una consulta
    body = _table(table.columns, [[row[c] for c in table.columns] for row in rows[:shown]])
    header = f"{len(rows)} filas" + (f" (muestro las primeras {shown})" if len(rows) > shown else "")
    return f"{header}:\n\n{body}"
