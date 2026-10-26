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
    # Solo quedan las que arman un flujo (imagen, archivo, nota); el resto se escribe con «/».
    labels = {row["label"] for row in client.get("/api/tools").json()["tools"]}

    assert labels == {"Imagen a CSV", "/resumir", "/ticket", "/csv"}


def test_tool_rows_are_buttons_that_fill_the_message_box_without_sending():
    # Las filas las dibuja app.js en el menú del «+»: botones que solo escriben en el cuadro (insertText), sin enviar.
    render = re.search(r"function renderTools.*?\n}\n", js("app.js"), re.S).group(0)

    assert 'make("button", "popover-action-item")' in render and 'button.type = "button"' in render
    assert "insertText(tool.insert)" in render
    assert "sendMessage" not in render and "submit" not in render


def test_modern_composer_toolbar_has_llm_controls():
    html = chat_html()
    composer = re.search(r'<form id="composer".*?</form>', html, re.S).group(0)

    # La barra vieja de atajos (.quick) ya no está arriba del input
    assert 'class="quick"' not in composer
    # Barra inferior moderna con controles estilo LLM
    assert 'class="composer-toolbar"' in composer
    assert 'id="attach"' in composer
    assert 'id="attach-popover"' in composer
    assert 'id="mic"' in composer
    assert 'id="composer-model"' in composer
    assert 'id="composer-effort"' in composer
    assert 'id="effort-popover"' in composer


def test_chat_warns_that_reading_images_can_be_wrong():
    assert "Puede equivocarse: revisá los datos antes de guardar." in chat_html()


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
    assert "Opciones de" in source and '"Editar"' in source  # el menú de tres puntitos lleva nombre accesible
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


def test_the_tools_panel_has_an_image_to_csv_row(client):
    row = next(r for r in client.get("/api/tools").json()["tools"] if r["label"] == "Imagen a CSV")

    assert row["action"] == "csv"
    assert 'tool.action === "csv"' in js("app.js")


def test_only_the_tools_that_spend_tokens_say_they_use_ai(client):
    # Si una herramienta pasa a usar (o dejar de usar) el modelo, hay que cambiarla en tools.py.
    assert {name for name, tagged in tool_rows(client) if tagged} == {"/resumir", "Imagen a CSV"}


def test_every_tool_row_is_listed_once(client):
    names = [name for name, _ in tool_rows(client)]

    assert len(names) == len(set(names))


def test_the_ai_tag_explains_itself_and_does_not_name_the_model(client):
    render = re.search(r"function renderTools.*?\n}\n", js("app.js"), re.S).group(0)

    assert '"IA"' in render and "tag.title" in render  # el tooltip dice qué significa
    assert "Gemini" not in render  # el modelo puede cambiar: el texto habla de «IA»
    assert "Gemini" not in str(client.get("/api/tools").json())


def test_the_tag_has_a_style():
    css = static_path("style.css").read_text(encoding="utf-8")

    assert ".tool-tag" in css


# --- panel «Contexto» --------------------------------------------------------


def test_the_right_panel_has_a_context_section():
    right = right_panel()

    heading = re.search(r'<h2[^>]*id="h-context"[^>]*>\s*Contexto\s*</h2>', right)
    assert heading
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


def test_the_right_panel_has_only_context_and_the_ai_cost():
    # Las herramientas van en el menú del «+» del composer, no en el panel.
    right = right_panel()
    headings = re.findall(r"<h2[^>]*>\s*(.*?)\s*</h2>", right, re.S)

    assert headings == ["Panel", "Contexto", "Costo del chat"]
    assert 'id="tools"' in re.search(r'<div id="attach-popover".*?id="tools"[^>]*>', chat_html(), re.S).group(0)
    for gone in ("side-channels", "Administrar canales", "Canales"):
        assert gone not in right, gone


def test_the_shell_script_no_longer_draws_channels_or_voice_in_the_panel():
    shell, css = page("shell.js"), page("style.css")

    for gone in ("renderSideChannels", "renderSideVoice", "VOICE_ROWS", "side-channels"):
        assert gone not in shell, gone
    assert ".side-right .channels" not in css and ".side-link" not in css
    assert "state.model" in shell  # lo que sí sigue leyendo: el modelo del header


def test_chat_waits_for_its_conversation_before_showing_the_greeting():
    # Sin esto, al volver al chat se ve un instante «chat nuevo» y después salta a la última conversación.
    assert re.search(r"<body[^>]*class=\"booting\"", chat_html())
    assert 'classList.remove("booting")' in js("app.js")
    css = js("style.css")
    assert re.search(r"body\.booting \.log\s*\{\s*opacity:\s*0;", css)


# --- Modo IA: selector, barra de costo y límite ---------------------------------------


