# Atajo de texto del Voice Companion (Alt+C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un segundo atajo global (`VOICE_TEXT_HOTKEY`, por defecto `ALT, C`) que abre en la ventanita del Voice Companion un campo de texto; al enviar, el turno de IA corre con el contexto de la ventana en foco, se ve en vivo en la tarjeta y no habla.

**Architecture:** El script del atajo ya manda una palabra al socket; se suma la palabra `ask`. El canal (`channels/voice.py`) tiene un `Prompter` que lee la ventana activa, le pide a la ventanita el modo entrada (`op: input`) y, cuando ella devuelve `submit:<json>` por stdout, llama a `Assistant.ask(..., source="text")`. Los permisos usan un modo hermano de `SAFE_AUTO` (`SAFE_AUTO_TEXT`) que deja correr la captura de pantalla sola si `IMAGES_TO_CLOUD` lo permite.

**Tech Stack:** Python 3.12, GTK4 + gtk4-layer-shell (proceso aparte `voice/overlay_window.py`), pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-29-voice-text-hotkey-design.md`

## Global Constraints

- Nombres de código en inglés; mensajes al usuario en español.
- Los tests nunca llaman a un modelo, ni a `hyprctl`, ventanas, audio o procesos reales (fakes; `_ctl`, `_hyprctl`, `_spawn` ya están bloqueados). La ventana GTK real **no** se abre en los tests ni la abre el agente: la prueba en vivo la hace la persona (Task 8).
- Todo valor por ajuste pasa por `settings/config.py` (`config.value`, nunca `os.environ`).
- Lo escrito en el campo es dato: tope 500 caracteres, se valida dentro del proceso de la ventana y otra vez en Epsilon; el texto de la ventana en foco pasa por `config.redact`, se recorta a 200 caracteres y va rotulado como dato.
- Tras cada tarea: `uv run pytest` verde y commit (sin firma de Claude en los mensajes, regla de la memoria del proyecto: **no** agregar `Co-Authored-By`). Nunca `git push`.
- Tras tocar el código del bot: `systemctl --user restart epsilon` y confirmar que quedó activo (Task 8).

## Review Focus

- Un `hide` viejo (el temporizador del turno anterior) llega mientras la persona escribe: la tarjeta no debe cerrarse (Task 5).
- Alt+C con el campo ya abierto lo cierra; si el proceso de la ventana murió, abre de nuevo en vez de quedar trabado (Task 7).
- Texto vacío, solo espacios o de más de 500 caracteres no inicia un turno (Tasks 5 y 7).
- `VOICE_TEXT_HOTKEY` vacío o igual a `VOICE_HOTKEY`: no se aplica ningún atajo duplicado y se avisa una vez (Task 2).
- Sin ventanita (`VOICE_OVERLAY=no` o falló) Alt+C avisa por notificación que la necesita y no inicia nada (Task 7).
- Enviar un texto mientras otro turno sigue corriendo corta el anterior (Task 7).
- Las tarjetas viejas no ensucian el turno nuevo: `_text` y `_heard` del `Presenter` se reinician al empezar un turno de texto (Task 4).

---

### Task 1: Ajuste `VOICE_TEXT_HOTKEY`

**Files:**
- Modify: `src/epsilon/settings/config.py` (junto a `VOICE_HOTKEY`, ~línea 600, y en el diccionario `_J`, ~línea 696)
- Modify: `src/epsilon/channels/web_static/config.html` (campo junto a `VOICE_HOTKEY`, ~línea 478)
- Test: `tests/settings/test_companion_settings.py`

**Interfaces:**
- Produces: `config.value("VOICE_TEXT_HOTKEY") -> str` (normalizado «ALT, C» o `""` = desactivado); `config.BY_KEY["VOICE_TEXT_HOTKEY"].validate(text)`.

- [ ] **Step 1: Write the failing tests** en `tests/settings/test_companion_settings.py`:
  `test_the_text_hotkey_defaults_to_alt_c` (`config.value("VOICE_TEXT_HOTKEY") == "ALT, C"`), `test_the_text_hotkey_is_normalized` (`validate("super shift,z") == "SUPER SHIFT, Z"`), `test_the_text_hotkey_can_be_empty_to_turn_it_off` (`validate("") == ""`), `test_a_bad_text_hotkey_is_rejected` (`validate("X")` lanza `ValueError`, igual que el test existente de `VOICE_HOTKEY`).
- [ ] **Step 2: Run** `uv run pytest tests/settings/test_companion_settings.py -v` → FAIL (`KeyError: 'VOICE_TEXT_HOTKEY'`).
- [ ] **Step 3: Implement** en `config.py` un `Setting("VOICE_TEXT_HOTKEY", "Atajo para escribirle al Voice Companion", live=True, default="ALT, C", validate=_optional_hotkey)` donde `_optional_hotkey(value: str) -> str` devuelve `""` si el texto está vacío y si no delega en `_hotkey`; y su `_J(name="voice.text_hotkey", help="Atajo para escribirle al Voice Companion (abre un campo de texto): modificador y tecla, por ejemplo ALT, C. Vacío lo desactiva")`. En `config.html` un campo idéntico al de `VOICE_HOTKEY` (mismo marcado, `data-key="VOICE_TEXT_HOTKEY"`, placeholder «ALT, C»).
- [ ] **Step 4: Run** `uv run pytest -q` → PASS (si falla un test que compara `settings_default.json`/schema, regenerarlos con `generate.py` como hace el proyecto y verificar que solo suma el ajuste nuevo).
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: VOICE_TEXT_HOTKEY setting (ALT, C by default)"`

