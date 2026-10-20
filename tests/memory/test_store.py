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


def press(action):
    return core.handle(Message(text="", session_id="test-memory", action=action))


def fill():
    store.remember("uno")
    store.remember("dos")
    store.remember("tres")


def test_memoria_numbers_the_lines(data_dir):
    fill()

    shown = say("/memoria").splitlines()

    assert [line.split(". ")[0] for line in shown] == ["1", "2", "3"]
    assert shown[1].endswith(": dos")


def test_forget_removes_only_that_line_and_returns_it(data_dir):
    fill()

    removed = store.forget(2)

    assert removed.endswith(": dos")
    assert [line.split(": ")[1] for line in store.lines()] == ["uno", "tres"]
    assert not (data_dir / "memory.md.tmp").exists()


def test_forget_refuses_a_line_that_changed_since_it_was_shown():
    fill()
    shown = store.line_at(2)[1]
    store.forget(1)  # los números se corrieron: ahora la 2 es «tres»

    with pytest.raises(ValueError):
        store.forget(2, expected=shown)
    assert len(store.lines()) == 2


def test_edit_keeps_the_date_and_checks_the_text(data_dir):
    fill()
    date = store.lines()[1].split(": ")[0]

    new = store.edit(2, "dos corregido")

    assert new == f"{date}: dos corregido" and store.lines()[1] == new
    with pytest.raises(ValueError):
        store.edit(2, "  ")
    with pytest.raises(ValueError):
        store.edit(9, "no existe")
    assert store.lines()[1] == new


def test_edit_gives_todays_date_to_a_line_edited_by_hand(data_dir):
    (data_dir / "memory.md").write_text("algo escrito a mano\n", encoding="utf-8")

    assert store.edit(1, "ordenado").endswith(": ordenado") and store.lines()[0].startswith("- 20")


def test_olvidar_asks_first_and_only_the_button_deletes(data_dir):
    fill()

    asked = say("/olvidar 2")
    assert "dos" in asked and len(store.lines()) == 3  # todavía no borró nada

    assert press("olvidar").text == "Olvidado." and len(store.lines()) == 2
    assert press("olvidar").text == "No hay nada pendiente."  # no se confirma dos veces


def test_olvidar_conservar_keeps_everything_and_bad_numbers_are_explained(data_dir):
    fill()
    core.handle(Message(text="/olvidar 1", session_id="test-memory"))

    assert "queda en la memoria" in press("conservar").text and len(store.lines()) == 3
    assert "Uso: /olvidar" in say("/olvidar")
    assert "No hay una línea 9" in say("/olvidar 9")
    assert "Uso: /editar" in say("/editar 2")


def test_editar_command(data_dir):
    fill()

    assert say("/editar 3 tres corregido").startswith("Cambiado: 3. ")
    assert store.lines()[2].endswith(": tres corregido")
