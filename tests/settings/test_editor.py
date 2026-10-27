import pytest

from epsilon.settings import editor, store


@pytest.fixture
def screen(monkeypatch):
    """Una sesión gráfica con xdg-open y gtk-launch instalados; anota lo que se lanza."""
    calls = []
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(editor, "_launch", calls.append)
    return calls


def test_open_prefers_the_text_editor(screen, monkeypatch, tmp_data):
    monkeypatch.setattr(editor, "_run", lambda args: "org.gnome.TextEditor.desktop\n")
    editor.open_file("settings")
    assert screen == [["gtk-launch", "org.gnome.TextEditor.desktop", str(store.settings_path())]]
    assert store.settings_path().exists()


def test_open_asks_for_the_plain_text_editor(screen, monkeypatch, tmp_data):
    asked = []
    monkeypatch.setattr(editor, "_run", lambda args: asked.append(args) or "")
    editor.open_file("settings")
    assert asked == [["xdg-mime", "query", "default", "text/plain"]]


def test_open_falls_back_to_xdg_open(screen, monkeypatch, tmp_data):
    monkeypatch.setattr(editor, "_run", lambda args: "")
    editor.open_file("default")
    assert screen == [["xdg-open", str(store.default_path())]]
    assert store.default_path().exists()  # se genera si faltaba


def test_open_does_not_overwrite_the_settings(screen, monkeypatch, tmp_data):
    store.settings_path().write_text('{"user.name": "Ana"}', encoding="utf-8")
    monkeypatch.setattr(editor, "_run", lambda args: "")
    editor.open_file("settings")
    assert store.settings_path().read_text(encoding="utf-8") == '{"user.name": "Ana"}'


def test_open_without_screen_raises(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not editor.can_open()
    with pytest.raises(OSError):
        editor.open_file("settings")


def test_open_without_programs_raises(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(editor.shutil, "which", lambda name: None)
    assert not editor.can_open()
    with pytest.raises(OSError):
        editor.open_file("settings")


def test_open_rejects_other_names():
    with pytest.raises(ValueError):
        editor.open_file("../../etc/passwd")


def test_processes_are_blocked_in_tests():
    with pytest.raises(RuntimeError):
        editor._run(["true"])
    with pytest.raises(RuntimeError):
        editor._launch(["true"])