---

### Task 2: Segundo atajo: script, archivos de Hyprland y `Hotkey`

**Files:**
- Modify: `src/epsilon/voice/client.py`
- Modify: `src/epsilon/utils/hyprland.py` (`conf_text`, `lua_text`, `write_conf`)
- Modify: `src/epsilon/channels/voice.py` (`WORDS`, clase `Hotkey`, `run()` crea el segundo script)
- Test: `tests/voice/test_client.py`, `tests/utils/test_hyprland.py` (el archivo que ya cubre `write_conf`; si el nombre difiere, el que lo haga), `tests/channels/test_voice.py` (zona de `Hotkey`, ~línea 417)

**Interfaces:**
- Produces:
  - `client.ASK_SCRIPT_NAME = "epsilon-voice-ask"`; `client.script_text(default_word: str) -> str`; `client.SCRIPT == client.script_text("toggle")` (no se rompe lo existente); `client.write_script(folder: Path, word: str = "toggle") -> Path` escribe `SCRIPT_NAME` para `toggle` y `ASK_SCRIPT_NAME` para `ask`.
  - `hyprland.write_conf(folder, hotkey, command, extra: tuple[str, str] | None = None) -> tuple[Path, Path]` y `conf_text/lua_text(hotkey, command, extra=None)`: `extra` es `(hotkey, command)` y suma su línea de bind / su `hl.bind` al **mismo** par de archivos (así la persona no incluye un archivo más).
  - `Hotkey(folder, script, announce, text_script: Path | None = None)`; `Hotkey.applied_text: str | None`.

- [ ] **Step 1: Write the failing tests**
  - `test_client.py`: `test_the_ask_script_defaults_to_the_ask_word` (escribe con `write_script(tmp_path, "ask")`, lo corre con un socket falso como hacen los tests existentes de `run_script` y el servidor recibe `ask\n`); `test_the_toggle_script_is_unchanged` (`write_script(tmp_path)` sigue llamándose `epsilon-voice-toggle` y manda `toggle`).
  - hyprland: `test_write_conf_with_an_extra_bind_writes_both_in_the_same_files` (el `.conf` contiene `bind = ALT, X, exec, /a/toggle` y `bind = ALT, C, exec, /a/ask`; el `.lua` contiene los dos `hl.bind`); `test_write_conf_without_extra_is_the_same_as_before`.
  - `test_voice.py` (usa los fakes de `_ctl` ya existentes en esa zona): `test_hotkey_applies_both_shortcuts` (con `VOICE_HOTKEY="ALT, X"`, `VOICE_TEXT_HOTKEY="ALT, C"` se llama `apply_hotkey` dos veces, una con cada script); `test_an_empty_text_hotkey_applies_only_the_voice_one`; `test_the_same_key_twice_is_refused_once` (ambos `ALT, X` → `announce` recibe un solo aviso que dice que son iguales y el atajo de texto no se aplica); `test_changing_only_the_text_hotkey_unbinds_the_old_text_one` (`previous=` del anterior).
