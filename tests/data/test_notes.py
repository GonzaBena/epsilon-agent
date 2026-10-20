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
