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


# --- abrir Chromium si está cerrado ------------------------------------------------------------


class Launcher:
    """Reloj, procesos y puerto de mentira: el puerto responde recién después de `up_after` intentos (None = nunca)."""

    def __init__(self, monkeypatch, chromium_running=False, up_after=None, systemd_code=0):
        self.commands, self.attempts, self.now = [], 0, [0.0]
        self.chromium_running, self.up_after, self.systemd_code = chromium_running, up_after, systemd_code
        monkeypatch.setattr(navegador, "_http", self.http)
        monkeypatch.setattr(navegador, "_run", self.run)
        monkeypatch.setattr(navegador.time, "monotonic", lambda: self.now[0])
        monkeypatch.setattr(navegador.time, "sleep", lambda seconds: self.now.__setitem__(0, self.now[0] + seconds))

    def http(self, method, path):
        if path != "/json/version":
            raise AssertionError(path)
        self.attempts += 1
        if self.up_after is None or self.attempts <= self.up_after:
            raise navegador.NavegadorError("No pude hablar con Chromium en el puerto 9222.")
        return {"Browser": "Chrome/154.0.0.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x"}

    def run(self, cmd):
        self.commands.append(cmd)
        code = 0 if (cmd[0] == "pgrep") == self.chromium_running else 1
        if cmd[0] == "systemd-run":
            code = self.systemd_code
        return type("Done", (), {"returncode": code})()


def test_ensure_running_does_nothing_when_the_port_answers(monkeypatch):
    launcher = Launcher(monkeypatch, up_after=0)

    navegador.ensure_running()

    assert launcher.commands == []


def test_ensure_running_never_touches_a_chromium_that_runs_without_the_port(monkeypatch):
    launcher = Launcher(monkeypatch, chromium_running=True)

    with pytest.raises(navegador.NavegadorError, match="reiniciar"):
        navegador.ensure_running()

    assert not any(cmd[0] == "systemd-run" for cmd in launcher.commands)


def test_ensure_running_launches_chromium_outside_the_service_and_waits_for_the_port(monkeypatch):
    launcher = Launcher(monkeypatch, up_after=3)

    navegador.ensure_running()

    assert ["systemd-run", "--user", "--collect", "chromium"] in launcher.commands


def test_ensure_running_gives_up_if_the_port_never_answers(monkeypatch):
    Launcher(monkeypatch, up_after=None)

    with pytest.raises(navegador.NavegadorError):
        navegador.ensure_running()


def test_ensure_running_reports_a_failed_launch(monkeypatch):
    Launcher(monkeypatch, systemd_code=1)

    with pytest.raises(navegador.NavegadorError, match="lanzar"):
        navegador.ensure_running()


def test_open_url_makes_sure_chromium_is_running_first(monkeypatch):
    FakeChromium(monkeypatch)
    called = []
    monkeypatch.setattr(navegador, "ensure_running", lambda: called.append(1))

    navegador.open_url("https://music.youtube.com/")

    assert called == [1]


# --- registro de acciones -------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from epsilon.data import notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    return tmp_path


def test_audit_appends_one_line_per_action(data_dir):
    navegador.audit("nav_abrir", "music.youtube.com", "Abrí la pestaña ABCDEF", asked=False)
    navegador.audit("nav_clic", "otro-sitio.com", "Hice clic", asked=True)

    lines = (data_dir / "browser.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "nav_abrir" in lines[0] and "music.youtube.com" in lines[0] and "directo" in lines[0]
    assert "nav_clic" in lines[1] and "preguntó" in lines[1]


def test_audit_redacts_keys_and_keeps_one_line(data_dir, monkeypatch):
    key = "AIzaSyD-ejemplo_de_clave_larga_QzXw"
    monkeypatch.setenv("GEMINI_API_KEY", key)

    navegador.audit("nav_leer", "a.com", f"línea uno\nlínea dos con {key}", asked=False)

    text = (data_dir / "browser.log").read_text(encoding="utf-8")
    assert key not in text and len(text.splitlines()) == 1


def test_audit_trims_long_results(data_dir):
    navegador.audit("nav_leer", "a.com", "x" * 1000, asked=False)

    assert len((data_dir / "browser.log").read_text(encoding="utf-8")) < 400


def test_audit_never_breaks_the_action_if_it_cannot_write(tmp_path, monkeypatch):
    from epsilon.data import notes

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(notes, "DATA_DIR", blocker.resolve())

    navegador.audit("nav_leer", "a.com", "ok", asked=False)  # no lanza