- [ ] **Step 2: Run** esos tests → FAIL.
- [ ] **Step 3: Implement**
  - `client.py`: extraer el cuerpo de `SCRIPT` a `script_text(default_word)` (la palabra por defecto entra en el `sys.argv[1] if … else "<word>"`; ojo con las llaves del f-string).
  - `hyprland.py`: `conf_text`/`lua_text` agregan la línea del `extra` si viene, validándola con `normalize_hotkey` y `_command` igual que la principal; `write_conf` pasa `extra`.
  - `channels/voice.py`: `WORDS = ("toggle", "cancel", "status", "ask")`. `Hotkey.sync()` lee los dos ajustes; la clave `seen` pasa a ser la tupla `(VOICE_HOTKEY, VOICE_TEXT_HOTKEY)`; normaliza ambos (el de texto vacío o sin `text_script` = ninguno); si son iguales avisa una vez y descarta el de texto; llama a `write_conf(..., extra=...)` una vez y a `apply_hotkey` por cada atajo con su `previous` (`applied`, `applied_text`). Un `ValueError`/`HyprlandError` mantiene el comportamiento actual (se dice una vez y se sigue con lo anterior). El estado `state.set("companion", "running", …)` menciona ambos atajos.
  - `run()`: `ask_script = client.write_script(folder, "ask")` y pasarlo a `Hotkey(..., text_script=ask_script)`.
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: second Voice Companion hotkey (script, Hyprland files, Hotkey sync)"`

---

### Task 3: Modo de permisos `SAFE_AUTO_TEXT`

**Files:**
- Modify: `src/epsilon/ai/permissions.py` (`MODES`, `decide`, `offered`, `_decide_unattended`, `auto_ok`)
- Test: `tests/ai/test_permissions.py`, `tests/test_core_voice_permissions.py`

**Interfaces:**
- Produces: `permissions.SAFE_AUTO_TEXT = "safe-auto-text"`; `permissions.auto_ok(tool, text: bool = False) -> bool`; `_decide_unattended(tool_id, text: bool = False)`. `permissions.unattended(cid)` vale también para el modo nuevo.

- [ ] **Step 1: Write the failing tests** (en el estilo de los tests de `SAFE_AUTO`, ~línea 235 de `test_permissions.py`, con `llm`/`config` falsos):
  - `test_text_mode_is_unattended_like_voice_mode`.
  - `test_text_mode_runs_the_screenshot_alone_with_a_cloud_model_when_images_may_go_to_the_cloud` (`IMAGES_TO_CLOUD=si`, modelo `gemini:…` → `decide(cid, "pc.captura") == "run"`).
  - `test_text_mode_does_not_offer_the_screenshot_with_images_to_cloud_off` (`IMAGES_TO_CLOUD=no`, modelo de nube → no está en `offered(cid)`).
  - `test_voice_mode_still_blocks_the_screenshot_with_a_cloud_model` (regresión: `SAFE_AUTO` igual que hoy).
  - `test_text_mode_still_blocks_destructive_tools_and_connectors` (`olvidar` y una herramienta `mcp_…` → `block`).
  Usar el id real de la herramienta de captura (buscarlo con `tools.ai_id` sobre la que tiene `sees_screen`).
- [ ] **Step 2: Run** → FAIL (`AttributeError: SAFE_AUTO_TEXT`).
- [ ] **Step 3: Implement** `MODES = (SAFE_AUTO, SAFE_AUTO_TEXT)`; helper `_safe_mode(cid) -> str | None` que devuelve el modo si es uno de los dos; `decide`/`offered` lo usan en lugar de comparar con `SAFE_AUTO` y pasan `text=(mode == SAFE_AUTO_TEXT)`. En `auto_ok`, la regla de `sees_screen` pasa a: permitida si `_local_model()` **o** (`text` y `llm.images_allowed(<proveedor del modelo del chat>)`); si no, `False`. El proveedor sale de `llm.parse_model(llm.model_for("chat"))[0]`.
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: SAFE_AUTO_TEXT permission mode (screenshot allowed alone for the text hotkey)"`

