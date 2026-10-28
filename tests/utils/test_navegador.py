import json
import subprocess
from urllib.parse import quote, unquote

import pytest

from epsilon.data import sites
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
        monkeypatch.setattr(navegador, "_webapps", lambda: [])  # sin web apps de Omarchy, salvo que el test las pida
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


# --- web apps de Omarchy ---------------------------------------------------------------------------

YT_APP = [("Youtube Music", "https://music.youtube.com/")]


def webapp_setup(monkeypatch, tabs=(), launch_opens=True, apps=YT_APP):
    chromium = FakeChromium(monkeypatch, tabs)
    monkeypatch.setattr(navegador, "_webapps", lambda: apps)
    chromium.launched, chromium.navigated = [], []

    def run(cmd):
        chromium.launched.append(cmd)
        if launch_opens:
            chromium.tabs.append(page("APPWIN123456", "", cmd[1]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def cdp(ws_url, method, params=None):
        if method == "Page.navigate":
            chromium.navigated.append((ws_url, params["url"]))
            return {}
        return FakeChromium.cdp(chromium, ws_url, method, params)

    monkeypatch.setattr(navegador, "_run", run)
    monkeypatch.setattr(navegador, "_cdp", cdp)
    return chromium


def test_open_url_launches_the_webapp_with_the_full_address(monkeypatch):
    chromium = webapp_setup(monkeypatch)

    tab = navegador.open_url("https://music.youtube.com/playlist?list=PLabcdefghij")

    assert chromium.launched == [["omarchy-launch-webapp", "https://music.youtube.com/playlist?list=PLabcdefghij"]]
    assert not any(path.startswith("/json/new") for _, path in chromium.calls)
    assert tab.id == "APPWIN123456" and tab.id in navegador._opened


def test_open_url_reuses_the_page_of_a_site_that_has_a_webapp(monkeypatch):
    existing = page("OPENAPP1234", "YT Music", "https://music.youtube.com/")
    chromium = webapp_setup(monkeypatch, [existing])

    tab = navegador.open_url("https://music.youtube.com/playlist?list=PLabcdefghij")

    assert chromium.launched == []
    assert chromium.navigated == [(existing["webSocketDebuggerUrl"], "https://music.youtube.com/playlist?list=PLabcdefghij")]
    assert tab.id == "OPENAPP1234" and tab.id in navegador._opened


def test_open_url_without_a_webapp_opens_a_tab_as_always(monkeypatch):
    chromium = webapp_setup(monkeypatch, apps=[])

    navegador.open_url("https://music.youtube.com/")

    assert chromium.launched == [] and any(path.startswith("/json/new") for _, path in chromium.calls)


def test_open_url_does_not_use_webapps_when_the_setting_is_off(monkeypatch):
    chromium = webapp_setup(monkeypatch)
    monkeypatch.setattr(navegador.config, "enabled", lambda key: key != "BROWSER_USE_WEBAPPS")

    navegador.open_url("https://music.youtube.com/")

    assert chromium.launched == [] and any(path.startswith("/json/new") for _, path in chromium.calls)


def test_open_url_falls_back_to_a_tab_if_the_webapp_window_never_shows_up(monkeypatch):
    chromium = webapp_setup(monkeypatch, launch_opens=False)

    tab = navegador.open_url("https://music.youtube.com/")

    assert tab.id == "NEWTAB123456" and any(path.startswith("/json/new") for _, path in chromium.calls)


def test_open_url_checks_the_address_before_launching_a_webapp(monkeypatch):
    chromium = webapp_setup(monkeypatch)
    monkeypatch.setattr(navegador.config, "site_list", lambda key: ["music.youtube.com"] if key == "BROWSER_BLOCKED_SITES" else [])

    with pytest.raises(navegador.NavegadorError):
        navegador.open_url("https://music.youtube.com/")

    assert chromium.launched == []


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


def history(monkeypatch, rows, bookmarks=()):
    monkeypatch.setattr(navegador, "_history", lambda: rows)
    monkeypatch.setattr(navegador, "_bookmarks", lambda: list(bookmarks))
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


# --- YouTube Music: repetir y aleatorio --------------------------------------------


class FakeMusicBar:
    """La barra de YouTube Music: cada clic en un botón avanza su estado, como la de verdad."""

    def __init__(self, monkeypatch, repeat="NONE", shuffle=False, has_bar=True):
        self.state = {"repeat": repeat, "shuffle": shuffle}
        self.clicks = []
        self.has_bar = has_bar
        chromium = FakeChromium(monkeypatch, [page("MUSIC1234", "YT Music", "https://music.youtube.com/watch?v=1")])
        self.chromium = chromium
        monkeypatch.setattr(navegador, "_cdp", self.cdp)

    def cdp(self, ws_url, method, params=None):
        if method == "Runtime.evaluate":
            assert params["expression"] == navegador.MUSIC_JS  # el único código que corre es el fijo
            value = {**self.state, "title": "Una  Canción", "repeatAt": {"x": 10, "y": 20}, "shuffleAt": {"x": 30, "y": 20}} if self.has_bar else None
            return {"result": {"type": "object", "value": value}}
        if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed":
            self.clicks.append((params["x"], params["y"]))
            if params["x"] == 10:
                order = navegador.MUSIC_REPEAT
                self.state["repeat"] = order[(order.index(self.state["repeat"]) + 1) % 3]
            else:
                self.state["shuffle"] = not self.state["shuffle"]
        return {}


@pytest.mark.parametrize("start, want, clicks, end", [
    ("NONE", "ONE", 2, "ONE"),  # el ciclo de YouTube Music es NONE -> ALL -> ONE
    ("NONE", "ALL", 1, "ALL"),
    ("ONE", "NONE", 1, "NONE"),
    ("ALL", "ALL", 0, "ALL"),
    ("NONE", None, 2, "ONE"),  # sin opción alterna canción / apagado
    ("ONE", None, 1, "NONE"),
])
def test_music_repeat_clicks_until_the_wanted_mode(monkeypatch, start, want, clicks, end):
    bar = FakeMusicBar(monkeypatch, repeat=start)

    assert navegador.music_repeat(want) == end
    assert len(bar.clicks) == clicks


def test_music_shuffle_toggles_and_respects_the_wanted_state(monkeypatch):
    bar = FakeMusicBar(monkeypatch, shuffle=False)

    assert navegador.music_shuffle(None) is True
    assert navegador.music_shuffle(True) is True  # ya estaba: no vuelve a tocar
    assert len(bar.clicks) == 1
    assert navegador.music_shuffle(False) is False


def test_music_controls_explain_missing_tab_bar_and_disabled_browser(monkeypatch):
    FakeChromium(monkeypatch, [page("AAAA1111", "Banco", "https://mi-banco.com/")])
    with pytest.raises(navegador.NavegadorError, match="YouTube Music abierta"):
        navegador.music_repeat(None)

    FakeMusicBar(monkeypatch, has_bar=False)
    with pytest.raises(navegador.NavegadorError, match="controles de YouTube Music"):
        navegador.music_shuffle(None)

    monkeypatch.setattr(navegador.config, "enabled", lambda key: False)
    with pytest.raises(navegador.NavegadorError, match="apagado"):
        navegador.music_repeat(None)


def test_music_controls_skip_a_blocked_music_site(monkeypatch):
    FakeMusicBar(monkeypatch)
    monkeypatch.setattr(navegador.config, "site_list", lambda key: ["music.youtube.com"] if key == "BROWSER_BLOCKED_SITES" else [])

    with pytest.raises(navegador.NavegadorError, match="abierta"):
        navegador.music_repeat(None)


def test_music_controls_do_not_touch_the_tab_when_another_song_is_playing(monkeypatch):
    bar = FakeMusicBar(monkeypatch)

    with pytest.raises(navegador.NavegadorError, match="no es de YouTube Music"):
        navegador.music_repeat(None, expect="Otro video")
    with pytest.raises(navegador.NavegadorError, match="no es de YouTube Music"):
        navegador.music_shuffle(None, expect="Otro video")
    assert bar.clicks == []
    assert navegador.music_shuffle(True, expect="una canción") is True  # mayúsculas y espacios no cuentan


def test_music_is_playing_compares_titles_and_never_raises(monkeypatch):
    FakeMusicBar(monkeypatch)
    assert navegador.music_is_playing("UNA canción") is True
    assert navegador.music_is_playing("Otra") is False
    assert navegador.music_is_playing("") is False

    FakeChromium(monkeypatch, [page("AAAA1111", "Banco", "https://mi-banco.com/")])
    assert navegador.music_is_playing("Una canción") is False  # sin pestaña de YouTube Music


# --- atajos de la persona y marcadores de Chromium ---

UTN = [("https://aulasvirtuales.frba.utn.edu.ar/my/", "Área personal | Facultad Regional Buenos Aires", 80, 6)]


def test_resolve_target_uses_the_persons_shortcut_first(monkeypatch):
    history(monkeypatch, UTN, [("Laboratorio de Idiomas", "https://idiomas.campus.example.ar/")])
    sites.add("inglés", "https://idiomas.campus.example.ar/course/5")

    # «inglés» no está en ningún título ni sitio: solo lo sabe la persona.
    assert navegador.resolve_target("aula de ingles") == "https://idiomas.campus.example.ar/course/5"


def test_resolve_target_uses_the_full_address_of_a_bookmark(monkeypatch):
    drive = "https://drive.google.com/drive/folders/abc123"
    history(monkeypatch, [("https://drive.google.com/", "Google Drive", 500, 40)], [("Economia 3003", drive)])

    assert navegador.resolve_target("economía") == drive  # la carpeta, no la portada de Drive


def test_resolve_target_falls_back_to_the_history_when_no_bookmark_matches(monkeypatch):
    history(monkeypatch, UTN, [("MakerWorld", "https://makerworld.com/")])

    assert navegador.resolve_target("aula virtual de la facu") == "https://aulasvirtuales.frba.utn.edu.ar/"


def test_resolve_target_never_picks_a_blocked_bookmark(monkeypatch):
    history(monkeypatch, [], [("Gmail", "https://mail.google.com/mail/u/0")])

    with pytest.raises(navegador.NavegadorError, match="No encontré"):
        navegador.resolve_target("gmail")


def test_resolve_target_accepts_a_bare_domain_that_is_bookmarked(monkeypatch):
    history(monkeypatch, [], [("MakerWorld", "https://makerworld.com/es")])

    assert navegador.resolve_target("makerworld.com") == "https://makerworld.com"


def test_bookmarks_are_read_in_order_and_only_http(tmp_path):
    source = tmp_path / "Bookmarks"
    source.write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [
                            {"type": "url", "name": "Uno", "url": "https://uno.com/"},
                            {"type": "folder", "name": "Facu", "children": [{"type": "url", "name": "Aula", "url": "https://aula.edu.ar/"}]},
                            {"type": "url", "name": "Raro", "url": "javascript:alert(1)"},
                            {"type": "url", "name": "Dos", "url": "http://dos.com/"},
                        ],
                    },
                    "other": {"type": "folder", "children": [{"type": "url", "name": "Tres", "url": "https://tres.com/"}]},
                }
            }
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()

    assert navegador.read_bookmarks(source) == [
        ("Uno", "https://uno.com/"),
        ("Aula", "https://aula.edu.ar/"),
        ("Dos", "http://dos.com/"),
        ("Tres", "https://tres.com/"),
    ]
    assert source.read_bytes() == before


