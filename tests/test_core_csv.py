import pytest

from epsilon import core
from epsilon.data import notes

KEY = "AIzaSyD-ejemplo_de_clave_larga_QzXw"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    (tmp_path / "gastos.csv").write_text(
        "fecha,comercio,total\n2026-10-01,Coto,1.500\n2026-10-02,Kiosco,300\n", encoding="utf-8"
    )
    (tmp_path / "pedidos").mkdir()
    (tmp_path / "pedidos" / "pedidos-2026.csv").write_text("nombre,precio\nana,10\n", encoding="utf-8")
    (tmp_path / "pedidos" / "pedidos-2025.csv").write_text("nombre,precio\nluis,20\n", encoding="utf-8")
    (tmp_path / "inbox.md").write_text("fecha,comercio\n", encoding="utf-8")
    return tmp_path


def say(text):
    return core.handle(core.Message(text=text)).text


def test_only_the_name_shows_the_columns_and_examples():
    text = say("/csv gastos")

    assert "2 filas" in text and "Columnas: fecha (fecha), comercio (texto), total (número)" in text


def test_filters_and_operations():
    assert say("/csv gastos total>1000").startswith("1 filas")
    assert say("/csv gastos | suma total") == "Suma de total: **1800** (2 filas)"
    assert say("/csv gastos comercio ~ coto | suma total") == "Suma de total: **1500** (1 filas)"


def test_the_name_can_have_an_extension_a_folder_or_quotes():
    assert "2 filas" in say("/csv gastos.csv")
    assert "1 filas" in say("/csv pedidos/pedidos-2026.csv")
    assert "1 filas" in say('/csv "pedidos-2026"')


def test_several_matches_are_listed_and_not_guessed():
    text = say("/csv pedidos")

    assert "varias coincidencias" in text and "pedidos/pedidos-2025.csv" in text and "pedidos/pedidos-2026.csv" in text


def test_only_csv_files_are_queried():
    assert "Todavía no hay" not in say("/csv inbox") and "No encontré ningún archivo guardado" in say("/csv inbox")


def test_no_name_a_missing_file_and_a_bad_query_explain_themselves():
    assert say("/csv").startswith("Uso: /csv")
    assert "No encontré ningún archivo guardado con «zzz»" in say("/csv zzz")
    assert "No existe la columna «precio»" in say("/csv gastos precio>1")
    assert "Hay comillas sin cerrar" in say('/csv gastos comercio~"coto')


def test_nothing_outside_the_data_folder_can_be_read():
    assert "No encontré" in say("/csv ../../etc/passwd")
    assert "No encontré" in say("/csv /etc/passwd")


def test_a_key_inside_the_file_is_redacted(data_dir):
    (data_dir / "claves.csv").write_text(f"nombre,clave\nana,{KEY}\n", encoding="utf-8")

    assert KEY not in say("/csv claves")


def test_an_unreadable_file_gives_a_plain_message(data_dir):
    (data_dir / "roto.csv").write_bytes(b"")

    assert "vacío" in say("/csv roto")


def test_the_ai_tool_takes_the_same_argument_even_with_the_command_copied():
    assert core.AI_RUNNERS["csv"]("gastos | suma total").text == "Suma de total: **1800** (2 filas)"
    assert core.AI_RUNNERS["csv"]("/csv gastos | suma total").text == "Suma de total: **1800** (2 filas)"