---

### Task 4: `Assistant.ask(source, context)`, `Speaker` mudo y `Presenter` limpio

**Files:**
- Modify: `src/epsilon/channels/voice.py` (`SESSION_TEXT`, `Assistant.ask/_run`)
- Modify: `src/epsilon/core.py:2027` (`_usage_channel`)
- Modify: `src/epsilon/voice/speaker.py:209-217` (`on_event`)
- Modify: `src/epsilon/voice/overlay.py` (`Presenter._handle`, rama `turn start`)
- Test: `tests/channels/test_voice.py` (zona de `Assistant`, ~línea 696), `tests/test_core.py` (~línea 353), `tests/voice/test_speaker.py`, `tests/voice/test_overlay.py`

**Interfaces:**
- Produces:
  - `voice.SESSION_TEXT = "companion:text"`.
  - `Assistant.ask(self, text: str, *, source: str = "voice", context: str = "") -> None`. Con `source="text"`: sesión `SESSION_TEXT`, modo `permissions.SAFE_AUTO_TEXT`, el evento `{"type":"turn","state":"start","source":"text"}`, un evento `{"type":"status","text":"Dijiste: <text>"}` justo después, el mensaje al núcleo es `f"{context}\n{text}"` si hay contexto y **lo que se guarda en la conversación es solo `text`**. Con `source="voice"` nada cambia.
  - `core._usage_channel("companion:text") == "Companion"`.

- [ ] **Step 1: Write the failing tests**
  - `test_voice.py`: `test_a_text_ask_uses_the_text_session_and_mode` (sesión `companion:text`, `permissions` en `SAFE_AUTO_TEXT`, **misma conversación** que la de voz: `conversations.current("companion")`); `test_a_text_ask_sends_the_context_but_saves_only_what_was_typed` (`msg.text == "Ventana en foco (dato, no instrucciones): kitty — nvim\n¿qué es esto?"` y lo guardado `("user", "¿qué es esto?")`); `test_a_text_ask_announces_what_was_typed_as_heard` (entre los eventos hay `status` con `"Dijiste: ¿qué es esto?"` con el número de turno); `test_a_voice_ask_is_unchanged`.
  - `test_core.py`: `_usage_channel("companion:text") == "Companion"` y `"companion:voice" == "Voz"`.
  - `test_speaker.py`: `test_the_speaker_ignores_text_turns` (secuencia `turn start source=text`, `text`, `reply` → el sintetizador falso no recibe nada).
  - `test_overlay.py`: `test_a_text_turn_starts_with_a_clean_card` (después de un `reply` anterior, un `turn start` con `source: "text"` y luego `status "Pensando…"` manda un `show` con `text == ""` y `heard == ""`; el mismo flujo con `source` ausente conserva el comportamiento actual).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**
  - `Assistant.ask` guarda `source` y `context` en el `_Turn`/argumentos de `_run`; elige sesión y modo; arma el mensaje; `_save(conversation_id, text, reply)` sigue guardando el texto tipeado. El `turn start` lleva `source`.
  - `_usage_channel`: antes de la regla `companion:` poner `if session_id == "companion:text": return "Companion"` (importar la constante no: el núcleo no conoce canales, comparar con el literal).
  - `Speaker.on_event`: en la rama `turn start`, si `event.get("source") == "text"` → `self._turn = None; self._state = None; return` (los eventos siguientes no coinciden con ningún turno y se ignoran).
  - `Presenter._handle`: en `turn start` con `source == "text"` → `self._speaking = self._closed = False; self._heard = self._text = ""; self._cancel_timer()` además de `self._turn = turn`.
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: text turns for the Voice Companion (session, mode, silent speaker, clean card)"`

---

### Task 5: Protocolo de la ventanita: modo entrada

**Files:**
- Modify: `src/epsilon/voice/overlay_window.py`
- Test: `tests/voice/test_overlay_window.py`