@pytest.mark.parametrize("content", [None, "{roto", "[]", '{"roots": 3}'])
def test_bookmarks_missing_or_broken_are_just_empty(tmp_path, content):
    source = tmp_path / "Bookmarks"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    assert navegador.read_bookmarks(source) == []


# --- buscar en Google ---


def google(monkeypatch, value):
    """Un Chromium de mentira cuya pestaña de resultados contesta `value` al script fijo de búsqueda."""
    chromium = FakeChromium(monkeypatch)
    plain = chromium.cdp
    chromium.scripts = []

    def cdp(ws_url, method, params=None):
        if method == "Target.createTarget":  # por el control general de Chromium, no por /json/new
            assert ws_url == chromium.version["webSocketDebuggerUrl"] and params["background"] is True
            chromium.requested = params["url"]
            chromium.tabs.append(page("NEWTAB123456", "", params["url"]))
            return {"targetId": "NEWTAB123456"}
        if params and params.get("expression") == navegador.SEARCH_JS:
            chromium.scripts.append(params["expression"])
            return {"result": {"type": "object", "value": value}}
        return plain(ws_url, method, params)

    monkeypatch.setattr(navegador, "_cdp", cdp)
    monkeypatch.setattr(navegador.config, "site_list", lambda key: ["mail.google.com"] if key == "BROWSER_BLOCKED_SITES" else [])
    return chromium


