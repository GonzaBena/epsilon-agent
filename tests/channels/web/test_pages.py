import shutil
import subprocess
import re
from pathlib import Path

import pytest

from epsilon import state, tools
from epsilon import state
from epsilon.channels import web
from tests.channels.web.helpers import BRAND, LOWERCASE, PAGES, chat_html, js, menu_items, page, static_path, top_links


# --- Página estática ---------------------------------------------------------


def test_home_serves_the_page_with_its_css_and_js(client):
    page = client.get("/")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "/static/css/style.css" in page.text and "/static/js/app.js" in page.text


def test_static_files_are_served_with_their_types(client):
    css = client.get("/static/css/style.css")
    js = client.get("/static/js/app.js")

    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_static_files_cannot_escape_their_folder(client):
    assert client.get("/static/../web.py").status_code == 404
    assert client.get("/static/%2e%2e/web.py").status_code == 404


def test_pages_are_not_cached(client):
    # Así, después de actualizar Epsilon el navegador no usa un app.js viejo.
    assert client.get("/static/js/app.js").headers["cache-control"] == "no-cache"


def test_responses_carry_a_strict_content_security_policy(client):
    for path in ("/", "/api/health"):
        policy = client.get(path).headers["content-security-policy"]
        assert "script-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy


def test_even_rejected_requests_carry_the_policy(client):
    reply = client.get("/api/health", headers={"Host": "evil.com"})

    assert reply.status_code == 403
    assert "content-security-policy" in reply.headers


@pytest.mark.parametrize("name", ["app.js", "config.js", "shell.js", "markdown.js", "permisos.js", "rutinas.js"])
def test_js_never_builds_html_from_text(name):
    import re

    source = static_path(name).read_text(encoding="utf-8")
    source = re.sub(r"//.*", "", source)  # los comentarios pueden nombrar la regla

    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in source


@pytest.mark.parametrize("name", ["index.html", "config.html", "permisos.html", "rutinas.html"])
def test_html_has_no_inline_script_or_style(name):
    import re

    html = static_path(name).read_text(encoding="utf-8")

    assert not re.search(r"<script(?![^>]*\bsrc=)", html)  # solo <script src=...>
    assert "style=" not in html and "<style" not in html
    assert not re.search(r"\son\w+=", html)  # nada de onclick= y similares


# --- Esqueleto compartido: header, menú lateral y paneles que se contraen ----


def test_both_pages_load_the_shared_shell_script():
    for name in PAGES:
        assert "/static/js/shell.js" in page(name), name


def test_both_pages_have_the_same_menu():
    # El menú está escrito en cada HTML (sin plantillas): este test evita que se desfasen.
    for name in PAGES:
        assert menu_items(page("index.html")) == menu_items(page(name)), name


def test_menu_has_the_expected_sections_and_no_dead_links():
    labels = [label for _, _, label in menu_items(page("index.html"))]

    # Canales y Uso están en la barra de arriba; Configuración, abajo de todo en este menú.
    assert labels == ["Chat", "Biblioteca", "Permisos", "Conectores", "Rutinas", "Configuración"]
    # Nada que no funcione todavía (Memoria, Docs, Sesión, Resúmenes...).
    for gone in ("Memoria", "Docs", "Sesión", "Resúmenes"):
        assert gone not in page("index.html") and gone not in page("config.html")


def test_every_menu_item_has_a_text_label_even_when_only_the_icon_shows():
    for name in PAGES:
        for href, command, label in menu_items(page(name)):
            assert label, (name, href)


def test_menu_commands_are_ones_the_chat_page_may_run_from_a_link():
    # La página ejecuta por sí sola un comando que llega en la URL (?cmd=...) solo
    # si el servidor lo declara (tools.py), y esos son solo lecturas (nada de escribir
    # ni de llamar al modelo). La lista ya no está escrita en app.js.
    allowed = tools.link_commands()

    assert sorted(allowed) == ["/archivos", "/archivos tickets", "/clima", "/dolar", "/fecha", "/tareas", "/uso", "/ver"]
    for tool in tools.TOOLS:
        if tool.links:
            assert not tool.writes and not tool.uses_ai, tool.label
    assert "ALLOWED_COMMANDS" not in page("app.js")
    for href, command, label in menu_items(page("index.html")):
        if command:
            assert command in allowed, command