**Interfaces:**
- Produces (funciones puras, testeables sin GTK):
  - `OPS = ("show", "hide", "quit", "input", "close_input")`; `KINDS` suma `"asking"`.
  - `MAX_INPUT = 500`.
  - `clean_input(command: dict) -> dict`: `{"op": "input", "title": str (≤ MAX_TITLE, por defecto "Preguntale a Epsilon"), "layout": {…igual que clean_show…}}`.
  - `accepts(command: dict, typing: bool) -> bool`: mientras `typing` es verdadero solo se aceptan `input`, `close_input` y `quit`; `show` y `hide` se descartan.
  - `submitted(text: str) -> str | None`: el texto sin espacios de más, o `None` si queda vacío o pasa de `MAX_INPUT`.
  - `submit_line(text: str) -> str`: `"submit:" + json.dumps(text, ensure_ascii=False)`.
- Protocolo por stdout (además de `ready`/`error:`): `submit:<json string>` y `closed`.

- [ ] **Step 1: Write the failing tests**: `test_parse_accepts_the_input_ops`; `test_clean_input_limits_title_and_fills_the_layout`; `test_accepts_ignores_show_and_hide_while_typing` (`accepts({"op":"hide"}, True) is False`, `accepts({"op":"hide"}, False) is True`, `accepts({"op":"close_input"}, True) is True`); `test_submitted_trims_and_refuses_empty_or_too_long` (`"  hola "` → `"hola"`, `"   "` → `None`, `"x"*501` → `None`, `"x"*500` válido); `test_submit_line_round_trips_quotes_and_newlines` (`json.loads(line.removeprefix("submit:")) == text`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** las funciones puras y, dentro de `main()`, el modo entrada en `Card`: un `Gtk.Entry` (oculto salvo en entrada, `set_max_length(MAX_INPUT)`), `enter_input(command)` (aplica el layout, clase CSS `asking`, muestra título + campo, `LayerShell.set_keyboard_mode(self.win, INPUT_KEYBOARD_MODE)` con `INPUT_KEYBOARD_MODE = LayerShell.KeyboardMode.EXCLUSIVE` en una constante con un comentario de que `ON_DEMAND` es la alternativa si Hyprland no devuelve bien el foco, restaura la región de entrada para que reciba el teclado, `entry.grab_focus()`, `typing = True`), y `leave_input()` (vuelve a `KeyboardMode.NONE`, click-through otra vez, oculta el campo, `typing = False`). Enter (`activate`): `submitted(entry.get_text())`; si es `None` no hace nada; si no, imprime `submit_line(text)` (con `flush=True`), `leave_input()` y esconde la tarjeta. Esc (`Gtk.EventControllerKey`): imprime `closed`, `leave_input()` y esconde. `close_input` hace lo mismo que Esc. `handle()` consulta `accepts(command, typing)` antes de actuar. Agregar `--demo-input` que abre el modo entrada sin Epsilon (la corre la persona). CSS: `.card.asking .title` en un tono propio.
- [ ] **Step 4: Run** `uv run pytest tests/voice/test_overlay_window.py -v` → PASS (el test existente que vigila que `POSITIONS` coincida con `overlay.POSITIONS` sigue verde).
- [ ] **Step 5: Commit** `git commit -am "feat: input mode in the Voice Companion window (pure protocol helpers + GTK entry)"`

---

### Task 6: `overlay.Window` entrega `submit` y `closed`

**Files:**
- Modify: `src/epsilon/voice/overlay.py` (`Window.__init__`, `Window._read`)
- Test: `tests/voice/test_overlay.py`

**Interfaces:**
- Produces: `Window(announce, spawn=None, clock=…, defer=…, on_submit: Callable[[str], None] | None = None, on_closed: Callable[[], None] | None = None)`. `_read` interpreta `submit:<json string>` → `on_submit(text)` (solo si decodifica a `str` de 1 a 500 caracteres; si no, se descarta) y `closed` → `on_closed()`. Los callbacks se llaman **fuera** del candado de la `Window` y una excepción en ellos nunca mata el hilo lector.

- [ ] **Step 1: Write the failing tests** (con el `spawn` falso que ya usan los tests de `Window`, cuyo `stdout` es un `BytesIO`/pipe): `test_a_submit_line_calls_on_submit_with_the_decoded_text` (`submit:"hola \"mundo\""`); `test_a_closed_line_calls_on_closed`; `test_a_malformed_or_oversized_submit_is_dropped` (JSON roto, número, `""`, 501 caracteres); `test_a_failing_callback_does_not_stop_the_reader` (después sigue llegando `ready`/otra línea).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** el parseo en `_read` (junto a `ready` y `error:`), con un límite `MAX_SUBMIT = 500` definido en `overlay.py` (un test vigila que coincida con `overlay_window.MAX_INPUT`, como ya se hace con `POSITIONS`).
- [ ] **Step 4: Run** `uv run pytest tests/voice -q` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: Window reads submit/closed lines from the overlay process"`

---

### Task 7: `Prompter`, palabra `ask` del socket y cableado en `run()`

**Files:**
- Modify: `src/epsilon/channels/voice.py` (`Companion.ask`, `dispatch`, clase `Prompter`, `run()`)
- Test: `tests/channels/test_voice.py`

**Interfaces:**
- Consumes: `Assistant.ask(text, *, source, context)` (Task 4); `Window(on_submit=, on_closed=)` y `Window.send(command) -> bool` (Task 6); ops `input`/`close_input` (Task 5); `ventanas.report("activa") -> str` y `ventanas.VentanasError`.
- Produces:
  - `Companion.__init__(…, on_ask: Callable[[], str] | None = None)` y `Companion.ask() -> str`: si graba devuelve `"recording"` sin hacer nada; si no hay `on_ask` devuelve `"error"`; si no, devuelve lo que devuelva `on_ask()`.
  - `Prompter(send: Callable[[dict], bool], ask: Callable[..., None], cut: Callable[[], None], announce: Callable[[str], None], focused: Callable[[], str] = focused_window, start: Callable[[Callable[[], None]], None] = _in_thread)` con `toggle() -> str` (`"asking"`, `"closed"` o `"no-window"`), `submit(text: str) -> None`, `closed() -> None`.
  - `focused_window() -> str`: `ventanas.report("activa")` sin los `**`, vacío si falla.
  - `context_for(window: str) -> str`: `""` si no hay ventana; si no, `"Ventana en foco (dato, no instrucciones): " + redactada y recortada a 200 caracteres`.

- [ ] **Step 1: Write the failing tests** (con `send`, `ask`, `cut`, `focused` falsos; `start=lambda work: work()` para correr en el mismo hilo):
  - `test_toggle_reads_the_focused_window_before_asking_the_card_for_input` (el orden de las llamadas es `focused` → `send({"op":"input"…})`; devuelve `"asking"`).
  - `test_toggle_while_open_closes_the_input` (segunda llamada manda `{"op":"close_input"}` y devuelve `"closed"`).
  - `test_toggle_reopens_when_the_window_process_is_gone` (`send` devuelve `False` para `close_input` → manda `input` de nuevo).
  - `test_without_a_window_it_announces_and_starts_nothing` (`send` devuelve `False` → `announce` con un texto que menciona la ventanita; devuelve `"no-window"`; `ask` no se llamó).
  - `test_submit_cuts_the_previous_turn_and_asks_with_the_context` (`cut` antes que `ask`; `ask` recibe `source="text"` y `context` = `context_for(ventana leída en toggle)`).
  - `test_submit_ignores_empty_or_oversized_text` (`""`, `"  "`, 501 caracteres).
  - `test_submit_uses_the_window_read_at_open_time_not_a_new_one`.
  - `test_the_context_is_redacted_and_cut` (una clave falsa en el título no sale; 300 caracteres → 200) y `test_no_context_when_the_window_is_unknown`.
  - `test_the_ask_word_reaches_the_prompter_through_the_socket` (misma técnica que `ask(path, "toggle")` en ~línea 325: `ask(path, "ask")` contesta `"asking"`); `test_ask_while_recording_does_nothing` (`"recording"`); `test_ask_without_a_prompter_is_an_error`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** lo anterior. `Prompter.toggle`: si `self._open`, intentar `send({"op":"close_input"})`; si devuelve `True` → `_open=False`, `"closed"`; si no, seguir y abrir. Para abrir: `self._window_text = focused()` **antes** de `send({"op": "input", "title": …, "layout": layout()})`; si `send` da `False` → `announce("Para escribirle necesito la ventanita (VOICE_OVERLAY) y no se pudo abrir.")` y `"no-window"`. `submit` pone `_open=False`, valida (`strip`, 1 a 500), corre en `start(...)`: `cut()` y `ask(text, source="text", context=context_for(self._window_text))`. `closed` pone `_open=False`. En `run()`: construir `Window(_announce, on_submit=…, on_closed=…)` — como `Prompter` necesita `window.send` y `Window` necesita los callbacks del `Prompter`, crear primero el `Prompter` con una lambda `lambda c: window.send(c)` y asignar los callbacks con los kwargs una vez que existe; `cut` = `lambda: (voice.stop(), assistant.interrupt())` (sin `_warm_up`, no hay audio); pasar `on_ask=prompter.toggle` a `Companion`; y en el `finally` de `run()` no hace falta nada más (la ventana ya se cierra).
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: Alt+C opens the text input (Prompter, ask word, wiring)"`

---

### Task 8: Documentación, reinicio y prueba en vivo

**Files:**
- Modify: `CLAUDE.md` (párrafo del Voice Companion), `docs/plan-voice-companion.md` (guía de uso: Alt+C), `README.md` si menciona los atajos.
- Test: `tests/test_structure.py` (ya vigila la estructura; no debería necesitar cambios)

- [ ] **Step 1: Docs.** En `CLAUDE.md` agregar al párrafo del Voice Companion: atajo de texto (`VOICE_TEXT_HOTKEY`, palabra `ask` del socket, `Prompter`, modo entrada de la ventanita con `submit:`/`closed`, `SAFE_AUTO_TEXT`, sesión `companion:text` y gasto «Companion», captura sola con `IMAGES_TO_CLOUD`). En la guía de uso explicar Alt+C en un par de párrafos (qué se manda, que cerrar no corta el turno, que necesita la ventanita).
- [ ] **Step 2: Run** `uv run pytest -q` → PASS (todo verde).
- [ ] **Step 3: Commit** `git commit -am "docs: Voice Companion text hotkey"`
- [ ] **Step 4: Reiniciar el bot** `systemctl --user restart epsilon` y confirmar con `systemctl --user status epsilon` que está `active (running)`.
- [ ] **Step 5 (lo hace Gonzalo, no el agente):** probar en vivo. (a) `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 -I src/epsilon/voice/overlay_window.py --demo-input` y comprobar que el campo toma el teclado, Esc cierra y Enter imprime `submit:…`. (b) Con Epsilon andando, Alt+C: escribir «recordame agregar esto al proyecto», Enter, ver el streaming sin audio; Alt+C otra vez cierra; con otra ventana en foco, verificar que el título leído es el de esa ventana y no el de la tarjeta; comprobar que al cerrar la tarjeta el foco vuelve a la ventana de trabajo. Si el foco no vuelve, cambiar `INPUT_KEYBOARD_MODE` a `ON_DEMAND` (Task 5) y repetir. Informar el resultado.

---

## Self-review

- **Cobertura de la spec:** atajo y ajuste (T1, T2); tarjeta con campo, Enter, Esc/Alt+C (T5, T7); progreso en vivo sin audio (T4); contexto de ventana leído antes de abrir, redactado y recortado (T7); cerrar no corta, enviar otro texto sí (T7: `cut` solo en `submit`; `close_input` solo esconde); captura sola con `IMAGES_TO_CLOUD` (T3); Alt+X intacto (T3 regresión, T4 `source="voice"`); gasto «Companion» (T4); sin ventanita (T7); docs (T8). El riesgo del foco queda como paso manual explícito (T8.5) con la alternativa nombrada.
- **Consistencia de nombres:** `SAFE_AUTO_TEXT`, `SESSION_TEXT`, `Assistant.ask(text, *, source, context)`, `Prompter.toggle/submit/closed`, `Window(on_submit, on_closed)`, `clean_input/accepts/submitted/submit_line`, `MAX_INPUT == MAX_SUBMIT == 500` se usan igual en las tareas que los consumen.
- **Mis decisiones (marcadas en la spec):** `INPUT_KEYBOARD_MODE = EXCLUSIVE` como punto de partida; se conserva el estilo hablado `chat.SPOKEN` en la conversación del Companion (respuestas cortas, buenas para una tarjeta chica); la tarjeta de respuesta se esconde sola tras `VOICE_OVERLAY_HIDE_S`, como hoy.
