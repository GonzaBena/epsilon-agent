import pytest

from epsilon.data import changes, notes


@pytest.fixture
def todo(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    path = tmp_path / "todo.md"
    path.write_text("- [ ] pagar internet\n- [ ] comprar café\n- [ ] llamar a Ana\n", encoding="utf-8")
    return path


def edit(n, new):
    """Lo mismo que hace core._edit_note: editar y anotar el cambio."""
    before, prev, nxt = notes.edit_line("todo", n, new)
    return changes.record("todo", n, before, new, prev, nxt)


def text(path):
    return path.read_text(encoding="utf-8")


# --- La verificación (pura) -------------------------------------------------------------------


CHANGE = {"line": 2, "before": "b", "after": "B"}
DELETE = {"line": 2, "before": "b", "after": "", "prev": "a", "next": "c"}


@pytest.mark.parametrize(
    "lines, change, expected",
    [
        (["a", "B", "c"], CHANGE, (1, False)),  # sigue en su lugar
        (["nueva", "a", "B", "c"], CHANGE, (2, False)),  # se movió: está en un único otro lugar
        (["a", "otra", "c"], CHANGE, "Esa línea cambió"),  # cambió: no se toca
        (["B", "a", "B"], CHANGE, "Esa línea cambió"),  # dos candidatas y ninguna en su lugar: no se adivina
        (["a", "c"], DELETE, (1, True)),  # borrada: vuelve entre sus vecinas
        (["x", "a", "c"], DELETE, (2, True)),  # sus vecinas se movieron juntas
        (["a", "x", "c"], DELETE, (1, True)),  # una vecina sigue en su lugar: insertar no pisa nada
        (["z", "x", "y"], DELETE, "La nota cambió"),  # ninguna vecina en su lugar ni juntas en otro: no se adivina
        (["c"], {"line": 1, "before": "b", "after": "", "prev": None, "next": "c"}, (0, True)),  # era la primera
        (["a"], {"line": 2, "before": "b", "after": "", "prev": "a", "next": None}, (1, True)),  # era la última
    ],
)
def test_plan_undoes_only_when_it_is_safe(lines, change, expected):
    outcome = changes.plan(lines, change)
    if isinstance(expected, str):
        assert isinstance(outcome, str) and outcome.startswith(expected)
    else:
        assert outcome == expected


# --- Editar y deshacer -----------------------------------------------------------------------


def test_undo_puts_back_what_the_line_said(todo):
    change_id = edit(2, "- [x] comprar café")

    assert changes.undo(change_id) == "La línea 2 de todo volvió a decir «- [ ] comprar café»."
    assert text(todo) == "- [ ] pagar internet\n- [ ] comprar café\n- [ ] llamar a Ana\n"
    assert changes.get(change_id)["undone"] is True


def test_undo_puts_back_a_deleted_line_in_its_place(todo):
    change_id = edit(2, "")
    assert text(todo) == "- [ ] pagar internet\n- [ ] llamar a Ana\n"

    changes.undo(change_id)
    assert text(todo) == "- [ ] pagar internet\n- [ ] comprar café\n- [ ] llamar a Ana\n"


def test_undo_follows_a_line_that_moved(todo):
    change_id = edit(2, "- [x] comprar café")
    todo.write_text("- [ ] nueva arriba\n" + text(todo), encoding="utf-8")  # alguien agregó una línea arriba

    changes.undo(change_id)
    assert text(todo).splitlines()[2] == "- [ ] comprar café"


def test_undo_refuses_and_touches_nothing_if_the_line_changed_since(todo):
    change_id = edit(2, "- [x] comprar café")
    edit(2, "- [x] comprar café y leche")
    before = text(todo)

    with pytest.raises(ValueError, match="Esa línea cambió desde entonces"):
        changes.undo(change_id)
    assert text(todo) == before
    assert changes.preview(change_id)["can_undo"] is False


def test_several_edits_undo_newest_first(todo):
    first = edit(2, "- [x] comprar café")
    second = edit(2, "- [x] comprar café y leche")

    changes.undo(second)
    changes.undo(first)
    assert text(todo).splitlines()[1] == "- [ ] comprar café"


def test_an_empty_line_comes_back_as_an_empty_line(todo):
    todo.write_text("a\n\nc\n", encoding="utf-8")
    change_id = edit(2, "b")

    changes.undo(change_id)
    assert text(todo) == "a\n\nc\n"  # la línea vacía vuelve: no es lo mismo que borrarla


def test_undo_twice_or_on_a_missing_note_explains_why(todo):
    change_id = edit(1, "- [x] pagar internet")
    changes.undo(change_id)
    with pytest.raises(ValueError, match="Ya lo deshiciste"):
        changes.undo(change_id)

    other = edit(3, "- [x] llamar a Ana")
    todo.unlink()
    with pytest.raises(ValueError, match="ya no existe"):
        changes.undo(other)
    assert changes.preview(other)["reason"] == "La nota «todo» ya no existe."


def test_unknown_or_malformed_ids_do_not_exist(todo):
    assert changes.get("../../etc") is None and changes.get("20260101-000000-zzzz") is None
    with pytest.raises(LookupError):
        changes.undo("20260101-000000-abcd")


def test_the_log_only_grows(todo):
    change_id = edit(1, "- [x] pagar internet")
    changes.undo(change_id)

    lines = (todo.parent / changes.CHANGES_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and '"undo"' in lines[1]


def test_undoing_an_older_edit_first_does_not_block_a_newer_deletion(todo):
    # La línea 3 se borró cuando su vecina de arriba ya estaba editada; deshacer esa edición primero
    # cambia la vecina, pero la de abajo sigue en su lugar: el borrado vuelve igual.
    edited = edit(2, "- [x] comprar café")
    deleted = edit(3, "")

    changes.undo(edited)
    changes.undo(deleted)
    assert text(todo) == "- [ ] pagar internet\n- [ ] comprar café\n- [ ] llamar a Ana\n"
