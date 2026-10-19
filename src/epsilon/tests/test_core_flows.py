import pytest

from epsilon import core, notes


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    # Datos en carpeta temporal y sin sesiones de otros tests.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    core.SESSIONS.clear()


def say(text: str, session_id: str = "s1") -> core.Reply:
    return core.handle(core.Message(text=text, session_id=session_id))


def press(action: str, session_id: str = "s1") -> core.Reply:
    return core.handle(core.Message(action=action, session_id=session_id))


def send_file(caption: str = "", name: str = "foto.jpg", session_id: str = "s1"):
    attachment = core.Attachment(data=b"bytes", name=name, mime="image/jpeg")
    return core.handle(
        core.Message(text=caption, attachment=attachment, session_id=session_id)
    )


def fake_rows(monkeypatch, rows):
    calls = []

    def fake(data, mime, fields):
        calls.append((data, mime, fields))
        return rows

    monkeypatch.setattr(core.extract, "img_to_rows", fake)
    return calls


# --- parse_request -----------------------------------------------------------


def test_parse_request_splits_name_and_fields():
    assert core.parse_request("gastos: fecha, total") == ("gastos", ["fecha", "total"])


def test_parse_request_without_colon_is_none():
    assert core.parse_request("una foto") is None
    assert core.parse_request("") is None


def test_parse_request_without_fields_is_none():
    assert core.parse_request("gastos:") is None
    assert core.parse_request(": fecha") is None


# --- imagen → CSV con confirmación -------------------------------------------


def test_image_with_request_shows_preview_and_asks_confirmation(monkeypatch):
    calls = fake_rows(monkeypatch, [{"fecha": "1/1", "total": "10"}])

    reply = send_file("gastos: fecha, total")

    assert "1 fila(s) para gastos.csv" in reply.text
    assert "1/1 | 10" in reply.text
    assert [a.id for a in reply.actions] == ["guardar", "cancelar"]
    assert calls[0] == (b"bytes", "image/jpeg", ["fecha", "total"])
    # Todavía no se guardó nada.
    assert not (notes.DATA_DIR / "gastos.csv").exists()


def test_confirming_saves_the_csv(monkeypatch):
    fake_rows(monkeypatch, [{"fecha": "1/1", "total": "10"}])
    send_file("gastos: fecha, total")

    reply = press("guardar")

    assert reply.text == "1 fila(s) guardadas en gastos.csv"
    assert "1/1,10" in (notes.DATA_DIR / "gastos.csv").read_text(encoding="utf-8")


def test_cancelling_saves_nothing(monkeypatch):
    fake_rows(monkeypatch, [{"fecha": "1/1", "total": "10"}])
    send_file("gastos: fecha, total")

    assert press("cancelar").text == "Cancelado."
    assert not (notes.DATA_DIR / "gastos.csv").exists()


def test_cannot_save_twice(monkeypatch):
    fake_rows(monkeypatch, [{"fecha": "1/1", "total": "10"}])
    send_file("gastos: fecha, total")
    press("guardar")

    assert press("guardar").text == "No hay nada pendiente."


def test_new_image_replaces_the_pending_one(monkeypatch):
    fake_rows(monkeypatch, [{"a": "1"}])
    send_file("uno: a")
    send_file("dos: a")

    press("guardar")

    assert not (notes.DATA_DIR / "uno.csv").exists()
    assert (notes.DATA_DIR / "dos.csv").exists()


def test_preview_shows_only_first_five_rows(monkeypatch):
    fake_rows(monkeypatch, [{"a": str(i)} for i in range(8)])

    reply = send_file("x: a")

    assert "8 fila(s)" in reply.text
    assert "\n4" in reply.text and "\n5" not in reply.text


def test_image_without_rows(monkeypatch):
    fake_rows(monkeypatch, [])

    assert send_file("x: a").text == "No encontré datos en la imagen."
    assert press("guardar").text == "No hay nada pendiente."


def test_extract_error_gives_short_message(monkeypatch):
    def boom(*args):
        raise RuntimeError("detalle interno")

    monkeypatch.setattr(core.extract, "img_to_rows", boom)

    assert send_file("x: a").text == "No pude procesar la imagen."


def test_csv_with_other_columns_reports_error(monkeypatch):
    notes.save_csv("gastos", ["otra"], [{"otra": "1"}])
    fake_rows(monkeypatch, [{"fecha": "1/1"}])
    send_file("gastos: fecha")

    assert press("guardar").text.startswith("No se pudo guardar:")


def test_sessions_do_not_share_pending(monkeypatch):
    fake_rows(monkeypatch, [{"a": "1"}])
    send_file("x: a", session_id="uno")

    assert press("guardar", session_id="dos").text == "No hay nada pendiente."
    assert press("guardar", session_id="uno").text.endswith("x.csv")


