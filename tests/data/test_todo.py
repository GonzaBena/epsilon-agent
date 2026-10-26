import pytest

from epsilon import core
from epsilon.core import Message
from epsilon.data import notes, todo

KEY = "AIzaSyD-ejemplo_de_clave_larga_QzXw"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    return tmp_path


def say(text):
    return core.handle(Message(text=text, session_id="test-todo")).text


def test_add_appends_checkbox_lines_with_the_date_and_never_overwrites(data_dir):
    todo.add("Comprar café")
    todo.add("Pagar internet")

    lines = (data_dir / "todo.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("- [ ] Comprar café (creado: 20") and lines[1].startswith("- [ ] Pagar internet")


def test_pending_numbers_skip_the_finished_ones():
    for text in ("uno", "dos", "tres"):
        todo.add(text)

    todo.finish(1)

    assert [t.text for t in todo.pending()] == ["dos", "tres"]
    assert [t.text for t in todo.completed()] == ["uno"]
    todo.finish(2)  # ahora «tres» es la 2
    assert [t.text for t in todo.completed()] == ["uno", "tres"]


def test_finish_marks_the_line_and_keeps_everything_else(data_dir):
    (data_dir / "todo.md").write_text("# Mis cosas\n- [ ] a mano (creado: 2026-01-01)\nnota suelta\n", encoding="utf-8")
    todo.add("otra")

    task = todo.finish(1)

    lines = (data_dir / "todo.md").read_text(encoding="utf-8").splitlines()
    assert task.text == "a mano"
    assert lines[0] == "# Mis cosas" and lines[2] == "nota suelta"
    assert lines[1].startswith("- [x] a mano (completado: 20")
    assert [t.text for t in todo.pending()] == ["otra"]
    assert not list(data_dir.glob("*.tmp"))


@pytest.mark.parametrize("n", [0, 3, -1])
def test_finish_rejects_numbers_out_of_range(n):
    todo.add("uno")
    todo.add("dos")

    with pytest.raises(ValueError, match="hay 2"):
        todo.finish(n)


def test_add_rejects_empty_and_long_text():
    with pytest.raises(ValueError):
        todo.add("   ")
    with pytest.raises(ValueError, match="muy larga"):
        todo.add("x" * 301)


def test_add_joins_lines_and_redacts_keys(data_dir):
    todo.add(f"llamar\na Ana {KEY}")

    saved = (data_dir / "todo.md").read_text(encoding="utf-8")
    assert KEY not in saved and len(saved.splitlines()) == 1


def test_a_task_that_looks_like_the_date_suffix_still_reads_back_whole():
    todo.add("revisar (creado: 2020-01-01)")

    assert [t.text for t in todo.pending()] == ["revisar (creado : 2020-01-01)"]


def test_tareas_with_text_adds_and_confirms_with_the_pending_count():
    assert say("/tareas Comprar café") == "Tarea agregada (1 pendiente): Comprar café"
    assert say("/tareas Pagar internet") == "Tarea agregada (2 pendientes): Pagar internet"
    assert say("/tareas") == "1. Comprar café\n2. Pagar internet"


def test_tareas_lists_numbered_pending_and_todas_adds_the_done():
    assert "No tenés tareas pendientes" in say("/tareas")
    say("/tareas uno")
    say("/tareas dos")
    assert say("/hecho 1") == "Hecha: uno"

    assert say("/tareas") == "1. dos"
    assert say("/tareas todas") == "1. dos\n\nHechas:\n- ✓ uno"


def test_hecho_explains_bad_input():
    say("/tareas uno")

    assert "Uso" in say("/hecho")
    assert "Uso" in say("/hecho uno")
    assert "No hay una tarea pendiente 5" in say("/hecho 5")
