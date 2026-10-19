from epsilon import notes


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