# --- archivos sin pedido de extracción ---------------------------------------


def test_file_without_request_is_saved_and_offers_summary():
    reply = send_file(name="doc.pdf")

    assert reply.text.startswith("Guardado: files/")
    assert [a.id.split(":")[0] for a in reply.actions] == ["resumir", "nada"]
    assert len(list((notes.DATA_DIR / "files").iterdir())) == 1


def test_nada_keeps_file_and_forgets_it():
    reply = send_file(name="doc.pdf")
    nada = reply.actions[1].id

    assert press(nada).text.startswith("Listo. Queda guardado: files/")
    assert press(nada).text.startswith("Ya no recuerdo ese archivo")


def test_resumir_button_summarizes_and_saves(monkeypatch):
    seen = []

    def fake(path):
        seen.append(path)
        return "summarizes/doc", "- resumen"

    monkeypatch.setattr(core.summarize, "summarize_and_save", fake)
    reply = send_file(name="doc.pdf")

    result = press(reply.actions[0].id)

    assert result.text == "Resumen guardado en summarizes/doc.md\n\n- resumen"
    assert seen[0].name.endswith("doc.pdf")
    # Ya resuelto: no se puede resumir dos veces con el mismo botón.
    assert press(reply.actions[0].id).text.startswith("Ya no recuerdo")


def test_resumir_button_reports_readable_errors(monkeypatch):
    def too_big(path):
        raise ValueError("El archivo es demasiado grande para resumirlo.")

    monkeypatch.setattr(core.summarize, "summarize_and_save", too_big)
    reply = send_file(name="doc.pdf")

    assert press(reply.actions[0].id).text == (
        "El archivo es demasiado grande para resumirlo."
    )


def test_resumir_button_hides_unexpected_errors(monkeypatch):
    def boom(path):
        raise RuntimeError("secreto")

    monkeypatch.setattr(core.summarize, "summarize_and_save", boom)
    reply = send_file(name="doc.pdf")

    assert press(reply.actions[0].id).text == "No pude resumirlo."


def test_resumir_button_can_be_retried_after_failure(monkeypatch):
    attempts = []

    def flaky(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise RuntimeError("falló")
        return "summarizes/doc", "- ok"

    monkeypatch.setattr(core.summarize, "summarize_and_save", flaky)
    reply = send_file(name="doc.pdf")
    button = reply.actions[0].id

    assert press(button).text == "No pude resumirlo."
    assert press(button).text.startswith("Resumen guardado")


def test_unknown_action_does_not_break():
    assert press("inventada").text == "No entiendo esa acción."


# --- /ticket y /cancelar -----------------------------------------------------


def test_ticket_sends_next_file_to_tickets_without_buttons():
    assert "tickets/" in say("/ticket").text

    reply = send_file(name="ticket.jpg")

    assert reply.text.startswith("Guardado: tickets/")
    assert reply.actions == []
    assert len(list((notes.DATA_DIR / "tickets").iterdir())) == 1


def test_ticket_mode_is_used_only_once():
    say("/ticket")
    send_file(name="uno.jpg")

    assert send_file(name="dos.jpg").text.startswith("Guardado: files/")


def test_cancel_leaves_ticket_mode():
    say("/ticket")

    assert say("/cancelar").text == "Cancelado. Ya no espero ningún ticket."
    assert send_file(name="a.jpg").text.startswith("Guardado: files/")


def test_cancel_without_ticket_mode():
    assert say("/cancelar").text == "No hay nada para cancelar."


def test_plain_word_cancelar_also_cancels():
    say("/ticket")

    assert say("  Cancelar ").text == "Cancelado. Ya no espero ningún ticket."


# --- /resumir ----------------------------------------------------------------


def test_resumir_command_needs_a_name():
    assert say("/resumir").text.startswith("Uso: /resumir nombre")


def test_resumir_command_without_matches():
    assert say("/resumir nada").text == "No encontré nada con ese nombre."


def test_resumir_command_with_several_matches_does_not_guess(monkeypatch):
    monkeypatch.setattr(core.summarize, "summarize_and_save", lambda p: 1 / 0)
    notes.save_note("casa1", "x")
    notes.save_note("casa2", "x")

    reply = say("/resumir casa")

    assert "Hay varias coincidencias" in reply.text
    assert "- casa1.md" in reply.text and "- casa2.md" in reply.text


def test_resumir_command_summarizes_the_single_match(monkeypatch):
    monkeypatch.setattr(
        core.summarize, "summarize_and_save", lambda p: ("summarizes/casa", "- ok")
    )
    notes.save_note("casa", "x")

    assert say("/resumir casa").text == "Resumen guardado en summarizes/casa.md\n\n- ok"
