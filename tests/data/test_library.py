import os
import subprocess

import pytest

from epsilon.data import library, notes


@pytest.fixture
def data(tmp_path, monkeypatch):
    """Una carpeta de datos con de todo, y con fechas distintas: el número más alto es el más nuevo."""
    root = tmp_path.resolve()
    monkeypatch.setattr(notes, "DATA_DIR", root)
    files = {
        "inbox.md": b"comprar filamento",
        "gastos.csv": b"fecha,total\n2026-01-01,10\n",
        "files/2026-10-15_manual.pdf": b"%PDF-1.4",
        "tickets/2026-10-18_balance.jpg": b"jpg",
        "tickets/factura.png": b"png",
        "casa/compras.txt": b"leche",
        "summarizes/inbox.md": b"un resumen",  # no es parte de la biblioteca
        "conversations/abc.jsonl": b"{}",  # tampoco
        "files/raro.exe": b"MZ",  # ni una extensión que Epsilon no maneja
    }
    for age, (name, content) in enumerate(files.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (1_000_000 + age, 1_000_000 + age))
    return root


def names(page):
    return [e.path for e in page["items"]]


def test_lists_notes_files_and_tickets_but_not_summaries_or_odd_files(data):
    page = library.search()

    assert page["total"] == 6
    assert set(names(page)) == {
        "inbox.md", "gastos.csv", "files/2026-10-15_manual.pdf",
        "tickets/2026-10-18_balance.jpg", "tickets/factura.png", "casa/compras.txt",
    }


def test_newest_first(data):
    assert names(library.search())[0] == "casa/compras.txt"  # el último que se creó


def test_kinds(data):
    kinds = {e.path: e.kind for e in library.search()["items"]}

    assert kinds["inbox.md"] == "note" and kinds["casa/compras.txt"] == "note"
    assert kinds["tickets/factura.png"] == "ticket"
    assert kinds["gastos.csv"] == "file" and kinds["files/2026-10-15_manual.pdf"] == "file"


def test_how_each_one_opens(data):
    types = {e.path: e.type for e in library.search()["items"]}

    assert types["inbox.md"] == "text" and types["gastos.csv"] == "text"
    assert types["tickets/factura.png"] == "image"
    assert types["files/2026-10-15_manual.pdf"] == "pdf"


def test_filter_by_kind_and_counts_ignore_the_filter(data):
    page = library.search(kind="ticket")

    assert set(names(page)) == {"tickets/2026-10-18_balance.jpg", "tickets/factura.png"}
    assert page["total"] == 2
    assert page["counts"] == {"all": 6, "note": 2, "file": 2, "ticket": 2}


def test_search_by_name_or_folder_ignoring_case(data):
    assert names(library.search("FACTURA")) == ["tickets/factura.png"]
    assert set(names(library.search("casa"))) == {"casa/compras.txt"}  # por carpeta
    assert library.search("nada de esto")["total"] == 0


def test_counts_follow_the_search(data):
    assert library.search("png")["counts"] == {"all": 1, "note": 0, "file": 0, "ticket": 1}


def test_pages(data):
    first = library.search(limit=4)
    second = library.search(offset=4, limit=4)

    assert len(first["items"]) == 4 and len(second["items"]) == 2
    assert first["total"] == 6
    assert not set(names(first)) & set(names(second))


def test_a_symlink_that_points_outside_is_not_listed(data, tmp_path_factory):
    outside = tmp_path_factory.mktemp("ajeno") / "secreto.md"
    outside.write_text("no es tuyo")
    (data / "enlace.md").symlink_to(outside)

    assert "enlace.md" not in names(library.search())
    with pytest.raises(ValueError):
        library.resolve("enlace.md")


@pytest.mark.parametrize(
    "bad", ["../../etc/passwd", "/etc/passwd", "summarizes/inbox.md", "files/raro.exe", "no-existe.md", "files", ""]
)
def test_resolve_refuses_what_is_not_in_the_library(data, bad):
    with pytest.raises(ValueError):
        library.resolve(bad)


def test_resolve_accepts_a_listed_file(data):
    assert library.resolve("inbox.md") == data / "inbox.md"


def test_read_text_and_its_cap(data, monkeypatch):
    assert library.read_text("inbox.md") == {"text": "comprar filamento", "truncated": False}

    monkeypatch.setattr(library, "MAX_TEXT_CHARS", 5)
    assert library.read_text("inbox.md") == {"text": "compr", "truncated": True}


def test_read_text_refuses_images_and_pdfs(data):
    for name in ("tickets/factura.png", "files/2026-10-15_manual.pdf"):
        with pytest.raises(ValueError):
            library.read_text(name)


def test_media_type_only_for_images_and_pdfs(data):
    assert library.media_type(data / "tickets/factura.png") == "image/png"
    assert library.media_type(data / "tickets/2026-10-18_balance.jpg") == "image/jpeg"
    assert library.media_type(data / "files/2026-10-15_manual.pdf") == "application/pdf"
    assert library.media_type(data / "inbox.md") is None


# --- Abrir la carpeta --------------------------------------------------------


@pytest.fixture
def desktop(monkeypatch):
    """Una PC con pantalla y xdg-open; el programa de verdad nunca se lanza."""
    launched = []
    monkeypatch.setattr(library.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: launched.append((args, kwargs)))
    return launched


def test_can_reveal_needs_xdg_open_and_a_screen(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(library.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert not library.can_reveal()  # un servidor sin pantalla

    monkeypatch.setenv("DISPLAY", ":0")
    assert library.can_reveal()

    monkeypatch.setattr(library.shutil, "which", lambda name: None)
    assert not library.can_reveal()  # sin xdg-open


def test_reveal_opens_the_containing_folder_with_no_shell(data, desktop):
    library.reveal("tickets/factura.png")

    (args, kwargs), = desktop
    assert args == ["xdg-open", str(data / "tickets")]
    assert "shell" not in kwargs or kwargs["shell"] is False


def test_reveal_refuses_anything_outside_the_library(data, desktop):
    with pytest.raises(ValueError):
        library.reveal("../../etc/passwd")
    assert desktop == []


def test_reveal_fails_cleanly_without_a_screen(data, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(OSError):
        library.reveal("inbox.md")


def test_what_belongs_to_epsilon_is_not_listed_but_still_opens(data):
    system = {
        "memory.md": b"- 2026-10-20: soy gonzalo",
        "todo.md": b"- [ ] pagar",
        "skills/informe/SKILL.md": b"---\nname: informe\n---",
        "plugins/eco/README.md": b"un plugin",
        ".oculto/nota.md": b"x",
        ".algo.md": b"x",
    }
    for name, content in system.items():
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_bytes(content)
    (data / "casa" / "todo.md").write_bytes(b"- [ ] otra")  # una nota tuya que se llama igual, en otra carpeta, sí va

    paths = {e.path for e in library.search(limit=100)["items"]}
    assert not paths & set(system)
    assert "casa/todo.md" in paths and "inbox.md" in paths
    assert library.resolve("memory.md").name == "memory.md"  # abrir por su ruta (por ejemplo, con @) sigue andando


def test_the_voice_companion_folder_is_not_listed(tmp_path, monkeypatch):
    from pathlib import Path

    from epsilon.data import library, notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "hyprland-voice.conf").write_text("bind = ALT, X, exec, x")

    assert library._is_system(Path("voice/hyprland-voice.conf"))
    assert "hyprland-voice.conf" not in str(library.search())
