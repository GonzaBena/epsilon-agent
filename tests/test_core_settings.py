import pytest

from epsilon import core, tools
from epsilon.settings import editor
from epsilon.skills import store as skill_store

# /ajustes: abre settings.json en el editor de la PC (solo desde la web).


def ask(text, session_id):
    return core.handle(core.Message(text=text, session_id=session_id))


def test_ajustes_is_not_for_the_ai_nor_links():
    tool = next(t for t in tools.TOOLS if t.name == "ajustes")
    assert not tool.ai and not tool.links and tool.menu is None
    with pytest.raises(skill_store.SkillError):  # una skill no puede llamarse igual
        skill_store.check_name("ajustes")


def test_ajustes_in_telegram_does_not_launch(monkeypatch):
    core.SESSIONS.clear()
    opened = []
    monkeypatch.setattr(editor, "open_file", opened.append)
    reply = ask("/ajustes", "telegram:1")
    assert reply.text == "Esto abre un archivo en la pantalla de la PC: usalo desde la web."
    assert opened == []


def test_ajustes_in_the_web_opens_the_settings(monkeypatch):
    core.SESSIONS.clear()
    opened = []
    monkeypatch.setattr(editor, "open_file", opened.append)
    assert ask("/ajustes", "web").text == "Abrí settings.json en tu editor."
    assert ask("/ajustes defecto", "web").text == "Abrí settings_default.json en tu editor."
    assert opened == ["settings", "default"]


def test_ajustes_without_editor_says_why(monkeypatch):
    core.SESSIONS.clear()

    def fail(which):
        raise OSError(editor.NO_EDITOR)

    monkeypatch.setattr(editor, "open_file", fail)
    assert ask("/ajustes", "web").text == editor.NO_EDITOR + "."


def test_ajustes_rejects_other_arguments(monkeypatch):
    core.SESSIONS.clear()
    opened = []
    monkeypatch.setattr(editor, "open_file", opened.append)
    assert "Uso: /ajustes [defecto]" in ask("/ajustes ../etc", "web").text
    assert opened == []