def test_menu_links_and_commands_agree():
    from urllib.parse import parse_qs, urlparse

    for href, command, label in menu_items(page("index.html")):
        if command:
            assert parse_qs(urlparse(href).query)["cmd"] == [command], href


def test_panel_toggles_are_accessible_buttons():
    for name in PAGES:
        html = page(name)
        tag = re.search(r'<button[^>]*data-toggle="left"[^>]*>', html).group(0)
        for needed in ('aria-expanded="', 'aria-controls="left"', 'aria-label="', 'type="button"'):
            assert needed in tag, (name, needed)


def test_no_search_box_in_the_sidebar_for_now():
    for name in PAGES:
        sidebar = re.search(r'<aside[^>]*id="left".*?</aside>', page(name), re.S).group(0)
        assert "<input" not in sidebar, name


def test_new_chat_replaces_clear_chat():
    html = page("index.html")

    assert 'id="new-chat"' in html and "Nuevo chat" in html
    assert "Limpiar chat" not in html


# --- Pulido: botón de enviar con ícono y estado vacío como saludo -------------


def test_send_button_is_an_icon_button_that_still_has_a_name():
    tag = re.search(r'<button[^>]*id="send"[^>]*>(.*?)</button>', chat_html(), re.S)
    attrs = re.search(r'<button[^>]*id="send"[^>]*>', chat_html()).group(0)

    assert 'type="submit"' in attrs and 'aria-label="Enviar"' in attrs
    assert "send-btn" in attrs
    assert "<svg" in tag.group(1) and 'aria-hidden="true"' in tag.group(1)


def test_config_save_button_keeps_its_text_label():
    # Comparte la clase base con el botón de enviar del chat, pero es de texto.
    html = page("config.html")

    assert re.search(r'<button[^>]*id="save"[^>]*class="send"[^>]*>\s*Guardar cambios', html)


def test_empty_state_is_a_welcome_with_an_avatar_a_greeting_and_a_place_for_the_ideas():
    section = re.search(r'<section id="empty".*?</section>', chat_html(), re.S).group(0)

    assert 'class="welcome-avatar"' in section
    assert 'id="welcome-greeting"' in section and 'id="welcome-cards"' in section
    assert "starter" not in section
    assert "audio" not in section.lower()  # todavía no se puede mandar audio


def test_empty_state_is_not_a_message_node():
    # «Nuevo chat» borra todos los .msg: si el saludo lo fuera, se perdería.
    tag = re.search(r'<section id="empty"[^>]*>', chat_html()).group(0)

    assert "msg" not in tag


# --- La marca de Epsilon es la Ε mayúscula (U+0395), en todos lados ----------


@pytest.mark.parametrize("name", ["index.html", "config.html", "app.js", "shell.js", "config.js", "style.css"])
def test_no_lowercase_epsilon_anywhere_in_the_web_files(name):
    # La Ε y la E latina se parecen tanto que es fácil colar la letra equivocada.
    text = static_path(name).read_text(encoding="utf-8")

    assert LOWERCASE not in text, name


def test_every_letter_mark_in_the_html_is_the_capital_epsilon():
    # El header y el saludo del chat usan una imagen; si alguna vez vuelve una letra suelta, tiene que ser
    # la Ε mayúscula. Las demás páginas no llevan ninguna.
    marks = re.findall(r'class="mark"[^>]*>(.)<', page("index.html"))
    assert all(m == BRAND for m in marks), [hex(ord(m)) for m in marks]
    for name in ("config.html", "canales.html", "uso.html", "permisos.html", "rutinas.html"):
        assert 'class="mark"' not in page(name), name


def test_the_marks_drawn_by_the_scripts_use_the_brand_logo():
    # Las respuestas de Epsilon y el «Pensando…» usan el logo de la marca (imagen), no el cuadrado con la letra.
    js = static_path("app.js").read_text(encoding="utf-8")
    drawn = re.findall(r'make\("span", "mark", "(.)"\)', js)

    assert len(drawn) == 0
    assert 'src = "/static/img/apple-touch-icon.png"' in js


# --- Barra de arriba y página Canales ----------------------------------------


