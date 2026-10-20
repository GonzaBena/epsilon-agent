from epsilon import summarize


def test_summarize_note(tmp_path, monkeypatch):
    # tmp_path es una carpeta temporal que pytest crea y borra sola.
    note = tmp_path / "nota.md"
    note.write_text("comprar filamento", encoding="utf-8")

    sent = []  # acá anotamos lo que "se habría mandado" a Gemini

    def fake_generate(parts, prompt, max_tokens=2000):
        sent.append(parts)
        return "- resumen falso"

    # monkeypatch reemplaza llm.generate por la falsa SOLO durante este test.
    monkeypatch.setattr(summarize.llm, "generate", fake_generate)

    assert summarize.summarize(note) == "- resumen falso"
    assert sent[0] == ["comprar filamento"]  # las notas viajan como texto


def test_summarize_pdf_sends_bytes(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-falso")  # no hace falta un PDF real para esta prueba

    sent = []
    monkeypatch.setattr(
        summarize.llm,
        "generate",
        lambda parts, prompt, max_tokens=2000: sent.append(parts) or "ok",
    )

    summarize.summarize(pdf)
    assert sent[0][0].inline_data.mime_type == "application/pdf"
