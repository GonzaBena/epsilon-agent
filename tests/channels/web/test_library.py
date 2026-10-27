import re

import pytest

from epsilon.data import library, notes
from tests.channels.web.helpers import js, page


@pytest.fixture
def stocked(client, tmp_path):
    """El cliente de la web con unos archivos guardados (client ya apunta DATA_DIR a tmp_path)."""
    (tmp_path / "inbox.md").write_text("comprar filamento\n", encoding="utf-8")
    (tmp_path / "tickets").mkdir()
    (tmp_path / "tickets" / "balance.png").write_bytes(b"\x89PNG-no-real")
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "manual.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "summarizes").mkdir()
    (tmp_path / "summarizes" / "inbox.md").write_text("un resumen")
    return client


@pytest.fixture
def desktop(monkeypatch):
    launched = []
    monkeypatch.setattr(library, "can_reveal", lambda: True)
    monkeypatch.setattr(library.subprocess, "Popen", lambda args, **kwargs: launched.append(args))
    return launched


# --- La lista --------------------------------------------------------------------


def test_library_lists_items_counts_and_the_root(stocked, tmp_path):
    data = stocked.get("/api/library").json()

    assert data["total"] == 3
    assert {i["path"] for i in data["items"]} == {"inbox.md", "tickets/balance.png", "files/manual.pdf"}
    assert data["counts"] == {"all": 3, "note": 1, "file": 1, "ticket": 1}
    assert data["root"] == str(tmp_path)
    assert isinstance(data["can_reveal"], bool)


def test_settings_files_hidden_from_library(stocked, tmp_path, monkeypatch):
    # Aunque .json llegara a ser una extensión de la biblioteca, los ajustes no se listan ni se abren.
    monkeypatch.setattr(notes, "summarizable_ext", notes.summarizable_ext | {".json"})
    (tmp_path / "files" / "datos.json").write_text("{}")  # un .json tuyo sí
    for name in ("settings.json", "settings_default.json", "settings.schema.json"):
        (tmp_path / name).write_text("{}")
        with pytest.raises(ValueError, match="no existe en la biblioteca"):
            library.resolve(name)
        assert stocked.get(f"/api/library/text?path={name}").status_code == 404
    paths = {i["path"] for i in stocked.get("/api/library").json()["items"]}
    assert "files/datos.json" in paths and not any(p.startswith("settings") for p in paths)


def test_library_filter_search_and_paging(stocked):
    assert stocked.get("/api/library?kind=ticket").json()["total"] == 1
    assert stocked.get("/api/library?q=manual").json()["items"][0]["name"] == "manual.pdf"
    two = stocked.get("/api/library?limit=2").json()
    rest = stocked.get("/api/library?offset=2&limit=2").json()
    assert len(two["items"]) == 2 and len(rest["items"]) == 1 and two["total"] == 3


@pytest.mark.parametrize("query", ["kind=otro", "limit=0", "limit=101", "offset=-1", f"q={'a' * 101}"])
def test_library_rejects_bad_parameters(stocked, query):
    assert stocked.get(f"/api/library?{query}").status_code == 422


# --- Abrir un elemento -----------------------------------------------------------------


def test_text_of_a_note(stocked):
    response = stocked.get("/api/library/text", params={"path": "inbox.md"})

    assert response.json() == {"text": "comprar filamento\n", "truncated": False}


def test_text_refuses_non_text(stocked):
    assert stocked.get("/api/library/text", params={"path": "tickets/balance.png"}).status_code == 415


def test_image_and_pdf_are_served_with_a_fixed_type(stocked):
    image = stocked.get("/api/library/file", params={"path": "tickets/balance.png"})
    pdf = stocked.get("/api/library/file", params={"path": "files/manual.pdf"})

    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert pdf.headers["content-type"] == "application/pdf"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.content == b"\x89PNG-no-real"