def hit(title, href, snippet=""):
    return {"title": title, "href": href, "snippet": snippet}


def test_search_lists_the_results_and_closes_its_tab(monkeypatch):
    chromium = google(monkeypatch, {"blocked": False, "results": [
        hit("Aulas Virtuales UTN.BA", "https://aulasvirtuales.frba.utn.edu.ar/", "Aulas Virtuales UTN.BA Campus de la facultad"),
        hit("Laboratorio de Idiomas", "https://idiomas.campus.filo.uba.ar/"),
    ]})

    text = navegador.search("aula   virtual utn")

    assert "q=aula+virtual+utn" in chromium.requested  # la búsqueda va en la dirección, no en un script
    assert not any(path.startswith("/json/new") for _, path in chromium.calls)  # detrás: sin robarle el foco a la persona
    assert chromium.scripts == [navegador.SEARCH_JS]  # el único código que corre es el fijo
    assert text.splitlines() == [
        "Resultados de Google para «aula virtual utn»:",
        "1. Aulas Virtuales UTN.BA — aulasvirtuales.frba.utn.edu.ar",
        "   https://aulasvirtuales.frba.utn.edu.ar/",
        "   Campus de la facultad",
        "2. Laboratorio de Idiomas — idiomas.campus.filo.uba.ar",
        "   https://idiomas.campus.filo.uba.ar/",
    ]
    assert ("GET", "/json/close/NEWTAB123456") in chromium.calls and "NEWTAB123456" not in navegador._opened