def test_every_page_has_the_same_top_bar_and_marks_where_you_are():
    expected = [("/", "Chat"), ("/canales", "Canales"), ("/uso", "Uso"), ("/permisos", "Permisos")]
    current = {"index.html": "Chat", "canales.html": "Canales", "uso.html": "Uso", "config.html": None, "biblioteca.html": None, "permisos.html": "Permisos", "rutinas.html": None, "conectores.html": None}

    for name in PAGES:
        links = top_links(page(name))
        assert [(h, t) for h, t, _ in links] == expected, name
        assert [t for _, t, here in links if here] == ([current[name]] if current[name] else []), name


def test_settings_is_the_last_item_of_the_left_menu_and_is_current_only_on_its_page():
    for name in PAGES:
        html = page(name)
        items = re.findall(r'<a class="nav-item"([^>]*)>(.*?)</a>', re.search(r'aria-label="Secciones"[^>]*>(.*?)</nav>', html, re.S).group(1), re.S)
        attrs, inner = items[-1]
        assert 'href="/config"' in attrs and "Configuración" in inner, name
        assert ("aria-current" in attrs) == (name == "config.html"), name
        assert "icon-link" not in html  # ya no hay botón de configuración arriba


def test_the_top_bar_only_has_chat_channels_usage_and_permissions():
    for name in PAGES:
        assert [t for _, t, _ in top_links(page(name))] == ["Chat", "Canales", "Uso", "Permisos"]


def test_the_channels_page_is_served(client):
    reply = client.get("/canales")

    assert reply.status_code == 200 and "canales.js" in reply.text


def test_the_channels_page_loads_its_own_script_and_the_shell():
    html = page("canales.html")

    assert '/static/js/canales.js' in html and '/static/js/shell.js' in html and '/static/js/config.js' not in html
    assert 'id="channel-cards"' in html and 'role="status"' in html


def test_the_channels_script_draws_cards_with_text_content_and_a_real_switch():
    script = page("canales.js")

    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))
    assert "innerHTML" not in code
    assert '"/api/channels"' in script and "PUT" in script
    assert 'setAttribute("role", "switch")' in script and "aria-checked" in script
    assert "channel.switchable" in script  # la web no lleva interruptor
    for state in ("running", "starting", "skipped", "stopped", "off"):
        assert state in script, state


def test_the_settings_page_no_longer_shows_channel_status():
    html, script = page("config.html"), page("config.js")

    assert 'id="channels"' not in html and "renderChannels" not in script
    assert 'id="h-channels"' not in html


# --- Marca: logo y favicon -------------------------------------------------------


def test_every_page_declares_the_favicons_and_they_exist():
    for name in PAGES:
        html = page(name)
        assert 'rel="icon" href="/static/img/favicon.ico"' in html, name
        assert 'sizes="32x32" href="/static/img/favicon-32.png"' in html, name
        assert 'rel="apple-touch-icon" href="/static/img/apple-touch-icon.png"' in html, name
        for path in re.findall(r'(?:href|src)="/static/(img/[^"]+)"', html):
            assert (web.STATIC_DIR / path).is_file(), (name, path)


def test_the_browser_default_favicon_path_works(client):
    reply = client.get("/favicon.ico")

    assert reply.status_code == 200 and reply.headers["content-type"] == "image/x-icon"
    assert reply.content == (web.STATIC_DIR / "img" / "favicon.ico").read_bytes()
    assert client.get("/static/img/favicon-32.png").status_code == 200


def test_every_page_uses_the_logo_in_the_header_with_empty_alt():
    for name in PAGES:
        tag = re.search(r'<img class="brand-logo"[^>]*>', page(name)).group(0)
        assert 'alt=""' in tag  # decorativo: el nombre «Epsilon» ya está al lado
        assert 'width="30"' in tag and 'height="30"' in tag  # no mueve la página al cargar


def test_the_readme_images_exist():
    readme = (Path(web.__file__).parents[3] / "README.md").read_text(encoding="utf-8")
    root = Path(web.__file__).parents[3]

    paths = re.findall(r'<img[^>]*src="([^"]+)"', readme) + re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme)
    assert paths, "el README no muestra el logo"
    for path in paths:
        assert (root / path).is_file(), path


# --- Los colores suaves salen del acento --------------------------------------


