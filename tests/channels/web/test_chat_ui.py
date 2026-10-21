import re

import pytest

from epsilon import core
from epsilon.data import conversations
from epsilon.channels import web
from tests.channels.web.helpers import attachment_html, chat_html, js, make_conversations, page, right_panel, static_path, tool_rows


# --- Panel derecho (chat), atajos y nota de cautela --------------------------


def test_chat_has_a_collapsible_right_panel_with_an_accessible_toggle():
    html = chat_html()

    assert re.search(r'<aside[^>]*id="right"', html)
    tag = re.search(r'<button[^>]*data-toggle="right"[^>]*>', html).group(0)
    for needed in ('aria-expanded="', 'aria-controls="right"', 'aria-label="', 'type="button"'):
        assert needed in tag, needed
    assert 'class="shell has-right"' in html or "has-right" in re.search(r'<div class="shell[^"]*"', html).group(0)


def test_the_config_page_has_no_right_panel():
    html = page("config.html")

    assert 'id="right"' not in html and "has-right" not in html


def test_tool_rows_only_list_commands_the_core_knows(client):
    # Si se cambia o saca un comando del núcleo, el panel no puede seguir ofreciéndolo.
    rows = client.get("/api/tools").json()["tools"]

    assert rows, "el panel de herramientas no tiene filas"
    for row in rows:
        if row["insert"].startswith("/"):
            assert row["insert"].strip().lstrip("/") in core.COMMANDS, row["insert"]


def test_command_suggestions_only_offer_commands_the_core_knows(client):
    # Al escribir «/», el cuadro sugiere estos: ninguno puede ser un comando que el núcleo no entiende.
    commands = client.get("/api/tools").json()["commands"]

    assert {c["name"] for c in commands} >= {"ver", "buscar", "ayuda"}
    for command in commands:
        assert command["name"] in core.COMMANDS, command["name"]
        assert command["desc"]


def test_the_tools_panel_skips_what_the_menu_and_the_shortcuts_already_offer(client):
    # /ver, /archivos y /uso están en el menú de la izquierda; «Guardar nota», en los atajos.
    labels = {row["label"] for row in client.get("/api/tools").json()["tools"]}

    assert labels == {"Imagen a CSV", "/buscar", "/editar", "/ia", "/olvidar", "/recordar", "/resumir", "/ticket"}


def test_tool_rows_are_buttons_that_fill_the_message_box_without_sending():
    # Las filas las dibuja app.js: botones que solo escriben en el cuadro (insertText), sin enviar.
    render = re.search(r"function renderTools.*?\n}\n", js("app.js"), re.S).group(0)

    assert 'make("button", "tool")' in render and 'button.type = "button"' in render
    assert "insertText(tool.insert)" in render
    assert "sendMessage" not in render and "submit" not in render


def test_quick_actions_sit_above_the_message_box():
    html = chat_html()
    composer = re.search(r'<form id="composer".*?</form>', html, re.S).group(0)

    for label in ("Guardar nota", "Imagen a CSV", "Resumir"):
        assert label in composer, label
    assert composer.index("Guardar nota") < composer.index('id="input"')


def test_chat_warns_that_reading_images_can_be_wrong():
    assert "Epsilon puede equivocarse al leer imágenes" in chat_html()
    assert "Revisá los datos antes de guardar" in chat_html()


# --- Vista previa del archivo adjunto (cuadrada, con quitar y nombre) ---------


def test_attachment_has_a_square_thumbnail_with_remove_button_inside():
    html = attachment_html()
    thumb = re.search(r'<div class="thumb">(.*?)</div>', html, re.S).group(1)

    assert '<img id="thumb-img"' in thumb and 'alt=""' in thumb  # decorativa: el nombre va abajo
    assert 'id="thumb-pdf"' in thumb  # los PDF no tienen miniatura: llevan un ícono
    button = re.search(r'<button[^>]*id="chip-remove"[^>]*>', thumb).group(0)
    assert 'type="button"' in button and 'aria-label="Quitar el archivo"' in button


