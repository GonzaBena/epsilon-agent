import pytest

from epsilon import core
from epsilon.ai import llm, permissions, usage
from epsilon.data import conversations, notes

# Archivos mencionados con «@» en un chat de modo IA: llegan al modelo en ese mensaje. El modelo es un guion.

CID = "20261021-100000-aaaa"
NORMAL = "20261021-100001-bbbb"
TURN = llm.text_turn("model", "")


@pytest.fixture
def seen(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setenv("GEMINI_MODEL", "modelo-a")
    monkeypatch.setenv("CHAT_LIMIT_USD", "")
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})
    monkeypatch.setattr(permissions, "_granted", {})
    monkeypatch.setattr(core, "SESSIONS", {})
    conversations.start(CID, "ai", 5)
    conversations.start(NORMAL, "normal", 5)
    calls = []

    def fake(history, system, offered, max_tokens=1500, allow_calls=True):
        calls.append(list(history))
        usage.record("modelo-a", 1000, 0)
        return llm.Step("listo", [], TURN)

    monkeypatch.setattr(llm, "chat_tools", fake)
    return calls


def put(rel, data):
    path = notes.DATA_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def say(text, cid=CID):
    conversations.add_message(cid, "user", text)
    return core.handle(core.Message(text=text, session_id=f"web:{cid}", conversation_id=cid))


def test_a_mentioned_pdf_reaches_the_model_in_that_message(seen):
    put("files/a.pdf", b"%PDF-1.4 datos")

    reply = say("armá un resumen de @files/a.pdf")

    assert reply.text == "listo" and reply.ai
    last = seen[0][-1]
    assert last.parts[1].inline_data.mime_type == "application/pdf"
    assert last.parts[-1].text == "armá un resumen de @files/a.pdf"


def test_a_missing_file_answers_with_an_error_and_never_calls_the_model(seen):
    reply = say("resumí @no-existe.md")

    assert reply.text == "No encuentro «no-existe.md» en la biblioteca."
    assert seen == []


def test_an_email_is_not_a_mention(seen):
    reply = say("escribile a juan@correo.com")

    assert reply.text == "listo" and len(seen[0][-1].parts) == 1


def test_over_the_limits_it_errors_before_calling_the_model(seen, monkeypatch):
    monkeypatch.setenv("CHAT_MAX_FILES", "1")
    put("a.md", "x")
    put("b.md", "y")

    reply = say("compará @a.md y @b.md")

    assert "máximo por mensaje es 1" in reply.text and seen == []


def test_files_do_not_travel_again_in_the_next_message(seen):
    put("a.md", "contenido")
    say("resumí @a.md")

    say("más corto")

    assert all(len(t.parts) == 1 for t in seen[1])


def test_a_normal_conversation_ignores_mentions(seen):
    reply = say("mirá @a.md", NORMAL)

    assert seen == [] and "listo" not in reply.text
