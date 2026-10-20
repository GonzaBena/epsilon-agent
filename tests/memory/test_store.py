import pytest

from epsilon import core
from epsilon.core import Message
from epsilon.data import notes
from epsilon.memory import store


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    return tmp_path


def say(text):
    return core.handle(Message(text=text, session_id="test-memory")).text


def test_remember_appends_dated_lines_and_never_overwrites(data_dir):
    store.remember("Prefiero los CSV con coma")
    store.remember("Vivo en Rosario")

    lines = (data_dir / "memory.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(": Prefiero los CSV con coma") and lines[0].startswith("- 20")
    assert lines[1].endswith(": Vivo en Rosario")


def test_read_is_empty_before_anything_is_remembered():
    assert store.read() == ""


def test_remember_rejects_empty_and_too_long_text(data_dir):
    with pytest.raises(ValueError):
        store.remember("   ")
    with pytest.raises(ValueError):
        store.remember("x" * (store.MAX_CHARS + 1))

    assert not (data_dir / "memory.md").exists()


def test_remember_is_one_line_and_redacts_secrets(monkeypatch, data_dir):
    monkeypatch.setattr(store.config, "redact", lambda text: text.replace("sk-secreto", "***"))

    store.remember("mi clave\nes sk-secreto")

    text = (data_dir / "memory.md").read_text(encoding="utf-8")
    assert "sk-secreto" not in text and "mi clave es ***" in text
    assert len(text.splitlines()) == 1


def test_recordar_and_memoria_commands(data_dir):
    assert "Todavía no recordé nada" in say("/memoria")
    assert "Uso: /recordar" in say("/recordar")

    assert "Anotado en la memoria" in say("/recordar Prefiero respuestas cortas")

    assert "Prefiero respuestas cortas" in say("/memoria")