def test_new_chat_offers_commands_only_as_a_secondary_switch():
    # Un chat nuevo arranca con IA; «solo comandos» es un interruptor, no dos botones iguales.
    html = chat_html()

    tag = re.search(r'<button[^>]*id="mode-commands"[^>]*>', html).group(0)
    assert 'type="button"' in tag and 'aria-pressed="false"' in tag
    assert "Solo comandos" in html and "sin IA, sin costo" in html and "Gemini" not in html
    assert 'id="mode-normal"' not in html and 'id="mode-ai"' not in html


def test_a_new_chat_starts_with_ai_when_it_is_ready():
    script = js("app.js")

    assert 'const defaultMode = () => (aiReady ? "ai" : "normal");' in script
    assert "chooseMode(defaultMode());" in script[script.index("async function openNewChat"):]
    assert "if (!started && !modePicked) chooseMode(defaultMode());" in script


def test_ai_cost_lives_in_the_right_panel_below_the_context():
    right = right_panel()

    assert 'id="ai-bar"' in right and 'id="ai-bar"' not in chat_html().replace(right, "")
    assert right.index("h-context") < right.index("h-ai-cost")


def test_ai_bar_exists_and_is_hidden_by_default():
    html = chat_html()

    assert re.search(r'<section[^>]*id="ai-bar"[^>]*\bhidden\b', html)
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


# --- Menciones con «@» en el chat con IA -----------------------------------------


def test_the_mention_start_rule_in_the_browser_matches_the_servers():
    # «@» solo al inicio o tras un espacio (así los mails no cuentan): lo escriben app.js y attachments.py.
    from epsilon.ai import attachments

    found = re.search(r"MENTION_AT_CURSOR\s*=\s*/(.+)/;", js("app.js")).group(1)

    assert found.startswith(attachments.MENTION.split("(?:")[0])


def test_at_suggestions_come_from_the_library_and_only_in_ai_mode():
    source = js("app.js")

    assert "/api/library?" in source
    assert "viewMode" in source


def test_uploading_in_ai_mode_saves_to_the_library_instead_of_summarizing():
    source = js("app.js")

    assert '"/api/files"' in source
    assert "wantsCsv" in source.split("uploadToLibrary(file)")[0].split("function setFile")[1]


# --- Aviso al cambiar de modelo en una conversación --------------------------------------


def test_the_chat_exposes_the_model_guard_and_asks_before_sending_after_a_model_change():
    source = js("app.js")

    assert "epsilonModelGuard" in source
    assert "model_change" in source and "epsilonModal" in source
    assert "window.confirm" not in source and "alert(" not in source
    assert "Enviar igual" in source


# --- Copiar un mensaje ---------------------------------------------------------


def test_every_bubble_gets_a_copy_button_that_copies_the_original_text():
    code = js("app.js")

    assert "column.append(body, copyButton(message.text))" in code  # tuyos y de la IA, debajo de la burbuja, con el texto sin dibujar
    assert "navigator.clipboard.writeText(text)" in code
    assert ".msg-copy" in static_path("style.css").read_text(encoding="utf-8")


# --- Ancladas, Canales y Recientes ------------------------------------------------


def test_the_list_has_three_collapsible_sections_and_a_three_dots_menu():
    source = js("shell.js")

    for title in ('"Ancladas"', '"Canales"', '"Recientes"'):
        assert title in source
    assert "aria-expanded" in source and "epsilon.recents.collapsed" in source  # plegar se recuerda
    assert 'method: "PUT"' in source and "/pin" in source
    for item in ('"Editar"', '"Borrar"', '"Anclar"', '"Desanclar"'):
        assert item in source
    assert 'setAttribute("role", "menu")' in source and '"Escape"' in source  # se usa con teclado
    assert ".recent-menu" in static_path("style.css").read_text(encoding="utf-8")


def test_the_static_recents_title_is_gone_from_every_page_because_shell_js_draws_the_sections():
    for name in ("index.html", "config.html", "canales.html", "uso.html", "biblioteca.html", "permisos.html", "rutinas.html"):
        assert "h-recents" not in page(name), name


# --- el botón de enviar es el cuadradito que cancela mientras se espera -----------------------


def test_the_send_button_has_both_icons_and_toggles_between_them_with_css():
    html = chat_html()
    css = js("style.css")

    assert 'class="icon-send"' in html and 'class="icon-stop"' in html and "<rect" in html  # el cuadradito
    assert ".send-btn.stop .icon-send" in css and ".send-btn.stop .icon-stop" in css


def test_while_busy_the_button_is_enabled_and_named_cancelar():
    source = js("app.js")
    set_busy = source[source.index("function setBusy"):source.index("function setBusy") + 900]

    assert 'classList.toggle("stop", value)' in set_busy and '"Cancelar"' in set_busy and '"Enviar"' in set_busy
    assert "sendButton.disabled" not in source  # si estuviera deshabilitado, no se podría tocar para cancelar


