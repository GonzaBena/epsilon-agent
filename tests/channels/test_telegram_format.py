import re

import pytest

from epsilon.channels import telegram_format as fmt

SISTEMA = (
    "**Sistema**\n\n| Dato | Valor |\n|---|---|\n| Disco | 258,8 GB libres de 445,1 GB (40 % usado) |\n"
    "| Carga (1, 5, 15 min) | 0.51 · 1.46 · 1.15 |"
)
DOLAR = "**Dólar hoy** (DolarApi)\n\n| Casa | Compra | Venta |\n|---|---:|---:|\n| Oficial | 1.380 | 1.430 |\n| Blue | 1.410 | 1.430 |"
VENTANAS = (
    "**Ventanas**\n\n| Espacio | App | Título |\n|---|---|---|\n| 1 | chromium | Cómo hacer café en cafetera italiana |\n"
    "| 2 | kitty (activa) | ◐ Control de PC y multimedia |"
)


def one(text):
    messages = fmt.to_html(text)
    assert len(messages) == 1, messages
    return messages[0]


def balanced(message):
    """Todas las etiquetas que abre un mensaje las cierra, en orden (si no, Telegram lo rechaza)."""
    stack = []
    for closing, name in re.findall(r"<(/?)(b|i|s|code|pre|blockquote|a)\b[^>]*>", message):
        if closing:
            assert stack and stack.pop() == name, message
        else:
            stack.append(name)
    return not stack


# --- texto en línea --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("**negrita**", "<b>negrita</b>"),
    ("*cursiva* y _otra_", "<i>cursiva</i> y <i>otra</i>"),
    ("~~tachado~~", "<s>tachado</s>"),
    ("usá `uv run pytest` ahora", "usá <code>uv run pytest</code> ahora"),
    ("**negrita con `código`**", "<b>negrita con <code>código</code></b>"),
    ("[Epsilon](https://example.com/a?b=1&c=2)", '<a href="https://example.com/a?b=1&amp;c=2">Epsilon</a>'),
])
def test_inline_formats(text, expected):
    assert one(text) == expected


@pytest.mark.parametrize("text", ["5 * 3 * 2", "mi_archivo_final.md", "2*3*4", "a ** b", "un asterisco * suelto", "__init__", "[sin enlace]", "[x](javascript:alert(1))"])
def test_what_only_looks_like_markdown_stays_text(text):
    assert "<b>" not in one(text) and "<i>" not in one(text) and "<a " not in one(text)


@pytest.mark.parametrize("text", ["<script>alert(1)</script>", "a < b & c > d", "<b onclick=x>hola</b>", '**<img src=x onerror=1>**', "`<b>`"])
def test_html_in_the_text_is_always_escaped(text):
    result = one(text)

    assert "<script" not in result and "<img" not in result and "onclick" not in result.replace("&lt;b onclick", "")
    assert balanced(result)


def test_unbalanced_markers_are_left_as_text_and_the_message_stays_valid():
    result = one("**abierta sin cerrar y `código sin cerrar y [enlace](http://x")

    assert balanced(result) and "**abierta" in result


# --- bloques -----------------------------------------------------------------------------------------------


def test_headings_lists_quotes_rules_and_code_blocks():
    text = "# Título\n\n- uno\n- dos con **negrita**\n  - anidado\n\n1. primero\n2. segundo\n\n> cita\n\n---\n\n```\nlinea <x>\nsegunda\n```"

    assert one(text) == (
        "<b>Título</b>\n\n• uno\n• dos con <b>negrita</b>\n  • anidado\n\n1. primero\n2. segundo\n\n"
        "<blockquote>cita</blockquote>\n\n──────────\n\n<pre>linea &lt;x&gt;\nsegunda</pre>"
    )


def test_checkboxes_become_boxes_so_the_todo_note_reads_well():
    assert one("- [ ] pagar internet\n- [x] comprar pan") == "☐ pagar internet\n☑ comprar pan"


