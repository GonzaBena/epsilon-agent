from epsilon.ai import extract


def fake_llm(monkeypatch, answer):
    """Reemplaza llm.generate y anota con qué se lo llamó. No gasta tokens."""
    calls = []

    def fake_generate(parts, prompt, max_tokens=2000, json_mode=False):
        calls.append({"parts": parts, "prompt": prompt, "json_mode": json_mode})
        return answer

    monkeypatch.setattr(extract.llm, "generate", fake_generate)
    return calls


def test_img_to_rows_keeps_only_requested_fields(monkeypatch):
    # El modelo "agrega" una clave de más (iva) y omite otra (proveedor).
    calls = fake_llm(monkeypatch, '[{"fecha": "2026-01-02", "total": 10, "iva": "21"}]')

    rows = extract.img_to_rows(b"img-falsa", "image/png", ["fecha", "proveedor", "total"])

    assert rows == [{"fecha": "2026-01-02", "proveedor": "", "total": "10"}]
    assert calls[0]["parts"][0].mime == "image/png"  # viaja la imagen
    assert calls[0]["json_mode"] is True  # pedimos JSON válido al modelo


def test_img_to_rows_wraps_single_object(monkeypatch):
    fake_llm(monkeypatch, '{"fecha": "2026-01-02"}')

    assert extract.img_to_rows(b"x", "image/png", ["fecha"]) == [{"fecha": "2026-01-02"}]