def test_attachment_name_sits_below_the_thumbnail():
    html = attachment_html()

    assert html.index('class="thumb"') < html.index('id="chip-name"')


def test_csp_allows_blob_images_for_the_previews(client):
    # La miniatura se arma con una URL local (blob:) del archivo elegido.
    policy = client.get("/").headers["content-security-policy"]

    assert re.search(r"img-src[^;]*\bblob:", policy)
    assert "script-src 'self'" in policy  # y sigue sin permitir scripts en línea


def test_attachment_remove_button_shows_on_hover_and_keyboard_focus():
    css = static_path("style.css").read_text(encoding="utf-8")

    assert ".thumb-remove" in css
    # Visible con el mouse encima, pero también con el foco del teclado y en pantallas táctiles.
    assert ":hover .thumb-remove" in css
    assert "focus-within" in css or ":focus-visible" in css
    assert "(hover: none)" in css


# --- Interfaz de conversaciones (Recientes, abrir, renombrar) -----------------


def test_the_conversation_id_pattern_in_the_browser_matches_the_servers():
    # El mismo patrón está escrito en app.js y en conversations.py: si uno cambia y el
    # otro no, el navegador rechazaría ids válidos (o aceptaría ids que el servidor rechaza).
    found = re.search(r"CONVERSATION_ID\s*=\s*/(.+)/;", js("app.js")).group(1)

    assert found == "^" + conversations.ID_PATTERN.pattern + "$"


def test_history_is_no_longer_kept_in_the_browser():
    # El servidor es la fuente de verdad. Lo único que queda de la clave vieja es borrarla.
    for name in ("app.js", "shell.js"):
        for line in js(name).splitlines():
            if "epsilon.history" in line and not line.strip().startswith("//"):
                assert "removeItem" in line, (name, line)


def test_the_chat_sends_the_conversation_with_every_request():
    source = js("app.js")

    assert source.count("conversation_id") >= 2  # en el JSON de los mensajes y en la subida
    assert "message_id" in source  # y al tocar un botón, en qué mensaje


def test_the_chat_opens_and_creates_conversations_through_the_api():
    source = js("app.js")

    assert "/api/conversations" in source
    assert 'method: "POST"' in source  # Nuevo chat crea una conversación en el servidor
    assert re.search(r'params\.get\("c"\)', source)  # /?c=<id> abre una conversación


def test_recents_come_from_the_server_and_link_to_each_conversation():
    source = js("shell.js")

    assert "/api/conversations" in source
    assert "/?c=" in source
    assert "aria-current" in source


def test_renaming_goes_through_the_title_endpoint_and_is_reachable_by_keyboard():
    source = js("shell.js")

    assert re.search(r'method:\s*"PUT"', source) and "/title" in source
    assert "Renombrar" in source  # el botón lleva nombre accesible
    assert '"Enter"' in source and '"Escape"' in source


def test_the_rename_button_shows_on_hover_focus_and_touch():
    css = static_path("style.css").read_text(encoding="utf-8")

    assert ".recent-edit" in css
    assert ":hover .recent-edit" in css and ":focus-within .recent-edit" in css
    assert re.search(r"\(hover: none\)\s*\{[^}]*\.recent-edit", css, re.S)


def test_the_recents_list_can_hold_many_conversations():
    # Con el tope viejo (240px) se cortaba la lista a las ~6 conversaciones.
    css = static_path("style.css").read_text(encoding="utf-8")
    block = re.search(r"\n\.recents \{(.*?)\}", css, re.S).group(1)
    max_height = int(re.search(r"max-height:\s*(\d+)px", block).group(1))

    assert max_height >= 2000


# --- Panel de herramientas: «Imagen a CSV» y la etiqueta «usa IA» ------------