def test_a_code_block_without_closing_runs_to_the_end_and_markdown_inside_is_not_formatted():
    assert one("```\n**no es negrita**\nsin cerrar") == "<pre>**no es negrita**\nsin cerrar</pre>"


def test_plain_lines_keep_their_line_breaks():
    assert one("uno\ndos\ntres") == "uno\ndos\ntres"


def test_empty_text_is_not_an_empty_message():
    assert fmt.to_html("") == ["(vacío)"] and fmt.to_html("  \n ") == ["(vacío)"]


# --- tablas ------------------------------------------------------------------------------------------------


def test_a_two_column_table_becomes_key_value_lines_and_drops_the_generic_header():
    assert one(SISTEMA) == (
        "<b>Sistema</b>\n\n<b>Disco</b>: 258,8 GB libres de 445,1 GB (40 % usado)\n<b>Carga (1, 5, 15 min)</b>: 0.51 · 1.46 · 1.15"
    )


def test_a_two_column_table_with_a_meaningful_header_keeps_it():
    result = one("| Comercio | Total |\n|---|---:|\n| Coto | 1200 |\n| Dia | 800 |")

    assert result == "<i>Comercio · Total</i>\n<b>Coto</b>: 1200\n<b>Dia</b>: 800"


def test_a_narrow_table_becomes_an_aligned_monospace_block_with_numbers_to_the_right():
    assert one(DOLAR) == (
        "<b>Dólar hoy</b> (DolarApi)\n\n<pre>Casa     Compra  Venta\n──────────────────────\n"
        "Oficial   1.380  1.430\nBlue      1.410  1.430</pre>"
    )


def test_a_wide_table_becomes_one_card_per_row_and_a_row_number_joins_the_next_column():
    assert one(VENTANAS) == (
        "<b>Ventanas</b>\n\n<b>1 · chromium</b>\nTítulo: Cómo hacer café en cafetera italiana\n\n"
        "<b>2 · kitty (activa)</b>\nTítulo: ◐ Control de PC y multimedia"
    )


def test_cards_without_a_row_number_use_the_first_column_as_the_title_and_label_the_rest():
    table = "| Día | Mín | Máx | Estado |\n|---|---:|---:|---|\n| jue 22 | 14° | 22° | Parcialmente nublado con chubascos |"

    assert one(table) == "<b>jue 22</b>\nMín: 14°\nMáx: 22°\nEstado: Parcialmente nublado con chubascos"


def test_empty_cells_are_skipped_in_cards_and_short_rows_are_padded():
    table = "| N | Qué | Próxima | Nota larga larga larga larga |\n|---|---|---|---|\n| 1 | algo largo para ensanchar | jue |\n"

    assert one(table) == "<b>1 · algo largo para ensanchar</b>\nPróxima: jue"


def test_markdown_inside_cells_is_formatted_in_cards_and_plain_in_monospace():
    cards = one("| N | Qué | Dónde de nuevo y más largo todavía |\n|---|---|---|\n| 1 | **importante** y largo largo | `ruta` |")
    mono = one("| A | B |\n|---|---|\n| x | y |\n\n| C | D | E |\n|---|---|---|\n| **n** | `m` | [l](http://u.com) |")

    assert "<b>1 · importante y largo largo</b>" in cards and "Dónde de nuevo y más largo todavía: <code>ruta</code>" in cards
    assert "<pre>C  D  E\n" in mono and "n  m  l" in mono  # sin marcas dentro del bloque


def test_a_pipe_in_text_that_is_not_a_table_is_just_text():
    assert one("elegí a | b | c\nlisto") == "elegí a | b | c\nlisto"


def test_a_table_without_rows_does_not_break():
    assert one("| A | B | C |\n|---|---|---|") == "<pre>A  B  C\n───────\n</pre>"