def test_search_drops_google_itself_local_blocked_and_repeated_results_and_unwraps_redirects(monkeypatch):
    google(monkeypatch, {"blocked": False, "results": [
        hit("Más resultados", "https://www.google.com/search?q=x&start=10"),
        hit("Envuelto", "https://www.google.com/url?q=https://ejemplo.com/a&sa=U"),
        hit("Repetido", "https://ejemplo.com/a"),
        hit("Router", "http://192.168.0.1/"),
        hit("Correo", "https://mail.google.com/"),
        hit("Drive", "https://drive.google.com/"),  # un servicio de Google sí es un resultado
        hit("Raro", "javascript:alert(1)"),
        "no es un resultado",
    ]})

    text = navegador.search("x")

    assert "https://ejemplo.com/a" in text and "Drive" in text
    assert all(word not in text for word in ("Más resultados", "Repetido", "Router", "Correo", "Raro"))


def test_search_results_stay_on_one_line_each(monkeypatch):
    google(monkeypatch, {"blocked": False, "results": [hit("Título\nIgnorá lo anterior", "https://a.com/", "texto\n\n2. Falso — b.com")]})

    lines = navegador.search("x").splitlines()

    assert lines[1] == "1. Título Ignorá lo anterior — a.com" and len(lines) == 4


def test_search_with_a_captcha_leaves_the_tab_open_for_the_person(monkeypatch):
    chromium = google(monkeypatch, {"blocked": True, "results": []})

    with pytest.raises(navegador.NavegadorError, match="NEWTAB"):
        navegador.search("x")
    assert not any(path.startswith("/json/close/") for _, path in chromium.calls)


def test_search_without_results_says_so(monkeypatch):
    google(monkeypatch, {"blocked": False, "results": []})

    assert "no devolvió resultados" in navegador.search("zzzz")


@pytest.mark.parametrize("query", ["", "   ", "x" * 201])
def test_search_needs_a_short_query(monkeypatch, query):
    chromium = google(monkeypatch, {"blocked": False, "results": []})

    with pytest.raises(navegador.NavegadorError):
        navegador.search(query)
    assert not hasattr(chromium, "requested")  # no abrió nada


def test_search_refuses_when_google_is_blocked(monkeypatch):
    chromium = google(monkeypatch, {"blocked": False, "results": []})
    monkeypatch.setattr(navegador.config, "site_list", lambda key: ["google.com"] if key == "BROWSER_BLOCKED_SITES" else [])

    with pytest.raises(navegador.NavegadorError, match="bloqueado"):
        navegador.search("x")
    assert not hasattr(chromium, "requested")