def test_soft_backgrounds_are_derived_from_the_accent_not_hard_coded():
    css = page("style.css")
    root = re.search(r":root \{(.*?)\n\}", css, re.S).group(1)

    for token in ("--bubble", "--accent-soft", "--accent-card", "--accent-line"):
        line = re.search(rf"{token}:\s*([^;]+);", root).group(1)
        assert "color-mix" in line and "var(--accent)" in line, token
    assert "color-mix" in re.search(r"--telegram-bg:\s*([^;]+);", root).group(1)
    # Los verdes oscuros de antes (derivados a mano del lima viejo) ya no están escritos a mano.
    for old in ("#1d2019", "#1a1f14", "#3a4429", "#1e2318"):
        assert old not in css, old


def test_the_derived_tokens_are_the_ones_the_styles_use():
    css = page("style.css")

    for token in ("var(--accent-soft)", "var(--accent-card)", "var(--accent-line)", "var(--bubble)"):
        assert token in css, token


# --- Fondo azulado en la conversación de Telegram ------------------------------


def test_the_telegram_view_tints_the_stage_and_the_composer_blue():
    css, app = page("style.css"), page("app.js")
    view = re.search(r"body\.view-telegram \{(.*?)\}", css, re.S).group(1)

    assert "--stage-bg: color-mix" in view and "var(--telegram)" in view  # sale del azul de Telegram
    assert "--bubble:" in view  # tu burbuja también
    assert "background: var(--stage-bg)" in re.search(r"\n\.stage \{(.*?)\}", css, re.S).group(0)
    assert "background: var(--stage-bg)" in re.search(r"\n\.composer \{(.*?)\}", css, re.S).group(0)
    assert re.search(r"--stage-bg: var\(\s*--bg\s*\)", css)  # fuera de Telegram, el fondo de siempre (un formateador puede partir la línea)
    assert 'document.body.classList.toggle("view-telegram", on)' in app


@pytest.mark.skipif(shutil.which("node") is None, reason="sin node no se puede revisar la sintaxis")
@pytest.mark.parametrize("name", ["app.js", "biblioteca.js", "canales.js", "config.js", "markdown.js", "permisos.js", "rutinas.js", "shell.js", "uso.js"])
def test_every_script_parses(name):
    # Un error de sintaxis deja la página muerta y ninguna otra prueba lo ve (no ejecutan el JS).
    done = subprocess.run(["node", "--check", str(static_path(name))], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr


def test_deleting_a_conversation_asks_first_without_browser_dialogs():
    code = re.sub(r"//.*", "", js("shell.js"))

    # La confirmación va en la propia lista, no en una ventana del navegador.
    for dialog in ("confirm(", "alert(", "prompt("):
        assert dialog not in code
    assert 'method: "DELETE"' in code and "deleteConfirm" in code


def test_recents_row_carries_the_color_and_the_buttons_float():
    css = js("style.css")

    # El fondo de la actual y del hover es de toda la fila (no del enlace, que se achicaba)...
    assert re.search(r"\.recent-row:not\(\.from-telegram\):has\(\.recent\[aria-current\]\)", css)
    assert re.search(r"\.recent-row \.recent\[aria-current\]\s*\{\s*background:\s*transparent;", css)
    # ...y los botones flotan encima, sin quitarle ancho al título.
    assert re.search(r"\.recent-row \.recent-edit\s*\{\s*position:\s*absolute;", css)


# --- Markdown en las respuestas del bot ----------------------------------------


def test_the_chat_loads_markdown_it_before_app_js_and_renders_only_bot_replies():
    html, app = page("index.html"), page("app.js")

    assert html.index("/static/js/vendor/markdown-it.min.js") < html.index("/static/js/markdown.js") < html.index("/static/js/app.js")
    assert 'message.role === "bot" && !message.error' in app and "window.renderMarkdown(message.text)" in app


@pytest.mark.skipif(shutil.which("node") is None, reason="sin node no se puede correr markdown-it")
def test_markdown_it_is_configured_so_model_output_cannot_inject_html():
    # Mismas opciones que js/markdown.js; el ataque tiene que salir como texto o como enlace descartado.
    script = (
        "const md = require(process.argv[1])({html:false, linkify:false, breaks:true}).disable('image');"
        "process.stdout.write(md.render(process.argv[2]));"
    )
    attack = '<script>alert(1)</script> <img src=x onerror=alert(1)> [clic](javascript:alert(1)) ![x](http://evil/p.png)'
    done = subprocess.run(
        ["node", "-e", script, str(static_path("vendor/markdown-it.min.js")), attack], capture_output=True, text=True
    )

    assert done.returncode == 0, done.stderr
    assert "<script" not in done.stdout and "<img" not in done.stdout and 'href="javascript' not in done.stdout


def test_the_chat_loads_katex_and_the_math_rule_before_markdown_js():
    html = page("index.html")

    order = ["vendor/katex.min.js", "math.js", "markdown.js", "app.js"]
    assert [html.index(f"/static/js/{name}") for name in order] == sorted(html.index(f"/static/js/{name}") for name in order)


@pytest.mark.skipif(shutil.which("node") is None, reason="sin node no se puede correr KaTeX")
def test_formulas_are_drawn_as_mathml_and_cannot_inject_html_or_links():
    # MathML y no HTML con estilos: la política de seguridad no deja estilos en línea.
    script = (
        "global.window = {};"
        "const md = require(process.argv[1])({html:false, linkify:false, breaks:true}).disable('image');"
        "require(process.argv[2])(md, require(process.argv[3]));"
        "process.stdout.write(md.render(process.argv[4]));"
    )
    text = r"$$\frac{a}{b}$$ y $v$, cuesta $5 y $10. $\href{javascript:alert(1)}{x}$ $<img src=x onerror=alert(1)>$"
    done = subprocess.run(
        ["node", "-e", script, str(static_path("vendor/markdown-it.min.js")), str(static_path("math.js")),
         str(static_path("vendor/katex.min.js")), text],
        capture_output=True, text=True,
    )

    assert done.returncode == 0, done.stderr
    assert "<math" in done.stdout and "<mfrac>" in done.stdout and "cuesta $5 y $10" in done.stdout
    assert "style=" not in done.stdout and "<img" not in done.stdout and "<a " not in done.stdout


def test_markdown_js_matches_the_options_the_test_above_checks():
    source = page("markdown.js")

    assert "html: false" in source and "linkify: false" in source and '.disable("image")' in source


def test_the_channels_script_names_plugins_from_the_api_and_asks_before_turning_one_on():
    script = page("canales.js")

    assert "channel.label" in script and "channel.about" in script  # los plugins se nombran solos
    assert "channel.warning" in script and "confirm(" in script and "confirmed" in script
    assert "invalid:" in script  # un plugin con manifiesto roto se muestra con su motivo
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))
    assert "innerHTML" not in code


