import json
import re
import shutil
import subprocess

import pytest

from epsilon.channels import web
from tests.channels.web.helpers import js, page, static_path

PAGES = ["index.html", "biblioteca.html", "permisos.html", "rutinas.html", "canales.html", "uso.html", "config.html"]
NODE = shutil.which("node")


def chip_tag(name):
    return re.search(r"<button[^>]*id=\"model-chip\"[^>]*>.*?</button>", page(name), re.S).group(0)


# El chat no lo lleva arriba: tiene el del composer (un solo lugar para elegir el modelo).
HEADER_CHIP_PAGES = [name for name in PAGES if name != "index.html"]


def test_the_chat_picks_the_model_only_in_the_composer():
    html = page("index.html")

    assert 'id="model-chip"' not in html
    assert 'id="composer-model"' in html and 'aria-haspopup="dialog"' in html


@pytest.mark.parametrize("name", HEADER_CHIP_PAGES)
def test_the_model_chip_is_a_button_that_opens_the_palette(name):
    html = page(name)

    assert '<a class="model-chip"' not in html
    tag = chip_tag(name)
    assert 'type="button"' in tag and 'class="model-chip"' in tag and 'aria-haspopup="dialog"' in tag
    assert 'id="model-name"' in tag


def test_the_model_chip_is_identical_in_every_page():
    assert len({chip_tag(name) for name in HEADER_CHIP_PAGES}) == 1


@pytest.mark.parametrize("name", PAGES)
def test_every_page_loads_the_palette_before_the_shell(name):
    html = page(name)

    assert html.index("/static/js/palette.js") < html.index("/static/js/shell.js")


def test_palette_and_shell_follow_the_web_rules():
    palette, shell = js("palette.js"), js("shell.js")

    assert not re.search(r"\.innerHTML|innerHTML\s*=", palette + shell)  # solo textContent
    assert "confirm(" not in palette.replace("epsilonModal", "") and "alert(" not in palette
    assert "/api/models" in palette and "showModal" in palette and "PUT" in palette
    assert "GEMINI_MODEL" not in shell and "epsilonPalette" in shell


def test_the_server_serves_the_palette_script(client):
    reply = client.get("/static/js/palette.js")

    assert reply.status_code == 200 and "javascript" in reply.headers["content-type"]


# --- La lógica pura de la paleta, ejecutada con node (si está) ---------------------


def run_node(expression):
    script = (
        "const window = {}; const document = undefined;\n"
        + static_path("palette.js").read_text(encoding="utf-8")
        + f"\nconsole.log(JSON.stringify({expression}));"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


MODELS = [
    {"id": "gemini:gemini-3.1-flash-lite", "provider": "gemini"},
    {"id": "ollama:qwen3:8b", "provider": "ollama"},
    {"id": "ollama:llama3.1:8b", "provider": "ollama"},
]


@pytest.mark.skipif(not NODE, reason="node no está instalado")
def test_filter_matches_text_anywhere_in_provider_model_ignoring_case():
    ids = lambda query: [m["id"] for m in run_node(f"window.epsilonPalette.filter({json.dumps(MODELS)}, {json.dumps(query)})")]

    assert ids("") == [m["id"] for m in MODELS]
    assert ids("  QWEN ") == ["ollama:qwen3:8b"]
    assert ids("ollama:") == ["ollama:qwen3:8b", "ollama:llama3.1:8b"]
    assert ids("8b") == ["ollama:qwen3:8b", "ollama:llama3.1:8b"]
    assert ids("flash") == ["gemini:gemini-3.1-flash-lite"]
    assert ids("nada") == []


@pytest.mark.skipif(not NODE, reason="node no está instalado")
def test_usable_offers_an_unlisted_model_only_for_a_provider_that_answered():
    def usable(query, up):
        return run_node(f"window.epsilonPalette.usable({json.dumps(query)}, {json.dumps(MODELS)}, {json.dumps(up)})")

    assert usable("ollama:phi4", ["gemini", "ollama"]) == "ollama:phi4"
    assert usable("ollama:phi4", ["gemini"]) is None  # Ollama no respondió: no se ofrece nada de él
    assert usable("ollama:qwen3:8b", ["gemini", "ollama"]) is None  # ya está en la lista
    assert usable("ollama:", ["ollama"]) is None and usable("phi4", ["gemini", "ollama"]) is None
    assert usable("raro:phi4", ["gemini", "ollama"]) is None
    assert usable("  ollama:phi4  ", ["ollama"]) == "ollama:phi4"


def test_the_search_field_has_an_id_and_a_name():
    # Sin id ni name el navegador avisa (y no puede recordar ni autocompletar el campo).
    palette = js("palette.js")

    assert 'input.id = "palette-input"' in palette and 'input.name = "palette-search"' in palette


def test_reopening_the_palette_shows_the_previous_list_while_it_refreshes():
    palette = js("palette.js")

    assert "state.loaded" in palette and "state = { ...state, loaded: false }" not in palette


# --- Favoritos y agrupación por proveedor ----------------------------------------


def layout(favorites, query="", up=None):
    return run_node(
        f"window.epsilonPalette.layout({json.dumps(MODELS)}, {json.dumps(favorites)}, {json.dumps(query)}, {json.dumps(up)})"
    )


def summary(rows):
    return [row["text"] if row["type"] == "heading" else row["id"] + ("*" if row["favorite"] else "") for row in rows]


@pytest.mark.skipif(not NODE, reason="node no está instalado")
def test_an_empty_search_shows_favorites_first_and_then_one_group_per_provider():
    rows = layout(["ollama:qwen3:8b"])

    assert summary(rows) == [
        "Favoritos", "ollama:qwen3:8b*",
        "gemini", "gemini:gemini-3.1-flash-lite",
        "ollama", "ollama:qwen3:8b*", "ollama:llama3.1:8b",
    ]


@pytest.mark.skipif(not NODE, reason="node no está instalado")
def test_without_favorites_there_is_no_favorites_heading():
    assert summary(layout([]))[0] == "gemini"


@pytest.mark.skipif(not NODE, reason="node no está instalado")
def test_typing_gives_a_flat_filtered_list_with_favorites_first_and_no_headings():
    rows = layout(["ollama:llama3.1:8b"], query="8b")

    assert summary(rows) == ["ollama:llama3.1:8b*", "ollama:qwen3:8b"]
    assert all(row["type"] == "model" for row in rows)


@pytest.mark.skipif(not NODE, reason="node no está instalado")
def test_a_favorite_of_a_provider_that_did_not_answer_is_kept_but_marked_down():
    rows = [r for r in layout(["ollama:phi4", "gemini:otro"], up=["gemini"]) if r["type"] == "model" and r["section"] == "favorites"]

    assert [(r["id"], r["down"]) for r in rows] == [("ollama:phi4", True), ("gemini:otro", False)]
    assert not any(r.get("down") for r in layout(["ollama:phi4"], up=None))  # sin saber todavía, nada se apaga


def test_ctrl_f_and_the_star_save_favorites_through_the_server():
    palette = js("palette.js")

    assert '"/api/models/favorites"' in palette and 'method: "PUT"' in palette
    assert 'event.key.toLowerCase() === "f"' in palette and "toggleFavorite" in palette
    assert "event.preventDefault()" in palette  # que Ctrl+F no abra la búsqueda del navegador
    css = static_path("style.css").read_text(encoding="utf-8")
    assert ".palette-heading" in css and ".palette-star" in css