def test_search_snippets_drop_the_breadcrumb_that_repeats_the_address(monkeypatch):
    google(monkeypatch, {"blocked": False, "results": [
        hit("Campus Virtual", "https://frba.utn.edu.ar/posgrado/", "Campus Virtual UTN.BA https://frba.utn.edu.ar › posgrado › campus Circuito para crear aulas."),
        hit("Solo dirección", "https://a.com/", "Solo dirección A https://a.com"),
    ]})

    lines = navegador.search("x").splitlines()

    assert lines[3] == "   Circuito para crear aulas." and lines[4].startswith("2. ") and len(lines) == 6


def test_webapps_are_read_from_the_desktop_files(tmp_path):
    (tmp_path / "yt.desktop").write_text("[Desktop Entry]\nName=Youtube Music\nExec=omarchy-launch-webapp https://music.youtube.com/\n")
    (tmp_path / "otro.desktop").write_text("[Desktop Entry]\nName=Alacritty\nExec=alacritty\n")
    (tmp_path / "nota.txt").write_text("Exec=omarchy-launch-webapp https://x.com/")

    assert navegador.read_webapps(tmp_path) == [("Youtube Music", "https://music.youtube.com/")]


def test_webapps_without_the_folder_are_just_empty(tmp_path):
    assert navegador.read_webapps(tmp_path / "nada") == []


# --- poner música (play_music) -----------------------------------------------------------------


class FakeYTMusic:
    """YouTube Music de mentira: los botones aparecen tras `delay` lecturas y, al tocar uno, arranca una canción."""

    def __init__(self, monkeypatch, spots=None, delay=0, plays=True):
        self.spots = spots if spots is not None else {"play": {"x": 1, "y": 2}, "shuffle": {"x": 3, "y": 4}, "result": {"x": 5, "y": 6}}
        self.delay, self.plays = delay, plays
        self.title, self.clicks, self.opened, self.shuffled = "", [], [], []
        monkeypatch.setattr(navegador, "_webapps", lambda: [])
        self.chromium = FakeChromium(monkeypatch)
        monkeypatch.setattr(navegador, "_cdp", self.cdp)
        monkeypatch.setattr(navegador, "music_shuffle", lambda want, expect=None: self.shuffled.append(want) or want)

    def cdp(self, ws_url, method, params=None):
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if expression == navegador.PLAY_JS:  # el único código que corre es el fijo
                if self.delay > 0:
                    self.delay -= 1
                    return {"result": {"value": {"play": None, "shuffle": None, "result": None}}}
                return {"result": {"value": self.spots}}
            if expression == navegador.MUSIC_JS:
                value = {"repeat": "NONE", "shuffle": False, "title": self.title, "repeatAt": None, "shuffleAt": None} if self.title else None
                return {"result": {"value": value}}
        if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed":
            self.clicks.append((params["x"], params["y"]))
            if self.plays:
                self.title = "Canción  Uno"
            return {}
        return FakeChromium.cdp(self.chromium, ws_url, method, params)


def test_play_music_opens_the_playlist_the_code_built_and_clicks_play(monkeypatch):
    yt = FakeYTMusic(monkeypatch)

    result = navegador.play_music("playlist", "PLabcdefghij")

    assert yt.chromium.requested == "/json/new?" + quote("https://music.youtube.com/playlist?list=PLabcdefghij", safe="")
    assert yt.clicks == [(1, 2)]
    assert result == "Sonando en YouTube Music: Canción Uno"


def test_play_music_shuffle_clicks_the_shuffle_button_of_a_playlist(monkeypatch):
    yt = FakeYTMusic(monkeypatch)

    assert navegador.play_music("playlist", "PLabcdefghij", shuffle=True).endswith("(aleatorio)")
    assert yt.clicks == [(3, 4)]


def test_play_music_searches_songs_with_an_escaped_address_and_clicks_the_first_result(monkeypatch):
    yt = FakeYTMusic(monkeypatch)

    navegador.play_music("canciones", "lo-fi & jazz\nsuave", shuffle=True)

    assert yt.chromium.requested == "/json/new?" + quote("https://music.youtube.com/search?q=lo-fi+%26+jazz+suave", safe="")
    assert yt.clicks == [(5, 6)] and yt.shuffled == [True]


def test_play_music_waits_for_the_buttons_to_be_drawn(monkeypatch):
    yt = FakeYTMusic(monkeypatch, delay=3)

    navegador.play_music("playlist", "PLabcdefghij")

    assert yt.clicks == [(1, 2)]