def test_the_config_page_draws_plugin_fields_itself_with_text_content():
    html, script = page("config.html"), page("config.js")

    assert 'id="plugin-settings"' in html
    assert "setting.group" in script and "plugin-settings" in script
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))
    assert "innerHTML" not in code


# --- Formulario de primera vez ------------------------------------------------


def onboarding_html():
    return re.search(r'<dialog id="onboarding".*?</dialog>', chat_html(), re.S).group(0)


def test_onboarding_form_has_two_labelled_fields_and_a_skip():
    section = onboarding_html()

    assert "¿Cómo te llamo?" in section and "¿Cómo querés llamarme?" in section
    assert re.search(r'<button[^>]*id="onboarding-save"[^>]*>\s*Continuar', section)
    assert re.search(r'<button[^>]*id="onboarding-skip"[^>]*>\s*Saltar por ahora', section)
    assert 'id="onboarding-user"' in section and 'id="onboarding-assistant"' in section
    assert 'value="Epsilon"' in section
    assert 'style="' not in section  # la CSP no deja estilos en línea


def app_region(start, end="// --- Arranque"):
    js = static_path("app.js").read_text(encoding="utf-8")
    return js[js.index(start):js.index(end, js.index(start))]


def test_onboarding_has_one_error_line_per_field_wired_to_its_input():
    section = onboarding_html()

    for field in ("user", "assistant"):
        assert re.search(rf'<p[^>]*class="error"[^>]*id="onboarding-{field}-error"[^>]*role="alert"', section)
        assert re.search(rf'<input[^>]*id="onboarding-{field}"[^>]*aria-describedby="onboarding-{field}-error"', section)
    assert 'id="onboarding-error"' not in section  # la línea compartida ya no existe