def test_clicking_while_busy_cancels_and_clicking_when_idle_sends():
    source = js("app.js")
    handler = source[source.index('composer.addEventListener("submit"'):][:400]

    assert "if (busy) cancelRequest()" in handler and "else submit()" in handler


def test_cancelling_aborts_the_request_and_tells_the_server():
    source = js("app.js")

    assert "new AbortController()" in source and "controller.abort()" in source
    assert '"/api/cancel"' in source and source.count("signal") >= 4  # al fetch de mensajes y de archivos
    assert "if (current.cancelled)" in source  # cancelar no se muestra como un error


def test_the_cancelled_text_matches_the_one_the_server_saves():
    match = re.search(r'const CANCELLED_TEXT = "([^"]+)"', js("app.js"))

    assert match and match.group(1) == core.CANCELLED


# --- El cajón en ventanas medianas ---------------------------------------------------------------


def test_tapping_the_conversation_menu_does_not_close_the_drawer():
    # El menú de tres puntitos vive en el <body>, fuera de .side-left: sin esta excepción, tocar «Borrar» o «Editar»
    # contaba como «tocar afuera», cerraba el cajón y la confirmación quedaba dibujada en una barra cerrada.
    source = js("shell.js")
    handler = source[source.index('document.addEventListener("click"'):]
    handler = handler[: handler.index("});")]

    assert ".recent-menu" in handler
    # Y no se decide con closest() sobre el DOM vivo: «Cancelar» y los ítems del menú vuelven a dibujar la lista dentro de su
    # propio clic, el botón queda desconectado y ya no parece estar en la barra. composedPath() se fija al empezar el evento.
    assert "composedPath()" in handler and "closest(" not in handler


# --- Historial de mensajes en el cuadro de texto y avatar del bot ----------------


def test_input_history_navigation_structures():
    app = js("app.js")

    assert "let messageHistory = [];" in app
    assert "let historyIndex = -1;" in app
    assert "let historyDraft = \"\";" in app
    assert "function initHistory(messages)" in app
    assert "function handleHistoryKey(event)" in app
    assert "handleHistoryKey(event)" in app
    assert "initHistory(data.messages)" in app
    assert "messageHistory.push(text)" in app


def test_bot_responses_use_the_brand_logo_avatar():
    app = js("app.js")
    css = static_path("style.css").read_text(encoding="utf-8")

    assert "function botMark()" in app
    assert 'mark.src = "/static/img/apple-touch-icon.png"' in app
    assert "row.append(botMark())" in app
    # Tanto en render() como en showThinking()
    assert app.count("row.append(botMark())") == 2

    # En CSS no lleva borde de texto y mantiene el estilo de logo de marca
    assert "img.mark" in css
    assert ".msg.bot .mark" in css


def test_input_history_navigation_behavior_in_node():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")

    app = js("app.js")
    block = app[app.index("function initHistory") : app.index('input.addEventListener("keydown"')]
    script = f"""
    let messageHistory = [];
    let historyIndex = -1;
    let historyDraft = "";
    const input = {{ value: "", selectionStart: 0, selectionEnd: 0, setSelectionRange(s, e) {{ this.selectionStart = s; this.selectionEnd = e; }} }};
    function resizeInput() {{}}
    function closeSuggestions() {{}}

    {block}

    initHistory([
        {{ role: "user", text: "primer mensaje" }},
        {{ role: "bot", text: "respuesta" }},
        {{ role: "user", text: "segundo mensaje" }},
    ]);

    if (messageHistory.length !== 2 || messageHistory[0] !== "primer mensaje" || messageHistory[1] !== "segundo mensaje") {{
        throw new Error("initHistory falló: " + JSON.stringify(messageHistory));
    }}

    input.value = "";
    input.selectionStart = 0;
    input.selectionEnd = 0;
    let evUp = {{ key: "ArrowUp", preventDefault() {{}} }};
    let handled = handleHistoryKey(evUp);
    if (!handled || input.value !== "segundo mensaje" || historyIndex !== 1) {{
        throw new Error("ArrowUp no trajo el último mensaje: " + input.value);
    }}

    handled = handleHistoryKey(evUp);
    if (!handled || input.value !== "primer mensaje" || historyIndex !== 0) {{
        throw new Error("ArrowUp no trajo el mensaje anterior: " + input.value);
    }}

    handled = handleHistoryKey(evUp);
    if (!handled || input.value !== "primer mensaje" || historyIndex !== 0) {{
        throw new Error("ArrowUp al tope falló: " + input.value);
    }}

    let evDown = {{ key: "ArrowDown", preventDefault() {{}} }};
    handled = handleHistoryKey(evDown);
    if (!handled || input.value !== "segundo mensaje" || historyIndex !== 1) {{
        throw new Error("ArrowDown no avanzó al segundo: " + input.value);
    }}

    handled = handleHistoryKey(evDown);
    if (!handled || input.value !== "" || historyIndex !== -1) {{
        throw new Error("ArrowDown no restauró borrador: " + input.value);
    }}

    input.value = "mi borrador";
    input.selectionStart = input.value.length;
    input.selectionEnd = input.value.length;
    handled = handleHistoryKey(evUp);
    if (!handled || input.value !== "segundo mensaje" || historyDraft !== "mi borrador") {{
        throw new Error("ArrowUp no guardó el borrador escrito");
    }}
    handled = handleHistoryKey(evDown);
    if (!handled || input.value !== "mi borrador" || historyIndex !== -1) {{
        throw new Error("ArrowDown no restauró el borrador escrito");
    }}

    input.value = "borrador esc";
    input.selectionStart = 0;
    input.selectionEnd = 0;
    handleHistoryKey(evUp);
    let evEsc = {{ key: "Escape", preventDefault() {{}} }};
    handled = handleHistoryKey(evEsc);
    if (!handled || input.value !== "borrador esc" || historyIndex !== -1) {{
        throw new Error("Escape no restauró el borrador");
    }}

    console.log("OK");
    """
    done = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "OK"


