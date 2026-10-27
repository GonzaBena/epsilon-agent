import pytest

from epsilon.data import tabular

CSV = (
    "fecha,comercio,total\n"
    "2026-10-01,Súper Coto,1.500\n"
    "21/10/2026,Mercado Libre,\"$ 2.300,50\"\n"
    "2026-09-30,Súper Coto,800\n"
    "2026-10-15,Kiosco,abc\n"
    "2026-10-16,\"Carnicería | Don Pepe\",300\n"
)


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "gastos.csv"
    path.write_text(CSV, encoding="utf-8")
    return tabular.load(path)


def ask(table, text):
    return tabular.query(table, text)


# --- Números y fechas ---------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("1500", 1500), ("1.500", 1500), ("$ 2.300,50", 2300.5), ("1,234.56", 1234.56), ("-3,5", -3.5),
    ("0.125", 0.125), ("12.5", 12.5), ("1.234.567", 1234567), ("1,234,567", 1234567),
])
def test_numbers_are_read_the_way_they_are_written_in_argentina(text, expected):
    assert tabular._number(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "1e5", "nan", "2026-10-01", "12abc", "--1"])
def test_other_things_are_not_numbers(text):
    assert tabular._number(text) is None


def test_dates_in_iso_and_day_first_formats_and_impossible_ones():
    assert tabular._date("2026-10-21") == tabular._date("21/10/2026") == tabular._date("21-10-2026")
    assert tabular._date("31/02/2026") is None and tabular._date("hola") is None


# --- Cargar y describir -------------------------------------------------------


def test_describe_shows_columns_types_row_count_and_examples(table):
    text = tabular.describe(table)

    assert "5 filas" in text and "fecha (fecha)" in text and "total (texto)" in text
    assert "| 2026-10-01 | Súper Coto | 1.500 |" in text and "/csv" in text


