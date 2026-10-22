import pytest

from epsilon import tools
from epsilon.ai import browser_guard as guard, chat
from epsilon.utils import navegador


def tool(call_id):
    return tools.route(call_id)


@pytest.fixture
def turn():
    return chat.Turn("20261020-140000-aaaa", [])


@pytest.fixture
def page(monkeypatch):
    """Una pestaña de mentira: `page.host` es su sitio y `page.label` el rótulo del elemento 1."""
    state = type("State", (), {"host": "music.youtube.com", "label": "Reproducir", "sensitive": False})()
    monkeypatch.setattr(navegador, "tab_host", lambda ref: state.host)
    monkeypatch.setattr(navegador, "element", lambda ref, n: navegador.Element(n, state.label, "button", 1.0, 2.0, state.sensitive))
    return state


@pytest.mark.parametrize(
    "url, verdict",
    [
        ("https://music.youtube.com/watch?v=1", "run"),
        ("https://otro-sitio.com/", "ask"),
        ("https://music.youtube.com@otro-sitio.com/", "ask"),  # el sitio real es el de después de la arroba
        ("https://eviltyoutube.com/", "ask"),  # no es subdominio de youtube.com
    ],
)
def test_abrir_only_runs_alone_on_trusted_sites(turn, url, verdict):
    assert guard.review(tool("navegador.abrir"), url, turn).verdict == verdict


def test_abrir_question_shows_the_real_site(turn):
    review = guard.review(tool("navegador.abrir"), "https://music.youtube.com@otro-sitio.com/", turn)

    assert "otro-sitio.com" in review.shown


def test_clic_on_a_harmless_button_of_a_trusted_site_runs(turn, page):
    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "run"


def test_clic_on_a_send_or_delete_button_asks_even_on_a_trusted_site(turn, page):
    page.label = "Publicar comentario"

    review = guard.review(tool("navegador.clic"), "abcdef 1", turn)

    assert review.verdict == "ask"
    assert "music.youtube.com" in review.shown and "Publicar comentario" in review.shown


def test_clic_asks_on_an_untrusted_site(turn, page):
    page.host = "sitio-raro.com"

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "ask"


def test_clic_asks_when_the_site_cannot_be_determined(turn, monkeypatch):
    def lost(ref):
        raise navegador.NavegadorError("No encuentro esa pestaña")

    monkeypatch.setattr(navegador, "tab_host", lost)

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "ask"


def test_clic_with_a_malformed_argument_has_nothing_to_approve(turn, page):
    assert guard.review(tool("navegador.clic"), "abcdef", turn).verdict == "run"  # el corredor lo rechaza sin tocar nada


def test_escribir_on_a_trusted_site_runs_and_its_question_shows_the_text(turn, page):
    page.label = "Buscar"
    assert guard.review(tool("navegador.escribir"), "abcdef 1\nmi supermix", turn).verdict == "run"

    page.host = "sitio-raro.com"
    review = guard.review(tool("navegador.escribir"), "abcdef 1\nmi supermix", turn)
    assert review.verdict == "ask" and "mi supermix" in review.shown and "sitio-raro.com" in review.shown


def test_leer_of_an_untrusted_site_taints_the_turn(turn, page):
    page.host = "sitio-raro.com"

    guard.after_run(tool("navegador.leer"), "abcdef", turn)

    assert turn.tainted is True


def test_leer_of_a_trusted_site_does_not_taint(turn, page):
    guard.after_run(tool("navegador.leer"), "abcdef", turn)

    assert turn.tainted is False


def test_a_tainted_turn_asks_before_any_action_but_not_before_reading(turn):
    turn.tainted = True

    assert guard.review(tool("notas.guardar"), "hola", turn).verdict == "ask"
    assert guard.review(tool("consultar.fecha"), "", turn).verdict == "run"


def test_a_clean_turn_leaves_other_tools_alone(turn):
    assert guard.review(tool("notas.guardar"), "hola", turn) == guard.Review()


def test_tab_cap_blocks_the_next_opening(turn):
    for _ in range(guard.MAX_NEW_TABS):
        guard.after_run(tool("navegador.abrir"), "https://music.youtube.com/", turn)

    review = guard.review(tool("navegador.abrir"), "https://music.youtube.com/", turn)

    assert review.verdict == "block" and review.message


def test_action_cap_blocks_any_browser_action(turn, page):
    for _ in range(guard.MAX_BROWSER_ACTIONS):
        guard.after_run(tool("navegador.clic"), "abcdef 1", turn)

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "block"
    assert guard.review(tool("navegador.leer"), "abcdef", turn).verdict == "block"
    assert guard.review(tool("consultar.fecha"), "", turn).verdict == "run"  # solo cuenta el navegador
