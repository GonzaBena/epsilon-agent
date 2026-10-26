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
    state = type("State", (), {"host": "music.youtube.com", "label": "Reproducir", "sensitive": False, "href": "", "read_hosts": None})()
    monkeypatch.setattr(navegador, "tab_host", lambda ref: state.host)
    monkeypatch.setattr(navegador, "element", lambda ref, n: navegador.Element(n, state.label, "button", 1.0, 2.0, state.sensitive, state.href))
    monkeypatch.setattr(navegador, "last_read_hosts", lambda: state.read_hosts if state.read_hosts is not None else [state.host])
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


def test_a_page_that_navigates_to_a_trusted_site_after_being_read_still_taints(turn, page):
    page.read_hosts = ["sitio-raro.com", "music.youtube.com"]  # el sitio que se leyó, no el que quedó después

    guard.after_run(tool("navegador.leer"), "abcdef", turn)

    assert turn.tainted is True


def test_a_read_that_recorded_no_site_taints(turn, page):
    page.read_hosts = []

    guard.after_run(tool("navegador.leer"), "abcdef", turn)

    assert turn.tainted is True


def test_opening_an_untrusted_site_taints_because_its_text_will_be_read_next(turn):
    guard.after_run(tool("navegador.abrir"), "https://sitio-raro.com/", turn)

    assert turn.tainted is True


def test_a_click_that_leaves_the_trusted_sites_taints(turn, page):
    page.host = "sitio-raro.com"  # después del clic la pestaña quedó en otro sitio

    guard.after_run(tool("navegador.clic"), "abcdef 1", turn)

    assert turn.tainted is True


def test_a_click_that_stays_on_a_trusted_site_does_not_taint(turn, page):
    guard.after_run(tool("navegador.clic"), "abcdef 1", turn)

    assert turn.tainted is False


def test_clicking_a_link_to_an_untrusted_site_asks_and_shows_the_destination(turn, page):
    page.href = "https://evil.tld/x"

    review = guard.review(tool("navegador.clic"), "abcdef 1", turn)

    assert review.verdict == "ask" and "evil.tld" in review.shown


def test_clicking_a_link_to_a_blocked_or_local_destination_is_refused(turn, page):
    for href in ("https://accounts.google.com/x", "http://127.0.0.1:9222/json", "http://192.168.1.1/"):
        page.href = href
        assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "block", href


def test_clicking_a_link_inside_the_trusted_sites_runs(turn, page):
    page.href = "https://music.youtube.com/playlist?list=RD1"

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "run"


def test_clicking_a_mailto_link_asks(turn, page):
    page.href = "mailto:alguien@example.com"

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "ask"


def test_opening_a_trusted_address_that_carries_another_address_asks(turn):
    url = "https://www.youtube.com/redirect?q=https://evil.tld/?d=1"

    assert guard.review(tool("navegador.abrir"), url, turn).verdict == "ask"


def test_the_question_text_is_built_even_when_the_guard_would_not_ask(turn, page):
    review = guard.review(tool("navegador.clic"), "abcdef 1", turn)

    assert review.verdict == "run" and review.shown.startswith("Sitio: music.youtube.com") and "Reproducir" in review.shown


def test_sensitivity_looks_at_the_button_not_at_the_card_text_appended_to_it(turn, page):
    page.label = "Reproducir — Post Malone: Greatest Hits"

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "run"


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


def test_with_the_guard_off_an_untrusted_site_runs_without_asking(turn, page, monkeypatch):
    monkeypatch.setenv("BROWSER_GUARD", "no")
    page.host = "sitio-raro.com"

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "run"
    assert guard.review(tool("navegador.abrir"), "https://otro-sitio.com/", turn).verdict == "run"


def test_with_the_guard_off_a_tainted_turn_and_sensitive_labels_do_not_ask(turn, page, monkeypatch):
    monkeypatch.setenv("BROWSER_GUARD", "no")
    page.label = "Eliminar cuenta"
    turn.tainted = True

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "run"


def test_with_the_guard_off_blocked_links_and_the_caps_still_hold(turn, page, monkeypatch):
    monkeypatch.setenv("BROWSER_GUARD", "no")
    page.href = "http://192.168.1.1/"

    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "block"
    page.href = ""
    turn.browser_actions = guard.MAX_BROWSER_ACTIONS
    assert guard.review(tool("navegador.clic"), "abcdef 1", turn).verdict == "block"


# --- buscar en Google ---


def test_search_question_shows_what_goes_to_google(turn):
    review = guard.review(tool("navegador.buscar"), "  horarios   utn frba ", turn)

    assert review.verdict == "run" and review.shown == "Buscar en Google: «horarios utn frba»"  # el permiso (ask) decide


def test_search_cap_blocks_the_fourth_search_even_with_the_guard_off(turn, monkeypatch):
    monkeypatch.setenv("BROWSER_GUARD", "no")
    for _ in range(guard.MAX_SEARCHES):
        guard.after_run(tool("navegador.buscar"), "algo", turn)

    review = guard.review(tool("navegador.buscar"), "algo más", turn)

    assert review.verdict == "block" and "preguntale a la persona" in review.message
    assert turn.browser_actions == guard.MAX_SEARCHES  # también cuentan como acciones del navegador


def test_search_results_taint_the_turn(turn):
    guard.after_run(tool("navegador.buscar"), "algo", turn)

    assert turn.tainted and turn.browser_tabs == 0  # la pestaña de resultados se cierra: no cuenta como nueva