def test_play_music_explains_when_youtube_changed(monkeypatch):
    yt = FakeYTMusic(monkeypatch, spots={"play": None, "shuffle": None, "result": None})

    with pytest.raises(navegador.NavegadorError, match="puede que YouTube Music haya cambiado"):
        navegador.play_music("playlist", "PLabcdefghij")

    assert yt.clicks == []


def test_play_music_says_so_when_nothing_started(monkeypatch):
    FakeYTMusic(monkeypatch, plays=False)

    with pytest.raises(navegador.NavegadorError, match="no veo que haya empezado"):
        navegador.play_music("playlist", "PLabcdefghij")


@pytest.mark.parametrize("kind, value", [("playlist", "x"), ("playlist", "PL/../x?a=1&b=2"), ("canciones", "  "), ("video", "abc"), ("playlist", "https://evil.com/xxxxxx")])
def test_play_music_never_builds_an_address_from_bad_input(monkeypatch, kind, value):
    yt = FakeYTMusic(monkeypatch)

    with pytest.raises(navegador.NavegadorError):
        navegador.play_music(kind, value)

    assert not hasattr(yt.chromium, "requested") and yt.clicks == []


def test_play_music_respects_the_browser_switch(monkeypatch):
    FakeYTMusic(monkeypatch)
    monkeypatch.setattr(navegador.config, "enabled", lambda key: key != "BROWSER_ENABLED")

    with pytest.raises(navegador.NavegadorError, match="apagado"):
        navegador.play_music("playlist", "PLabcdefghij")


# --- el «¿Salir del sitio?» de YouTube Music al navegar ------------------------------------------


class FakeSocket:
    """Un WebSocket de mentira: devuelve lo que se le arma y anota lo que se le manda."""

    def __init__(self, incoming):
        self.incoming, self.sent = list(incoming), []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if not self.incoming:
            raise TimeoutError
        return json.dumps(self.incoming.pop(0))


def test_navigate_accepts_the_leave_site_dialog(monkeypatch):
    socket = FakeSocket([
        {"method": "Page.javascriptDialogOpening", "params": {"type": "beforeunload"}},
        {"id": 1, "result": {"frameId": "x"}},
    ])
    monkeypatch.setattr(navegador, "connect", lambda *a, **k: socket)

    assert navegador._talk(navegador.WS_PREFIX + "page/X", "Page.navigate", {"url": "https://music.youtube.com/"}) == {"frameId": "x"}

    methods = [m["method"] for m in socket.sent]
    assert methods[0] == "Page.enable" and "Page.handleJavaScriptDialog" in methods
    assert next(m for m in socket.sent if m["method"] == "Page.handleJavaScriptDialog")["params"] == {"accept": True}


def test_navigate_accepts_a_dialog_that_shows_up_after_the_answer(monkeypatch):
    socket = FakeSocket([
        {"id": 1, "result": {}},
        {"method": "Page.javascriptDialogOpening", "params": {"type": "beforeunload"}},
    ])
    monkeypatch.setattr(navegador, "connect", lambda *a, **k: socket)

    navegador._talk(navegador.WS_PREFIX + "page/X", "Page.navigate", {"url": "https://music.youtube.com/"})

    assert any(m["method"] == "Page.handleJavaScriptDialog" for m in socket.sent)


def test_other_commands_do_not_touch_dialogs(monkeypatch):
    socket = FakeSocket([{"id": 1, "result": {"ok": 1}}])
    monkeypatch.setattr(navegador, "connect", lambda *a, **k: socket)

    assert navegador._talk(navegador.WS_PREFIX + "page/X", "Runtime.evaluate", {"expression": "1"}) == {"ok": 1}

    assert [m["method"] for m in socket.sent] == ["Runtime.evaluate"]


def test_an_alert_that_is_not_beforeunload_is_left_alone(monkeypatch):
    socket = FakeSocket([
        {"method": "Page.javascriptDialogOpening", "params": {"type": "alert"}},
        {"id": 1, "result": {}},
    ])
    monkeypatch.setattr(navegador, "connect", lambda *a, **k: socket)

    navegador._talk(navegador.WS_PREFIX + "page/X", "Page.navigate", {"url": "https://music.youtube.com/"})

    assert not any(m["method"] == "Page.handleJavaScriptDialog" for m in socket.sent)