def test_file_does_not_serve_notes_as_files(stocked):
    # Las notas se piden como texto (/api/library/text): nunca como un archivo que el navegador interprete.
    assert stocked.get("/api/library/file", params={"path": "inbox.md"}).status_code == 415


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "summarizes/inbox.md", "no-existe.png", "tickets"])
@pytest.mark.parametrize("route", ["/api/library/file", "/api/library/text"])
def test_nothing_outside_the_library_is_served(stocked, route, bad):
    assert stocked.get(route, params={"path": bad}).status_code == 404


def test_library_needs_a_local_host_like_every_route(stocked):
    # El guard de Host/Origin de siempre también cubre la biblioteca.
    assert stocked.get("/api/library", headers={"host": "ejemplo.com"}).status_code == 403
    assert stocked.get("/api/library/file", params={"path": "inbox.md"}, headers={"host": "ejemplo.com"}).status_code == 403


# --- Mostrar en la carpeta -----------------------------------------------------------------


def test_reveal_opens_the_folder(stocked, desktop, tmp_path):
    response = stocked.post("/api/library/reveal", json={"path": "tickets/balance.png"})

    assert response.status_code == 200
    assert desktop == [["xdg-open", str(tmp_path / "tickets")]]


def test_reveal_refuses_paths_outside_the_library(stocked, desktop):
    response = stocked.post("/api/library/reveal", json={"path": "../../etc"})

    assert response.status_code == 404
    assert desktop == []


def test_reveal_says_so_when_there_is_no_screen(stocked, monkeypatch):
    monkeypatch.setattr(library, "can_reveal", lambda: False)

    assert stocked.post("/api/library/reveal", json={"path": "inbox.md"}).status_code == 409


def test_reveal_rejects_a_foreign_origin(stocked, desktop):
    response = stocked.post("/api/library/reveal", json={"path": "inbox.md"}, headers={"origin": "http://evil.example"})

    assert response.status_code == 403 and desktop == []


# --- La página -----------------------------------------------------------------------------


def test_library_page_is_served(client):
    response = client.get("/biblioteca")

    assert response.status_code == 200 and "Biblioteca" in response.text
    assert "/static/js/biblioteca.js" in response.text


def test_library_page_is_read_only_and_loads_lazily():
    source = js("biblioteca.js")
    code = re.sub(r"//.*", "", source)

    # Solo lecturas, salvo abrir la carpeta: ningún PUT, DELETE ni PATCH.
    assert not re.search(r"method:\s*\"(PUT|DELETE|PATCH)\"", code)
    assert code.count('method: "POST"') == 1 and "/api/library/reveal" in code
    # La lista llega por partes.
    assert "limit" in code and "Cargar más" in page("biblioteca.html")
    # Lo que viene del servidor va como texto.
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in code


def test_library_page_has_no_inline_script_or_style():
    html = page("biblioteca.html")

    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "style=" not in html and "<style" not in html
    assert not re.search(r"\son\w+=", html)


# --- Transiciones (View Transitions) ----------------------------------------------------------


def test_pages_cross_fade_instead_of_popping():
    css = js("style.css")

    # Entre páginas: el navegador funde en vez de cortar, y quien pide menos animación no las ve.
    assert re.search(r"@view-transition\s*\{\s*navigation:\s*auto;", css)
    assert re.search(r"prefers-reduced-motion:\s*reduce\)\s*\{\s*@view-transition\s*\{\s*navigation:\s*none;", css)
    # El header y el menú llevan nombre para quedarse quietos mientras se funde el contenido.
    assert "view-transition-name: top-bar" in css and "view-transition-name: side-menu" in css


def test_library_swaps_screens_with_a_transition_and_a_fallback():
    code = js("biblioteca.js")

    assert "document.startViewTransition" in code
    assert "prefers-reduced-motion" in code  # sin animación, el cambio es directo
    assert "if (!document.startViewTransition" in code  # y sin soporte, también