def test_the_tools_panel_has_an_image_to_csv_row_that_works_like_the_pill(client):
    html = chat_html()
    row = next(r for r in client.get("/api/tools").json()["tools"] if r["label"] == "Imagen a CSV")

    assert row["action"] == "csv"
    # La píldora y la fila hacen lo mismo: las dos usan armCsv() de app.js.
    assert re.search(r'<button[^>]*id="pill-csv"[^>]*data-csv', html)
    assert "[data-csv]" in js("app.js") and 'tool.action === "csv"' in js("app.js")


def test_only_the_tools_that_spend_tokens_say_they_use_ai(client):
    # Si una herramienta pasa a usar (o dejar de usar) el modelo, hay que cambiarla en tools.py.
    assert {name for name, tagged in tool_rows(client) if tagged} == {"/ia", "/resumir", "Imagen a CSV"}


def test_every_tool_row_is_listed_once(client):
    names = [name for name, _ in tool_rows(client)]

    assert len(names) == len(set(names))


def test_the_ai_tag_explains_itself_and_does_not_name_the_model(client):
    render = re.search(r"function renderTools.*?\n}\n", js("app.js"), re.S).group(0)

    assert '"usa IA"' in render and "tag.title" in render  # el tooltip dice qué significa
    assert "Gemini" not in render  # el modelo puede cambiar: el texto habla de «IA»
    assert "Gemini" not in str(client.get("/api/tools").json())


def test_the_tag_has_a_style():
    css = static_path("style.css").read_text(encoding="utf-8")

    assert ".tool-tag" in css and ".tool-head" in css


# --- panel «Contexto» --------------------------------------------------------


def test_the_right_panel_has_a_context_section_after_the_tools():
    right = right_panel()

    heading = re.search(r'<h2[^>]*id="h-context"[^>]*>\s*Contexto\s*</h2>', right)
    assert heading
    assert right.index("h-tools") < heading.start()
    assert re.search(r'<ul[^>]*id="context"', right)
    # Vacío no es un hueco: explica qué va a aparecer ahí.
    assert re.search(r'<p[^>]*id="context-empty"[^>]*>[^<]+</p>', right)


def test_the_context_comes_from_the_server_and_is_drawn_as_text():
    app = js("app.js")

    # Al abrir una conversación y al recibir una respuesta. (Que no se use innerHTML ya lo vigila otro test.)
    assert app.count("data.artifacts") >= 2 and "insertAdjacentHTML" not in app


def test_clicking_a_context_item_writes_the_command_without_sending_it():
    app = js("app.js")
    start = app.index("function contextCommand")
    block = app[start : app.index("\n}\n", start)]

    # nota y resumen -> /ver; archivo -> /resumir; CSV -> /archivos
    assert "/ver " in block and "/resumir " in block and "/archivos " in block
    # Y el clic usa insertText (escribe), nunca ask/runCommand (envía).
    click = app[app.index("function renderContext") :]
    click = click[: click.index("\n}\n")]
    assert "insertText(" in click and "ask(" not in click and "runCommand(" not in click


def test_a_new_chat_and_opening_one_reset_the_context():
    app = js("app.js")
    def body_of(name):
        body = app[app.index(f"function {name}") :]
        return body[: body.index("\n}\n")]

    assert "setContext(" in body_of("clearScreen")
    # Abrir una conversación la dibuja con showConversation, que también carga el contexto.
    assert "showConversation(" in body_of("openConversation") and "setContext(" in body_of("showConversation")


def test_the_context_has_a_style():
    css = static_path("style.css").read_text(encoding="utf-8")

    assert ".context" in css and ".context-empty" in css


# --- «Ver más» en Recientes --------------------------------------------------


def test_the_conversation_list_reports_the_total_and_accepts_a_limit(client):
    make_conversations(client, 5)

    page = client.get("/api/conversations?limit=2").json()

    assert len(page["conversations"]) == 2 and page["total"] == 5
    assert client.get("/api/conversations").json()["total"] == 5  # sin limit: el tope de siempre (50)


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "201", "1.5", ""])
def test_a_bad_limit_is_rejected_without_listing_anything(client, bad):
    make_conversations(client, 1)

    assert client.get(f"/api/conversations?limit={bad}").status_code == 422


