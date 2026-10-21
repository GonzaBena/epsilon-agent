import pytest

from epsilon.ai import attachments, llm
from epsilon.data import notes

KEY = "AIzaSyD-ejemplo_de_clave_larga_QzXw"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.delenv("CHAT_MAX_FILES", raising=False)
    monkeypatch.delenv("CHAT_MAX_MB", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)


def put(rel, data):
    path = notes.DATA_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return rel


# --- mentions ----------------------------------------------------------------


def test_mentions_finds_paths_and_strips_trailing_punctuation():
    assert attachments.mentions("mirá @files/a.pdf y @notas.md.") == ["files/a.pdf", "notas.md"]


def test_mentions_understands_quoted_paths_with_spaces():
    assert attachments.mentions('resumí @"files/mi archivo.pdf" ya') == ["files/mi archivo.pdf"]


def test_mentions_ignores_emails_and_repeats():
    assert attachments.mentions("escribile a juan@correo.com") == []
    assert attachments.mentions("@a.md y @a.md") == ["a.md"]


# --- limits ----------------------------------------------------------------


def test_limits_default_and_fall_back_when_invalid(monkeypatch):
    assert (attachments.max_files(), attachments.max_bytes()) == (5, 15 * 1024 * 1024)
    for bad in ("x", "0", "-3", "999", ""):
        monkeypatch.setenv("CHAT_MAX_FILES", bad)
        monkeypatch.setenv("CHAT_MAX_MB", bad)
        assert (attachments.max_files(), attachments.max_bytes()) == (5, 15 * 1024 * 1024)
    monkeypatch.setenv("CHAT_MAX_FILES", "8")
    monkeypatch.setenv("CHAT_MAX_MB", "30")
    assert (attachments.max_files(), attachments.max_bytes()) == (8, 30 * 1024 * 1024)


# --- load ----------------------------------------------------------------


@pytest.mark.parametrize("path", ["../../etc/passwd", "no-existe.md", "summarizes/x.md"])
def test_load_rejects_what_the_library_does_not_show(path):
    put("summarizes/x.md", "resumen")

    with pytest.raises(ValueError, match="No encuentro"):
        attachments.load([path])


def test_load_rejects_more_files_than_the_limit(monkeypatch):
    monkeypatch.setenv("CHAT_MAX_FILES", "2")
    for name in "abc":
        put(f"{name}.md", "x")

    with pytest.raises(ValueError, match="máximo por mensaje es 2"):
        attachments.load(["a.md", "b.md", "c.md"])


def test_load_rejects_total_size_over_the_limit(monkeypatch):
    monkeypatch.setenv("CHAT_MAX_MB", "1")
    put("files/a.pdf", b"0" * 600_000)
    put("files/b.pdf", b"0" * 600_000)

    with pytest.raises(ValueError, match="máximo por mensaje es 1 MB"):
        attachments.load(["files/a.pdf", "files/b.pdf"])


def test_load_counts_a_repeated_file_once(monkeypatch):
    monkeypatch.setenv("CHAT_MAX_FILES", "1")
    put("a.md", "hola")

    assert len(attachments.load(["a.md", "a.md"])) == 2  # un rótulo y su contenido


def test_load_text_is_labeled_as_data_and_redacted():
    put("a.md", f"clave {KEY}")

    label, content = attachments.load(["a.md"])

    assert "a.md" in label and "no instrucciones" in label
    assert KEY not in content and "clave" in content


def test_load_pdf_goes_as_bytes_with_its_type():
    put("files/a.pdf", b"%PDF-1.4 datos")

    label, part = attachments.load(["files/a.pdf"])

    assert part.mime == "application/pdf"


def test_load_rejects_text_over_the_summary_limit(monkeypatch):
    monkeypatch.setattr(attachments.summarize, "MAX_TEXT", 10)
    put("a.md", "x" * 50)

    with pytest.raises(ValueError, match="demasiado grande"):
        attachments.load(["a.md"])


def test_load_refuses_files_the_chat_model_cannot_read(monkeypatch):
    from epsilon.ai.providers.base import Capabilities

    seen = []
    monkeypatch.setattr(llm, "capabilities", lambda task=None: seen.append(task) or Capabilities(True, False, False))
    put("files/a.pdf", b"%PDF-1.4 datos")
    put("files/b.png", b"png")
    put("a.md", "texto")

    with pytest.raises(ValueError, match="no lee PDFs"):
        attachments.load(["files/a.pdf"])
    with pytest.raises(ValueError, match="no lee imágenes"):
        attachments.load(["files/b.png"])
    assert len(attachments.load(["a.md"])) == 2  # el texto lo lee cualquier modelo
    assert set(seen) == {"chat"}