def test_onboarding_saves_with_put_config_and_shows_each_error_under_its_field():
    region = app_region("// --- Primera vez")
    save = region[region.index('"onboarding-form"'):]

    assert 'fetch("/api/config"' in save and 'method: "PUT"' in save
    assert "USER_NAME: user" in save and "ASSISTANT_NAME" not in region
    assert 'clear.push("AGENT_NAME")' in save  # nombre del asistente vacío = el de siempre
    assert re.search(r"onboardingUserError\.textContent = errors\.USER_NAME", save)
    assert re.search(r"onboardingAssistantError\.textContent = errors\.AGENT_NAME", save)
    assert "response.status === 422" in save


def test_onboarding_skip_is_remembered_in_the_browser_without_breaking_when_storage_fails():
    region = app_region("// --- Primera vez")
    skip = region[region.index("function skipOnboarding"):region.index('"onboarding-skip"')]

    assert "localStorage.setItem(ONBOARDING_SKIPPED" in skip and "try {" in skip and "catch" in skip
    assert "localStorage.getItem(ONBOARDING_SKIPPED" in region and "catch" in region


def test_welcome_and_onboarding_code_never_build_html_from_server_text():
    region = app_region("// --- Bienvenida")  # incluye el formulario de primera vez

    assert "innerHTML" not in region and "insertAdjacentHTML" not in region and "outerHTML" not in region
    assert "textContent = card.title" in region and "textContent = data.greeting" in region


def test_no_page_uses_the_authors_name_as_a_placeholder():
    for name in PAGES:
        assert 'placeholder="Gonzalo"' not in page(name), name


# --- Disposición de la bienvenida y formulario a pantalla completa -------------


def css_rule(selector):
    css = static_path("style.css").read_text(encoding="utf-8")
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{(.*?)\}}", css, re.S)
    assert match, selector
    return match.group(1)


def test_welcome_has_a_hero_with_the_greeting_and_the_big_picture_above_the_cards():
    section = re.search(r'<section id="empty".*?</section>', chat_html(), re.S).group(0)
    hero = re.search(r'<div class="welcome-hero">(.*?)</div>', section, re.S).group(1)

    assert 'id="welcome-greeting"' in hero and 'class="welcome-avatar"' in hero
    assert section.index("welcome-hero") < section.index('id="welcome-cards"')  # las ideas van debajo


def test_welcome_hero_is_two_columns_and_stacks_on_narrow_screens():
    assert "grid-template-columns" in css_rule(".welcome-hero")
    css = static_path("style.css").read_text(encoding="utf-8")
    narrow = css[css.index("@media (max-width: 36rem)"):]
    assert ".welcome-hero" in narrow[: narrow.index("\n}\n\n") + 3] or ".welcome-hero" in narrow[:600]


def test_onboarding_covers_the_whole_screen_with_a_background_and_a_centered_panel():
    section = onboarding_html()
    rule = css_rule(".onboarding[open]")

    assert 'class="onboarding-panel"' in section and 'id="onboarding-save"' in section.split("onboarding-panel")[1]
    assert "100vw" in css_rule(".onboarding") and ("100vh" in css_rule(".onboarding") or "100dvh" in css_rule(".onboarding"))
    assert "display: flex" in rule and "align-items: center" in rule and "justify-content: center" in rule
    assert "background" in css_rule(".onboarding")  # color de fondo propio, no el del cuadro flotante
    assert "max-width" in css_rule(".onboarding-panel")


def test_welcome_greeting_keeps_its_line_breaks_and_the_picture_is_big():
    assert "white-space: pre-line" in css_rule(".welcome-greeting")
    columns = css_rule(".welcome-hero")
    assert re.search(r"minmax\(0, (1[8-9]|2\d)rem\)", columns)  # columna de la imagen: 18 rem o más
    assert re.search(r"max-height: (2[6-9]|3\d)rem", css_rule(".welcome-avatar"))


def test_onboarding_is_a_real_form_so_enter_submits_it():
    section = onboarding_html()

    assert re.search(r'<form[^>]*id="onboarding-form"', section)
    assert re.search(r'<button[^>]*type="submit"[^>]*id="onboarding-save"', section)
    assert re.search(r'<button[^>]*type="button"[^>]*id="onboarding-skip"', section)
    assert 'addEventListener("submit"' in app_region("// --- Primera vez")


def test_escape_counts_as_skip_in_the_first_run_form():
    region = app_region("// --- Primera vez")

    assert "function skipOnboarding" in region
    assert 'addEventListener("cancel", skipOnboarding)' in region  # Esc: el evento nativo del <dialog>
    assert 'addEventListener("click", skipOnboarding)' in region