def test_recents_have_a_see_more_button_that_asks_for_a_bigger_page():
    source = js("shell.js")

    assert "Ver más" in source
    assert "limit=" in source and "total" in source
    assert re.search(r'make\("button",\s*"recents-more"', source)  # un botón de verdad: se alcanza con el teclado


def test_the_see_more_button_has_a_style():
    css = static_path("style.css").read_text(encoding="utf-8")

    assert ".recents-more" in css


def test_an_open_conversation_outside_the_first_page_still_gets_its_title():
    source = js("shell.js")

    # Si la abierta no está en la página pedida pero hay más en el servidor, se consulta
    # directamente (si no, el header diría «Conversación nueva» para una conversación vieja).
    assert "encodeURIComponent(currentId)" in source
    assert re.search(r"total\s*>\s*conversations\.length", source)


# --- La conversación de Telegram en la web: ícono, color, solo lectura -------


def test_telegram_conversations_are_marked_with_an_icon_a_color_and_a_word():
    shell, css = js("shell.js"), static_path("style.css").read_text(encoding="utf-8")

    assert 'conversation.channel === "telegram"' in shell and "channel-mark" in shell
    assert "Telegram: " in shell  # el nombre del canal también para quien no ve color ni ícono
    for needed in ("--telegram:", ".recent-row.from-telegram", ".channel-mark", ".crumb .from-telegram"):
        assert needed in css, needed


def test_the_telegram_color_is_not_the_only_signal_and_differs_from_the_accent():
    css = static_path("style.css").read_text(encoding="utf-8")
    accent = re.search(r"--accent:\s*(#[0-9a-f]+)", css).group(1)
    telegram_color = re.search(r"--telegram:\s*(#[0-9a-f]+)", css).group(1)

    assert telegram_color != accent


def test_the_chat_page_has_a_hidden_read_only_note_before_the_composer():
    html = chat_html()
    note = re.search(r'<p id="readonly-note"[^>]*>', html).group(0)

    assert "hidden" in note
    assert html.index('id="readonly-note"') < html.index('id="composer"')
    assert "Telegram" in html[html.index('id="readonly-note"') :][:600]


def test_a_telegram_conversation_turns_the_composer_off_and_old_buttons_do_nothing():
    app = js("app.js")

    assert 'setReadOnly(data.channel === "telegram")' in app
    assert "composer.inert = on" in app
    for guarded in ("function insertText", "function armCsv", "function setFile"):
        body = app[app.index(guarded) :]
        assert "if (readOnly) return" in body[:200], guarded
    assert "readOnly" in app[app.index("function pressAction") :][:200]


def test_the_open_telegram_conversation_refreshes_itself_and_follows_nuevo():
    app = js("app.js")
    block = app[app.index("async function refreshTelegram") :]
    block = block[: block.index("\n}\n")]

    assert "!readOnly || document.hidden" in block  # solo si la mirás
    assert "setInterval(refreshTelegram" in app
    assert 'c.channel === "telegram"' in block and "openConversation(latest.id)" in block
    assert "innerHTML" not in block


# --- El panel derecho ya no muestra canales (tienen su página) ---------------------


def test_the_right_panel_has_only_tools_and_context():
    right = right_panel()
    headings = re.findall(r"<h2[^>]*>\s*(.*?)\s*</h2>", right, re.S)

    assert headings == ["Panel", "Herramientas", "Contexto"] or headings == ["Herramientas", "Contexto"]
    for gone in ("side-channels", "Administrar canales", "Canales"):
        assert gone not in right, gone


def test_the_shell_script_no_longer_draws_channels_or_voice_in_the_panel():
    shell, css = page("shell.js"), page("style.css")

    for gone in ("renderSideChannels", "renderSideVoice", "VOICE_ROWS", "side-channels"):
        assert gone not in shell, gone
    assert ".side-right .channels" not in css and ".side-link" not in css
    assert "GEMINI_MODEL" in shell  # lo que sí sigue leyendo: el modelo del header