# --- Chip «IA» junto al título -------------------------------------------------


def test_header_has_an_ia_chip_next_to_the_title_and_outside_of_it():
    html = chat_html()
    chip = re.search(r'<span id="crumb-ai"[^>]*>IA</span>', html).group(0)
    crumb = re.search(r'<span id="crumb" data-dynamic>.*?</span>', html).group(0)

    assert "hidden" in chip and 'class="ai-mark"' in chip
    assert chip not in crumb  # shell.js reescribe el contenido de #crumb: el chip no puede vivir ahí


def test_app_updates_the_chip_when_the_mode_changes():
    js = static_path("app.js").read_text(encoding="utf-8")
    fn = js[js.index("function showAiChip"):]
    fn = fn[: fn.index("\n}\n")]

    assert 'getElementById("crumb-ai")' in js
    assert "crumbAi.hidden = !aiMode()" in fn
    for caller in ("function chooseMode", "function showMode", "function clearScreen"):
        body = js[js.index(caller):]
        assert "showAiChip()" in body[: body.index("\n}\n")], caller


# --- Tocar una idea del saludo ---------------------------------------------------


def test_a_welcome_card_does_nothing_if_another_conversation_was_opened_meanwhile():
    # Si abrís otra conversación mientras se crea el chat nuevo, el texto de la idea no puede
    # aparecer escrito en la que acabás de abrir: useCard compara el loadToken de después con el de antes.
    js_text = static_path("app.js").read_text(encoding="utf-8")
    body = js_text[js_text.index("async function useCard"):]
    body = body[: body.index("\n}\n")]

    assert body.index("loadToken") < body.index("await")
    assert body.count("loadToken") >= 2 and "return" in body[body.index("await"):body.index("writePrompt(")]


def test_config_says_that_the_voice_names_live_in_the_voice_section():
    html = page("config.html")
    names = html[html.index('aria-labelledby="s-nombres"'):html.index('aria-labelledby="s-chat"')]

    assert "Voz" in names and "hablarle por voz" in names


def test_the_right_panel_has_no_companion_and_voice_has_one_button():
    html = chat_html()

    assert "companion" not in html and "companion" not in js("app.js").replace("epsilon_companion_muted", "")
    assert html.count('id="voice-toggle"') == 1


def test_the_placeholder_invites_to_ask_or_to_type_a_command():
    script = js("app.js")

    assert 'placeholder="Pedile algo o escribí / para un comando"' in chat_html()
    assert 'const COMMANDS_PLACEHOLDER = "Escribí un comando, por ejemplo /ayuda";' in script
    assert "input.placeholder = defaultPlaceholder()" in script[script.index("function showAiChip"):]


def test_the_right_panel_folds_by_itself_when_it_has_nothing_to_show():
    # Sin nada guardado y sin costo, se ve contraído; lo que elegiste con el botón no se pisa.
    shell, script = js("shell.js"), js("app.js")

    assert "window.epsilonShell = { setRightEmpty };" in shell
    assert 'layout.right === "open" && (!rightEmpty || rightPeek)' in shell
    assert "window.epsilonShell.setRightEmpty(contextItems.length === 0 && aiBar.hidden);" in script
    for caller in ("function renderContext", "function showChatBar"):
        body = script[script.index(caller):]
        assert "updateRightPanel();" in body[: body.index("\n}\n")], caller
