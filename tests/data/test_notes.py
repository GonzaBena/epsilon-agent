import pytest

from epsilon.data import notes


def test_guardar_archivo_no_pisa(tmp_path, monkeypatch):
    # Apuntamos las notas a una carpeta temporal para no ensuciar la tuya.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    a = notes.save_file("../../x.pdf", b"uno")
    b = notes.save_file("../../x.pdf", b"dos")

    assert a != b  # el segundo no pisó al primero
    assert a.read_bytes() == b"uno"
    assert a.is_relative_to(tmp_path)  # y ambos quedaron dentro de la carpeta


def test_save_csv_writes_header_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    fields = ["fecha", "total"]

    notes.save_csv("gastos", fields, [{"fecha": "2026-01-01", "total": "10"}])
    notes.save_csv("gastos", fields, [{"fecha": "2026-01-02", "total": "20"}])

    lines = (tmp_path / "gastos.csv").read_text(encoding="utf-8").splitlines()
    # Un solo encabezado y las dos filas, en orden: nada se pisó.
    assert lines == ["fecha,total", "2026-01-01,10", "2026-01-02,20"]


def make_files(tmp_path):
    """Crea archivos con fechas distintas: el número más alto es el más nuevo."""
    import os

    for age, rel in enumerate(
        ["viejo.md", "files/foto.png", "gastos.csv", "summarizes/viejo.md", "usage.jsonl"]
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        os.utime(p, (1_000_000 + age, 1_000_000 + age))


def test_list_files_newest_first_without_summaries_or_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    make_files(tmp_path)

    found, total = notes.list_files()

    names = [str(p.relative_to(tmp_path)) for p in found]
    # El CSV incluido, lo más nuevo primero; sin resúmenes ni el registro de uso.
    assert names == ["gastos.csv", "files/foto.png", "viejo.md"]
    assert total == 3


def test_list_files_limit_and_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    make_files(tmp_path)

    found, total = notes.list_files(limit=2)
    assert len(found) == 2 and total == 3  # corta a 2 pero informa el total

    found, total = notes.list_files("foto")
    assert [p.name for p in found] == ["foto.png"] and total == 1


def test_save_file_in_custom_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    path = notes.save_file("t.jpg", b"x", folder="Tickets")

    assert path.parent == tmp_path / "tickets"  # carpeta aparte, en minúsculas
    assert path.read_bytes() == b"x"


def test_save_file_folder_cannot_escape_or_use_summaries(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    a = notes.save_file("a.jpg", b"1", folder="../../etc")
    b = notes.save_file("b.jpg", b"2", folder="summarizes")
    c = notes.save_file("c.jpg", b"3", folder="   ")

    assert a.is_relative_to(tmp_path)  # el "../" no sale de DATA_DIR
    assert b.parent == tmp_path / "files"  # carpeta reservada -> files/
    assert c.parent == tmp_path / "files"  # vacío -> files/


def test_clean_note_name_keeps_only_safe_characters():
    assert notes.clean_note_name("  Compras Casa ") == "compras_casa"
    assert notes.clean_note_name("../../etc/passwd") == "etc_passwd"
    assert notes.clean_note_name("///") == ""


# --- edit_note: la única forma de pisar una nota ------------------------------------------


def make_note(tmp_path, monkeypatch, text="- [ ] pagar internet\n- [ ] comprar café\n"):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    (tmp_path / "todo.md").write_text(text, encoding="utf-8")
    return tmp_path / "todo.md"


def test_edit_note_changes_only_that_fragment_and_keeps_the_previous_version(tmp_path, monkeypatch):
    path = make_note(tmp_path, monkeypatch)

    assert notes.edit_note("todo", "- [ ] pagar internet", "- [x] pagar internet") == "Editado todo.md"

    assert path.read_text(encoding="utf-8") == "- [x] pagar internet\n- [ ] comprar café\n"
    assert (tmp_path / "todo.md.bak").read_text(encoding="utf-8") == "- [ ] pagar internet\n- [ ] comprar café\n"
    assert not (tmp_path / "todo.md.tmp").exists()  # el temporal no queda


def test_edit_note_with_empty_new_text_deletes_the_fragment(tmp_path, monkeypatch):
    path = make_note(tmp_path, monkeypatch)

    notes.edit_note("todo", "- [ ] comprar café\n", "")

    assert path.read_text(encoding="utf-8") == "- [ ] pagar internet\n"


@pytest.mark.parametrize(
    "old, message",
    [("- [ ] inventada", "No encontré"), ("- [ ] ", "2 veces"), ("", "Falta el texto")],
)
def test_edit_note_refuses_and_touches_nothing_when_the_fragment_is_missing_ambiguous_or_empty(tmp_path, monkeypatch, old, message):
    path = make_note(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        notes.edit_note("todo", old, "x")

    assert path.read_text(encoding="utf-8") == before and not (tmp_path / "todo.md.bak").exists()


def test_edit_note_never_creates_a_note_and_stays_inside_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    with pytest.raises(ValueError, match="No existe"):
        notes.edit_note("nueva", "a", "b")
    with pytest.raises(ValueError, match="fuera"):
        notes.edit_note("../../etc/passwd", "a", "b")

    assert list(tmp_path.iterdir()) == []