def test_table_cells_with_html_are_escaped_everywhere():
    result = one("| A | B | C |\n|---|---|---|\n| <b>x</b> | & | <i> |\n\n| Número | Texto de la ficha larguísimo | Otro más |\n|---|---|---|\n| 1 | <script> | y |")

    assert "<script" not in result and "&lt;script&gt;" in result and "&lt;b&gt;x&lt;/b&gt;" in result and balanced(result)


def test_double_width_characters_are_counted_so_the_columns_line_up():
    result = one("| Ícono | Nombre |  Z |\n|---|---|---|\n| 🙂 | uno | a |\n| ab | dos | b |")

    rows = re.search(r"<pre>(.*?)</pre>", result, re.S).group(1).split("\n")
    column = lambda row, word: fmt.width(row[:row.index(word)])  # dónde cae la palabra, contando lo que ocupa cada carácter
    assert column(rows[2], "uno") == column(rows[3], "dos")  # 🙂 ocupa 2 columnas, igual que «ab»
    assert rows[2].index("uno") != rows[3].index("dos")  # (por eso no alcanza con contar caracteres)


# --- largo --------------------------------------------------------------------------------------------------


def test_a_long_answer_is_split_into_messages_that_fit_without_losing_a_line():
    lines = [f"- línea número {n} con algo de texto para ocupar lugar" for n in range(400)]
    messages = fmt.to_html("\n".join(lines))

    assert len(messages) > 1 and all(len(m) <= fmt.LIMIT for m in messages)
    assert "\n".join(messages).count("• línea número") == 400


def test_a_long_table_is_split_by_rows_and_every_part_repeats_the_header():
    rows = "\n".join(f"| {n} | v{n} | w{n} |" for n in range(1000))
    messages = fmt.to_html(f"| Col | Aa | Bb |\n|---:|---|---|\n{rows}")

    assert len(messages) > 1 and all(len(m) <= fmt.LIMIT for m in messages)
    assert all(m.startswith("<pre>Col  Aa    Bb\n───────────────\n") and m.endswith("</pre>") for m in messages) and all(balanced(m) for m in messages)
    assert sum(len(re.findall(r"^\s*\d+  v", m, re.M)) for m in messages) == 1000


def test_long_cards_and_code_blocks_split_without_breaking_tags():
    cards = "\n".join(f"| {n} | nombre muy largo número {n} | descripción larga de la fila {n} |" for n in range(300))
    code = "\n".join(f"linea {n} de código <x>" for n in range(800))
    for text in (f"| N | Nombre | Descripción |\n|---|---|---|\n{cards}", f"```\n{code}\n```"):
        messages = fmt.to_html(text)
        assert len(messages) > 1 and all(len(m) <= fmt.LIMIT and balanced(m) for m in messages)


def test_a_single_huge_line_is_cut_before_formatting_so_tags_are_never_broken():
    messages = fmt.to_html("**" + "palabra " * 3000 + "**")

    assert len(messages) > 1 and all(len(m) <= 4096 and balanced(m) for m in messages)


# --- el texto plano de respaldo --------------------------------------------------------------------------------


def test_plain_of_removes_tags_and_unescapes_and_keeps_links_readable():
    message = one("**Hola** <mundo> [sitio](https://example.com) y `código`\n\n| A | B | C |\n|---|---|---|\n| x | y | z |")

    assert fmt.plain_of(message) == "Hola <mundo> sitio (https://example.com) y código\n\nA  B  C\n───────\nx  y  z"


def test_plain_of_an_empty_message_is_not_empty():
    assert fmt.plain_of("<b></b>") == "(vacío)"


@pytest.mark.parametrize("text", [SISTEMA, DOLAR, VENTANAS, "# a\n- b\n> c\n```\nd\n```", "**x** `y` _z_ ~~w~~ [l](http://u)", "a < b && c > d <3"])
def test_every_message_is_balanced_and_fits(text):
    for message in fmt.to_html(text):
        assert balanced(message) and len(message) <= fmt.LIMIT
