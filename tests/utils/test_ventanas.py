import json

import pytest

from epsilon.utils import ventanas


def window(address, cls, title, ws, focus=1, **extra):
    return {"address": address, "class": cls, "title": title, "workspace": {"id": int(ws), "name": ws},
            "mapped": True, "hidden": False, "focusHistoryID": focus, **extra}


KITTY = window("0x1", "kitty", "Control de PC", "2", focus=0)
MUSIC = window("0x2", "chrome-music.youtube.com__-Default", "Un | set", "5")
CHROMIUM = window("0x3", "chromium", "Cómo hacer café", "1")


def fake_hypr(monkeypatch, clients, active=KITTY):
    seen = []

    def run(what):
        seen.append(what)
        return json.dumps({} if what == "activewindow" and active is None else active if what == "activewindow" else clients)

    monkeypatch.setattr(ventanas, "_hyprctl", run)
    return seen


def test_report_lists_windows_by_workspace_and_marks_the_active_one(monkeypatch):
    fake_hypr(monkeypatch, [MUSIC, KITTY, CHROMIUM])

    rows = ventanas.report("").splitlines()[4:]

    assert rows == [
        "| 1 | chromium | Cómo hacer café |",
        "| 2 | kitty (activa) | Control de PC |",
        "| 5 | music.youtube.com | Un / set |",  # la app web sin el prefijo del navegador; la barra no rompe la tabla
    ]


def test_report_activa_shows_only_the_focused_window(monkeypatch):
    seen = fake_hypr(monkeypatch, [])

    assert ventanas.report("Activa") == "**Ventana activa**: kitty — Control de PC (espacio 2)"
    assert seen == ["activewindow"]


def test_report_activa_without_focus(monkeypatch):
    fake_hypr(monkeypatch, [], active=None)

    assert ventanas.report("activa") == "No hay ninguna ventana con el foco."


def test_hidden_and_unmapped_windows_are_skipped(monkeypatch):
    fake_hypr(monkeypatch, [KITTY, {**CHROMIUM, "mapped": False}, {**MUSIC, "hidden": True}])

    assert len(ventanas.report("").splitlines()[4:]) == 1  # sin contar el encabezado


def test_no_windows(monkeypatch):
    fake_hypr(monkeypatch, [], active=None)

    assert ventanas.report("") == "No hay ventanas abiertas."


def test_titles_are_cut_and_flattened(monkeypatch):
    fake_hypr(monkeypatch, [{**KITTY, "title": "a\nb  " + "x" * 500}])

    row = ventanas.report("").splitlines()[-1]
    assert "\n" not in row and len(row) < 130


@pytest.mark.parametrize("text", ["todas", "cerrar", "activa; rm -rf /", "--help"])
def test_other_options_are_refused_without_running_anything(monkeypatch, text):
    seen = fake_hypr(monkeypatch, [])

    with pytest.raises(ValueError, match="Opciones: activa"):
        ventanas.report(text)
    assert seen == []


def test_a_broken_answer_is_a_readable_error(monkeypatch):
    monkeypatch.setattr(ventanas, "_hyprctl", lambda what: "no es json")

    with pytest.raises(ventanas.VentanasError):
        ventanas.report("")


def test_a_missing_hyprctl_is_a_readable_error(monkeypatch):
    monkeypatch.undo()  # vuelve el `_hyprctl` real; con un PATH vacío «no existe»
    monkeypatch.setenv("PATH", "")

    with pytest.raises(ventanas.VentanasError, match="Falta el programa"):
        ventanas._hyprctl("clients")


def test_the_instance_is_taken_from_the_runtime_folder_when_the_environment_lacks_it(monkeypatch, tmp_path):
    (tmp_path / "hypr" / "vieja").mkdir(parents=True)
    (tmp_path / "hypr" / "nueva").mkdir()
    (tmp_path / "hypr" / "vieja").touch()
    import os
    os.utime(tmp_path / "hypr" / "vieja", (1, 1))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)

    assert ventanas._instance_signature() == "nueva"
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    assert ventanas._instance_signature() is None