def test_a_numeric_column_is_called_a_number(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("n,precio\nana,10\nluis,20,5\n", encoding="utf-8")

    assert "precio (número)" in tabular.describe(tabular.load(path))


def test_load_refuses_empty_and_huge_files(tmp_path, monkeypatch):
    empty = tmp_path / "vacio.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="vacío"):
        tabular.load(empty)

    big = tmp_path / "grande.csv"
    big.write_text("a\n1\n", encoding="utf-8")
    monkeypatch.setattr(tabular, "MAX_BYTES", 2)
    with pytest.raises(ValueError, match="demasiado"):
        tabular.load(big)


def test_a_bom_and_short_rows_do_not_break_loading(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes("﻿nombre,precio\nana\n".encode("utf-8"))

    assert tabular.load(path).rows == [{"nombre": "ana", "precio": ""}]


# --- Filtros ------------------------------------------------------------------


def test_equals_ignores_case_and_accents(table):
    assert ask(table, 'comercio="SUPER coto"').startswith("2 filas")


def test_contains_ignores_case_and_accents(table):
    result = ask(table, "comercio~super")

    assert result.startswith("2 filas") and "Mercado" not in result


def test_dates_compare_as_dates_whatever_the_format(table):
    result = ask(table, "fecha>=2026-10-01")

    assert result.startswith("4 filas") and "21/10/2026" in result and "2026-09-30" not in result


def test_numbers_compare_as_numbers_and_unreadable_cells_are_left_out(table):
    result = ask(table, "total>1000")

    assert result.startswith("2 filas") and "Kiosco" not in result and "800" not in result


def test_all_filters_must_hold(table):
    result = ask(table, "comercio~coto total<1000")

    assert result.startswith("1 filas") and "2026-09-30" in result and "2026-10-01" not in result


def test_not_equal(table):
    result = ask(table, 'comercio!="super coto"')

    assert result.startswith("3 filas") and "Kiosco" in result and "Coto" not in result


def test_spaces_around_the_operator_and_quoted_values_work(table):
    assert ask(table, "total > 1000") == ask(table, "total>1000")
    assert "Mercado Libre" in ask(table, 'comercio ~ "mercado libre"')


def test_column_names_ignore_case_and_accents(table):
    assert ask(table, "COMERCIO~kiosco").startswith("1 filas")


def test_no_match_says_so(table):
    assert ask(table, "total>999999") == "Ninguna fila cumple eso."


@pytest.mark.parametrize("query,message", [
    ("precio>1", "No existe la columna «precio». Columnas: fecha, comercio, total."),
    ("total", "No entiendo «total»"),
    ("comercio>coto", "tiene que ser un número o una fecha"),
    ('comercio~"coto', "comillas sin cerrar"),
    ("| promedia total", "No conozco la operación «promedia»"),
    ("| suma", "necesita una columna"),
    ("| suma total de comercio", "«por columna»"),
    ("| ordenar", "ordenar columna"),
])
def test_clear_errors(table, query, message):
    with pytest.raises(ValueError) as error:
        ask(table, query)

    assert message in str(error.value)


# --- Operaciones --------------------------------------------------------------


def test_sum_counts_only_numbers_and_says_what_it_skipped(table):
    assert ask(table, "| suma total") == "Suma de total: **4900.5** (5 filas). 1 sin número en «total» no se contaron."


def test_sum_of_the_filtered_rows(table):
    assert ask(table, "comercio~coto | suma total") == "Suma de total: **2300** (2 filas)"


def test_average_min_max_and_count(table):
    assert "**1225.12**" in ask(table, "| promedio total")
    assert "**300**" in ask(table, "| min total") and "**2300.5**" in ask(table, "| max total")
    assert ask(table, "comercio~coto | cuenta") == "Cantidad: **2** (2 filas)"


def test_group_by_sorts_from_the_biggest(table):
    result = ask(table, "| suma total por comercio")

    lines = result.splitlines()
    assert lines[0] == "Suma de total por comercio:"
    assert "| Mercado Libre | 2300.5 | 1 |" in lines and lines.index("| Mercado Libre | 2300.5 | 1 |") < lines.index("| Súper Coto | 2300 | 2 |")
    assert "| Kiosco | — | 1 |" in lines


def test_sort_by_a_column_both_ways_with_unreadable_values_last(table):
    desc = ask(table, "| ordenar total desc").splitlines()
    asc = ask(table, "| ordenar total").splitlines()

    assert "Mercado" in desc[4] and "Kiosco" in desc[-1]
    assert "Pepe" in asc[4] and "Kiosco" in asc[-1]


def test_sort_keeps_duplicate_rows(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("a,b\nx,1\nx,1\ny,\n", encoding="utf-8")

    assert tabular.query(tabular.load(path), "| ordenar b").startswith("3 filas")


def test_a_bar_inside_quotes_is_not_the_operation_separator(table):
    assert ask(table, 'comercio~"Carnicería | Don Pepe"').startswith("1 filas")


def test_cells_with_bars_do_not_break_the_markdown_table(table):
    assert "Carnicería \\| Don Pepe" in ask(table, "comercio~carniceria")


def test_only_the_first_rows_are_shown(tmp_path, monkeypatch):
    path = tmp_path / "n.csv"
    path.write_text("n\n" + "\n".join(str(i) for i in range(50)) + "\n", encoding="utf-8")
    monkeypatch.setenv("FILES_TABLE_MAX_ROWS", "5")

    result = tabular.query(tabular.load(path), "n>=0")

    assert result.startswith("50 filas (muestro las primeras 5):") and result.count("\n| ") == 6  # el encabezado y 5 filas


# --- El nombre del archivo ----------------------------------------------------


@pytest.mark.parametrize("args,expected", [
    ("gastos fecha>1", ("gastos", "fecha>1")),
    ('"mi gasto" a>1', ("mi gasto", "a>1")),
    ("gastos", ("gastos", "")),
    ("tickets/gastos | cuenta", ("tickets/gastos", "| cuenta")),
])
def test_split_name(args, expected):
    assert tabular.split_name(args) == expected


def test_split_name_needs_a_name_and_closed_quotes():
    with pytest.raises(ValueError, match="Uso"):
        tabular.split_name("  ")
    with pytest.raises(ValueError, match="comillas"):
        tabular.split_name('"abierta a>1')