def test_chat_waits_for_its_conversation_before_showing_the_greeting():
    # Sin esto, al volver al chat se ve un instante «chat nuevo» y después salta a la última conversación.
    assert re.search(r"<body[^>]*class=\"booting\"", chat_html())
    assert 'classList.remove("booting")' in js("app.js")
    css = js("style.css")
    assert re.search(r"body\.booting \.log\s*\{\s*opacity:\s*0;", css)


# --- Modo IA: selector, barra de costo y límite ---------------------------------------


def test_new_chat_mode_selector_exists():
    html = chat_html()

    for mode in ("normal", "ai"):
        tag = re.search(rf'<button[^>]*id="mode-{mode}"[^>]*>', html).group(0)
        assert 'type="button"' in tag and "aria-pressed=" in tag, mode
    assert "Chat con IA" in html and "IA" in html and "Gemini" not in html


def test_ai_bar_exists_and_is_hidden_by_default():
    html = chat_html()

    assert re.search(r'<div[^>]*id="ai-bar"[^>]*\bhidden\b', html)
    for needed in ("ai-context", "ai-cost", "ai-limit", "ai-limit-save", "ai-blocked"):
        assert f'id="{needed}"' in html, needed


def test_app_js_sends_the_mode_only_with_the_first_message():
    code = js("app.js")

    assert "chatMode" in code and "started" in code
    assert "if (!started)" in code  # el modo viaja solo mientras la conversación no empezó
    assert 'form.append("mode"' in code


def test_app_js_fills_the_ai_bar_with_textcontent_and_edits_the_limit():
    code = js("app.js")

    # La barra se arma con textContent (que no use innerHTML en general ya lo vigila otro test).
    assert "aiContext.textContent" in code and "aiCost.textContent" in code and "aiBlocked.textContent" in code
    assert 'getElementById("ai-context")' in code
    assert "/limit" in code and '"PUT"' in code


def test_conversation_list_marks_ai_mode():
    code = js("shell.js")

    assert 'conversation.mode === "ai"' in code
    assert 'make("span", "ai-mark", "IA")' in code  # el texto va como textContent (make)


def test_sending_waits_for_the_new_chat_id_so_the_mode_is_not_lost():
    code = js("app.js")

    # startNewChat guarda su promesa y submit la espera antes de armar el pedido.
    assert "newChatReady = " in code
    start = code[code.index("async function startNewChat"):]
    assert "newChatReady" in start.split("\n}\n")[0]
    submit = code[code.index("async function submit()"):]
    body = submit.split("\n}\n")[0]
    assert "await newChatReady" in body
    assert body.index("await newChatReady") < body.index("ask(")


def _function_body(code, signature):
    """El texto de una función de nivel superior de app.js (hasta el `}` en la columna 0)."""
    return code[code.index(signature):].split("\n}\n")[0]


def test_the_mode_picker_does_not_flash_while_an_existing_conversation_loads():
    code = js("app.js")

    # Limpiar la pantalla esconde el selector; no lo muestra (antes parpadeaba al abrir una conversación).
    clear = _function_body(code, "function clearScreen()")
    assert "showMode(" not in clear and "modePicker.hidden = true" in clear
    # Solo un chat nuevo, o una conversación que el servidor no tiene todavía (404), lo muestran.
    assert 'showMode("normal", null)' in _function_body(code, "async function openNewChat()")
    opening = _function_body(code, "async function openConversation(id)")
    assert "response.status === 404" in opening and 'showMode("normal", null)' in opening


def test_a_failed_first_request_resyncs_the_mode_with_the_server():
    code = js("app.js")

    sync = _function_body(code, "async function syncMode()")
    assert "/api/conversations/${conversationId}" in sync and "showMode(" in sync and "started =" in sync
    # Si el primer pedido falla, el servidor pudo haber creado la conversación igual: se vuelve a mirar.
    ask = _function_body(code, "async function ask(request)")
    catch = ask[ask.index("} catch (error) {"):]
    assert "syncMode()" in catch
