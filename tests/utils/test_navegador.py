import pytest

from epsilon.utils import navegador


class FakeChromium:
    """Un Chromium de mentira: responde lo que `navegador._http` pediría al puerto 9222."""

    def __init__(self, monkeypatch, tabs=(), redirects=None, addresses=None):
        self.tabs = [dict(t) for t in tabs]
        self.redirects = redirects or {}
        self.addresses = addresses or {}
        self.calls = []
        self.version = {"Browser": "Chrome/154.0.0.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"}
        monkeypatch.setattr(navegador, "_http", self.http)
        monkeypatch.setattr(navegador, "_resolve", lambda host: self.addresses.get(host, ["142.250.0.1"]))
        monkeypatch.setattr(navegador.time, "sleep", lambda seconds: None)

    def http(self, method, path):
        self.calls.append((method, path))
        if path == "/json/version":
            return self.version
        if path == "/json/list":
            return self.tabs
        if method == "PUT" and path.startswith("/json/new?"):
            url = path.split("?", 1)[1]
            tab = page("NEWTAB123456", "", self.redirects.get(url, url))
            self.tabs.append(tab)
            return tab
        if path.startswith("/json/close/"):
            return "Target is closing"
        raise AssertionError(f"pedido inesperado: {method} {path}")


def page(tab_id, title, url):
    return {
        "id": tab_id,
        "title": title,
        "url": url,
        "type": "page",
        "webSocketDebuggerUrl": f"ws://127.0.0.1:9222/devtools/page/{tab_id}",
    }


def test_tabs_hides_personal_tabs_and_counts_them(monkeypatch):
    FakeChromium(monkeypatch, [page("AAAA1111", "Banco", "https://mi-banco.com/"), page("BBBB2222", "Música", "https://music.youtube.com/")])

    visible, hidden = navegador.tabs()

    assert [t.id for t in visible] == ["BBBB2222"] and hidden == 1


def test_tabs_shows_a_tab_epsilon_opened_even_on_an_untrusted_site(monkeypatch):
    FakeChromium(monkeypatch, [page("AAAA1111", "Otro", "https://otro-sitio.com/")])
    navegador._opened.add("AAAA1111")

    visible, hidden = navegador.tabs()

    assert [t.id for t in visible] == ["AAAA1111"] and hidden == 0


def test_tabs_never_shows_a_blocked_site_even_if_epsilon_opened_it(monkeypatch):
    FakeChromium(monkeypatch, [page("AAAA1111", "Google", "https://accounts.google.com/signin")])
    navegador._opened.add("AAAA1111")

    visible, hidden = navegador.tabs()

    assert visible == [] and hidden == 1


def test_resolve_tab_cannot_name_a_hidden_tab(monkeypatch):
    FakeChromium(monkeypatch, [page("AAAA1111", "Banco", "https://mi-banco.com/")])

    with pytest.raises(navegador.NavegadorError):
        navegador.resolve_tab("AAAA11")


def test_resolve_tab_rejects_ambiguous_and_short_prefixes(monkeypatch):
    FakeChromium(monkeypatch, [page("AB12CC", "Uno", "https://music.youtube.com/"), page("AB12DD", "Dos", "https://www.youtube.com/")])

    with pytest.raises(navegador.NavegadorError):
        navegador.resolve_tab("AB12")
    with pytest.raises(navegador.NavegadorError):
        navegador.resolve_tab("AB")
    assert navegador.resolve_tab("AB12CC").title == "Uno"


def test_tab_short_id_is_the_first_six_characters():
    assert navegador.Tab("ABCDEF123", "t", "u", "h").short == "ABCDEF"


def test_check_browser_rejects_something_that_is_not_chromium(monkeypatch):
    chromium = FakeChromium(monkeypatch)
    chromium.version = {"Browser": "Firefox/1", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x"}

    with pytest.raises(navegador.NavegadorError):
        navegador.check_browser()


def test_check_browser_rejects_a_websocket_that_points_elsewhere(monkeypatch):
    chromium = FakeChromium(monkeypatch)
    chromium.version = {"Browser": "Chrome/154", "webSocketDebuggerUrl": "ws://10.0.0.2:9222/x"}

    with pytest.raises(navegador.NavegadorError):
        navegador.check_browser()


def test_a_closed_port_says_so_in_spanish(monkeypatch):
    def down(method, path):
        raise navegador.NavegadorError("No pude hablar con Chromium en el puerto 9222.")

    monkeypatch.setattr(navegador, "_http", down)

    with pytest.raises(navegador.NavegadorError, match="9222"):
        navegador.check_browser()


def test_tabs_survives_garbage_from_the_list_endpoint(monkeypatch):
    chromium = FakeChromium(monkeypatch)
    chromium.tabs = "basura"

    with pytest.raises(navegador.NavegadorError):
        navegador.tabs()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "chrome://settings", "http://localhost:8765", "http://127.0.0.1:9222"])
def test_open_url_rejects_dangerous_urls_without_opening_anything(monkeypatch, url):
    chromium = FakeChromium(monkeypatch, addresses={"localhost": ["127.0.0.1"], "127.0.0.1": ["127.0.0.1"]})

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url(url)

    assert not any(method == "PUT" for method, _ in chromium.calls)


@pytest.mark.parametrize("addresses", [["10.0.0.5"], []])
def test_open_url_rejects_sites_on_the_local_network_or_unresolved(monkeypatch, addresses):
    chromium = FakeChromium(monkeypatch, addresses={"x.com": addresses})

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url("https://x.com/")

    assert not any(method == "PUT" for method, _ in chromium.calls)


def test_open_url_rejects_blocked_sites(monkeypatch):
    chromium = FakeChromium(monkeypatch)

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url("https://mail.google.com/")

    assert not any(method == "PUT" for method, _ in chromium.calls)


def test_open_url_closes_the_tab_when_a_redirect_lands_on_a_blocked_site(monkeypatch):
    chromium = FakeChromium(monkeypatch, redirects={"https://corto.com/a": "https://accounts.google.com/"})

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url("https://corto.com/a")

    assert ("GET", "/json/close/NEWTAB123456") in chromium.calls


def test_open_url_closes_the_tab_when_a_redirect_lands_on_the_local_network(monkeypatch):
    chromium = FakeChromium(monkeypatch, redirects={"https://corto.com/a": "http://192.168.1.1/"})

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url("https://corto.com/a")

    assert ("GET", "/json/close/NEWTAB123456") in chromium.calls


def test_open_url_returns_the_tab_and_remembers_it(monkeypatch):
    FakeChromium(monkeypatch)

    tab = navegador.open_url("https://music.youtube.com/")

    assert tab.host == "music.youtube.com" and tab.id in navegador._opened
