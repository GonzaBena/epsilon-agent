from urllib.parse import quote, unquote

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
        self.pending = {}  # id de pestaña -> lista de "estado|dirección" que va devolviendo location.href
        monkeypatch.setattr(navegador, "_cdp", self.cdp)
        monkeypatch.setattr(navegador.time, "sleep", lambda seconds: None)

    def cdp(self, ws_url, method, params=None):
        tab_id = ws_url.rsplit("/", 1)[1]
        staged = self.pending.get(tab_id)
        if staged:
            value = staged.pop(0) if len(staged) > 1 else staged[0]
        else:
            value = "complete|" + next(t["url"] for t in self.tabs if t["id"] == tab_id)
        return {"result": {"type": "string", "value": value}}

    def http(self, method, path):
        self.calls.append((method, path))
        if path == "/json/version":
            return self.version
        if path == "/json/list":
            return self.tabs
        if method == "PUT" and path.startswith("/json/new?"):
            url = unquote(path.split("?", 1)[1])
            self.requested = path
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


# --- pase de arreglos de la revisión final ---------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://127.0.0.1:9222/json/list", "http://192.168.1.1/", "file:///etc/passwd", "http://localhost:8765/"])
def test_tabs_hides_even_an_opened_tab_that_drifted_to_the_local_machine_or_network(monkeypatch, url):
    FakeChromium(monkeypatch, [page("AAAA1111", "x", url)])
    navegador._opened.add("AAAA1111")

    assert navegador.tabs() == ([], 1)
    with pytest.raises(navegador.NavegadorError):
        navegador.resolve_tab("AAAA11")


def test_open_url_checks_the_committed_address_not_the_requested_one(monkeypatch):
    chromium = FakeChromium(monkeypatch)
    chromium.pending["NEWTAB123456"] = ["loading|https://corto.com/a", "complete|https://accounts.google.com/"]

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url("https://corto.com/a")

    assert ("GET", "/json/close/NEWTAB123456") in chromium.calls


def test_open_url_escapes_the_whole_address_so_the_playlist_survives(monkeypatch):
    chromium = FakeChromium(monkeypatch)
    url = "https://music.youtube.com/watch?v=abc&list=RD123#t=1"

    navegador.open_url(url)

    assert chromium.requested == "/json/new?" + quote(url, safe="")


def test_trusted_matches_the_configured_sites(monkeypatch):
    assert navegador.trusted("music.youtube.com") and not navegador.trusted("eviltyoutube.com") and not navegador.trusted("")


def test_open_url_waits_past_the_initial_blank_document(monkeypatch):
    chromium = FakeChromium(monkeypatch)
    chromium.pending["NEWTAB123456"] = ["complete|about:blank", "loading|https://music.youtube.com/", "complete|https://music.youtube.com/"]

    tab = navegador.open_url("https://music.youtube.com/")

    assert tab.id in navegador._opened


# --- resolve_target: de un nombre suelto a una dirección ---


def history(monkeypatch, rows):
    monkeypatch.setattr(navegador, "_history", lambda: rows)
    # La lista de bloqueados no sale del .env de quien corre los tests.
    monkeypatch.setattr(navegador.config, "site_list", lambda key: ["mail.google.com"] if key == "BROWSER_BLOCKED_SITES" else [])


def test_resolve_target_leaves_full_addresses_alone():
    assert navegador.resolve_target(" https://x.com/a ") == "https://x.com/a"
    assert navegador.resolve_target("file:///etc/passwd") == "file:///etc/passwd"  # lo rechaza la revisión de la dirección


def test_resolve_target_adds_https_to_a_bare_domain_the_person_has_visited(monkeypatch):
    history(monkeypatch, [("https://www.youtube.com/", "YouTube", 10, 3)])
    assert navegador.resolve_target("youtube.com/watch?v=1") == "https://youtube.com/watch?v=1"


def test_resolve_target_refuses_a_domain_the_model_made_up(monkeypatch):
    history(monkeypatch, [("https://www.youtube.com/", "YouTube", 10, 3)])
    with pytest.raises(navegador.NavegadorError, match="no está en tu historial"):
        navegador.resolve_target("aulavirtualutn.com")


def test_resolve_target_resolves_a_phrase(monkeypatch):
    history(monkeypatch, [("https://aulasvirtuales.frba.utn.edu.ar/my/", "Aulas Virtuales UTN.BA", 80, 6)])
    assert navegador.resolve_target("aula virtual utn") == "https://aulasvirtuales.frba.utn.edu.ar/"


def test_resolve_target_looks_a_name_up_in_the_history(monkeypatch):
    history(monkeypatch, [("https://www.youtube.com/watch?v=secreto", "YouTube", 10, 3)])
    assert navegador.resolve_target("yt") == "https://youtube.com/"  # solo el sitio: nunca la página del historial


def test_resolve_target_never_picks_a_blocked_site(monkeypatch):
    history(monkeypatch, [("https://mail.google.com/", "Gmail", 900, 50)])
    with pytest.raises(navegador.NavegadorError, match="No encontré"):
        navegador.resolve_target("gm")


def test_resolve_target_explains_instead_of_searching(monkeypatch):
    history(monkeypatch, [])
    with pytest.raises(navegador.NavegadorError, match="pasame la dirección completa"):
        navegador.resolve_target("zzz")


def test_resolve_target_does_not_read_the_history_for_full_addresses(monkeypatch):
    def boom():
        raise AssertionError("no debía leer el historial")

    monkeypatch.setattr(navegador, "_history", boom)
    navegador.resolve_target("https://x.com/")


def test_history_reads_a_copy_in_read_only_mode(tmp_path):
    import sqlite3

    source = tmp_path / "History"
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INT, typed_count INT, last_visit_time INT, hidden INT)")
    db.executemany(
        "INSERT INTO urls (url, title, visit_count, typed_count, hidden) VALUES (?, ?, ?, ?, ?)",
        [("https://a.com/", "A", 5, 1, 0), ("https://oculta.com/", "O", 9, 9, 1), ("chrome://settings", "S", 9, 9, 0)],
    )
    db.commit()
    db.close()
    before = source.read_bytes()
    assert navegador.read_history(source) == [("https://a.com/", "A", 5, 1)]
    assert source.read_bytes() == before


def test_history_without_the_file_says_so(tmp_path):
    with pytest.raises(navegador.NavegadorError, match="historial"):
        navegador.read_history(tmp_path / "nada")
