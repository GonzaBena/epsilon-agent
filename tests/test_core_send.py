import pytest

from epsilon import core
from epsilon.data import notes

# /enviar: pedirle al canal que mande un archivo guardado. Solo Telegram lo envía; la web contesta que ya está en la biblioteca.


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    (tmp_path / "tickets").mkdir()
    (tmp_path / "tickets" / "super.jpg").write_bytes(b"jpg")
    (tmp_path / "tickets" / "farmacia.png").write_bytes(b"png")
    (tmp_path / "inbox.md").write_text("hola\n", encoding="utf-8")
    (tmp_path / "gastos.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "notes-secret.env").write_text("TOKEN=x\n", encoding="utf-8")
    (tmp_path / notes.SUMMARIES_DIR).mkdir()
    (tmp_path / notes.SUMMARIES_DIR / "inbox.md").write_text("resumen\n", encoding="utf-8")
    return tmp_path


def telegram(text):
    return core.handle(core.Message(text=text, session_id="telegram:7"))


def web(text):
    return core.handle(core.Message(text=text, session_id="web:abc"))


def test_telegram_gets_the_file_to_send():
    reply = telegram("/enviar super")

    assert reply.files == ["tickets/super.jpg"]
    assert "tickets/super.jpg" in reply.text


def test_the_web_never_sends_anything_and_says_it_is_in_the_library():
    reply = web("/enviar super")

    assert reply.files == []
    assert "Biblioteca" in reply.text


def test_notes_and_csv_can_be_sent_too():
    assert telegram("/enviar inbox").files == ["inbox.md"]
    assert telegram("/enviar gastos").files == ["gastos.csv"]


def test_the_path_shown_by_archivos_works_and_so_do_quotes():
    assert telegram("/enviar tickets/farmacia.png").files == ["tickets/farmacia.png"]
    assert telegram('/enviar "tickets/super.jpg"').files == ["tickets/super.jpg"]


def test_several_matches_are_listed_and_nothing_is_sent():
    reply = telegram("/enviar a")  # farmacia.png y gastos.csv

    assert reply.files == []
    assert "varias coincidencias" in reply.text


def test_nothing_found_and_missing_name():
    assert "No encontré" in telegram("/enviar zzz").text
    assert telegram("/enviar zzz").files == []
    assert "Uso" in telegram("/enviar").text


@pytest.mark.parametrize("wanted", [".env", "../../etc/passwd", "../inbox.md/..", "notes-secret.env", f"{notes.SUMMARIES_DIR}/inbox.md"])
def test_only_library_files_inside_the_data_folder_are_sent(wanted):
    assert telegram(f"/enviar {wanted}").files == []


def test_a_file_over_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(core, "SEND_MAX_BYTES", 2)

    reply = telegram("/enviar super")

    assert reply.files == [] and "pesa más" in reply.text


def test_the_outbox_is_per_call_so_nothing_is_sent_twice():
    telegram("/enviar super")

    assert telegram("/fecha").files == []


def test_the_ai_tool_asks_the_channel_to_send_the_file():
    token = core._outbox.set([])
    try:
        result = core.AI_RUNNERS["enviar"]("/enviar tickets/super.jpg")
        assert core._outbox.get() == ["tickets/super.jpg"]
        assert "Te mando" in result.text
    finally:
        core._outbox.reset(token)


def test_the_ai_tool_in_the_web_reports_that_nothing_was_sent():
    assert "Biblioteca" in core.AI_RUNNERS["enviar"]("super").text
