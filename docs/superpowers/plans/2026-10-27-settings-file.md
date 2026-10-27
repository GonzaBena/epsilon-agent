# Archivo de ajustes (`settings.json`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Los ajustes que no son claves pasan del `.env` a `DATA_DIR/settings.json` (JSONC, al estilo VS Code), con `settings_default.json` y `settings.schema.json` generados desde el registro, recarga en caliente y botones para abrirlos en el editor de texto.

**Architecture:** El registro `config.SETTINGS` crece (nombre JSON, tipo, rango, sección, ayuda, `loosens`) y suma los números del código (`settings/tunables.py`). Un módulo nuevo `settings/store.py` es el único que lee y escribe `settings.json` y guarda en memoria los valores aplicados; todo el código lee con `config.value(key)` / `config.number(key)` en vez de `os.environ`. El `.env` queda para claves, `ALLOWED_USER_ID` y `DATA_DIR`.

**Tech Stack:** Python 3 (stdlib `json`, `difflib`, `threading`), python-dotenv (ya está), FastAPI, JS sin frameworks. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-27-settings-file-design.md`

## Global Constraints

- Archivos: `DATA_DIR/settings.json`, `DATA_DIR/settings_default.json`, `DATA_DIR/settings.schema.json`, siempre por `notes.safe_path`, con la ruta calculada en cada uso desde `notes.DATA_DIR` (los tests la cambian).
- En el `.env` quedan solo: los `secret=True` (propios y de plugins), `ALLOWED_USER_ID`, `DATA_DIR`. Atributo nuevo `Setting.bootstrap=True` para `ALLOWED_USER_ID` y `DATA_DIR`.
- Precedencia de un valor: variable del proceso (systemd/terminal) > `settings.json` > valor de fábrica.
- Ningún valor secreto en `settings*.json`, en el schema, en un aviso ni en un log. Un secreto escrito en `settings.json` se ignora con el aviso «<clave>: las claves van en la pantalla de Configuración, no en este archivo» (sin el valor).
- Un ajuste `loosens=True` nunca se aplica en caliente si el cambio lo afloja (siempre pide reinicio); si lo endurece, se aplica al instante (ver Tarea 4, `tightens`). *(Decisión del plan: así apagar el navegador desde la pantalla sigue siendo instantáneo, como hoy.)*
- `settings.json` roto en pleno uso: se siguen usando los últimos valores buenos y se avisa; al arrancar con el archivo roto, valores de fábrica y aviso. Nunca se pisa. *(Decisión del plan.)*
- La IA no tiene herramienta para estos archivos; el comando nuevo no lleva `links`; `library.resolve` y el listado no los muestran.
- Mensajes en español, nombres de código en inglés. Sin `innerHTML` en la web: todo con `textContent`.
- Después de cada tarea: `uv run pytest` en verde y commit (sin firma de Claude). Al final, `systemctl --user restart epsilon` y confirmar que quedó activo.

## Review Focus

- La persona guarda en el editor mientras la pantalla guarda otro ajuste: `store.write` relee el archivo bajo el candado y edita el texto actual, sin perder lo que escribió el editor (test en Tarea 3).
- El editor guarda a medias (Epsilon lee un archivo truncado o vacío): se mantienen los últimos valores buenos, no los de fábrica (test en Tarea 3).
- Un número escrito como texto (`"6"`) o un sí/no escrito como `"si"`: se acepta si el validador del ajuste lo acepta, y la pantalla lo muestra igual que si fuera `6`/`true` (test en Tarea 2).
- Clave repetida en `settings.json`: gana la última (como `json`) y se avisa; `set_value` cambia la última y borra las anteriores (test en Tarea 1).
- Archivo con BOM o con saltos `\r\n` (editor de Windows): se lee igual y la edición conserva los `\r\n` (test en Tarea 1).

---

### Task 1: Lector y editor de JSONC

**Files:**
- Create: `src/epsilon/settings/jsonc.py`
- Test: `tests/settings/test_jsonc.py`

**Interfaces:**
- Produces: `loads(text: str) -> dict` (lanza `JsoncError(message: str, line: int)`, subclase de `ValueError`); `duplicates(text: str) -> list[str]`; `set_value(text: str, name: str, value: object) -> str`; `remove(text: str, name: str) -> str`. Solo objetos de primer nivel (las claves con puntos son planas, como VS Code).

- [ ] **Step 1: Tests**

```python
def test_reads_comments_and_trailing_commas():
    text = '// arriba\n{\n  /* bloque */ "a.b": 1, // fin\n  "c": "x // no es comentario",\n}\n'
    assert jsonc.loads(text) == {"a.b": 1, "c": "x // no es comentario"}

def test_bom_and_crlf():
    assert jsonc.loads('﻿{\r\n "a": true\r\n}\r\n') == {"a": True}

def test_broken_says_the_line():
    with pytest.raises(jsonc.JsoncError) as e:
        jsonc.loads('{\n "a": 1\n "b": 2\n}')
    assert e.value.line == 3

def test_top_level_must_be_object():
    with pytest.raises(jsonc.JsoncError):
        jsonc.loads("[1]")

def test_set_value_keeps_everything_else():
    text = '{\n  // mío\n  "a": 1, // ojo\n  "b": "x"\n}\n'
    out = jsonc.set_value(text, "a", 7)
    assert out == '{\n  // mío\n  "a": 7, // ojo\n  "b": "x"\n}\n'

def test_set_value_adds_before_closing_brace():
    out = jsonc.set_value('{\n  "a": 1 // c\n}\n', "z", [1, 2])
    assert jsonc.loads(out) == {"a": 1, "z": [1, 2]}
    assert "// c" in out

def test_set_value_on_empty_object():
    assert jsonc.loads(jsonc.set_value("{}", "a", "x")) == {"a": "x"}

def test_remove_line_and_comma():
    out = jsonc.remove('{\n  "a": 1,\n  "b": 2\n}\n', "b")
    assert jsonc.loads(out) == {"a": 1}

def test_duplicates_last_wins_and_set_value_cleans():
    text = '{\n "a": 1,\n "a": 2\n}'
    assert jsonc.loads(text) == {"a": 2}
    assert jsonc.duplicates(text) == ["a"]
    out = jsonc.set_value(text, "a", 3)
    assert jsonc.loads(out) == {"a": 3} and jsonc.duplicates(out) == []

def test_crlf_is_kept_on_edit():
    assert "\r\n" in jsonc.set_value('{\r\n "a": 1\r\n}\r\n', "b", 2)
```

- [ ] **Step 2: `uv run pytest tests/settings/test_jsonc.py -v`** → FAIL (módulo no existe).
- [ ] **Step 3: Implementar.** Un escáner de un pase que recorre el texto respetando strings JSON (con escapes) y devuelve, para cada clave de primer nivel, su rango de texto (desde la comilla de la clave hasta el fin del valor) y el de la coma que la sigue. `loads` arma el texto sin comentarios ni comas finales (reemplazando comentarios por espacios del mismo largo, conservando `\n`, así `json` informa la línea correcta) y llama a `json.loads`. `set_value` reemplaza solo el rango del valor (`json.dumps(value, ensure_ascii=False)`); si falta, inserta una línea con la sangría de la última clave (o dos espacios) antes de la `}` final, agregando la coma a la clave anterior si hace falta, con el fin de línea que usa el archivo.
- [ ] **Step 4: Tests en verde.**
- [ ] **Step 5: Commit** `feat: JSONC reader and minimal editor for settings.json`

---

### Task 2: Registro ampliado y números del código

**Files:**
- Modify: `src/epsilon/settings/config.py` (dataclass `Setting`, `SETTINGS`)
- Create: `src/epsilon/settings/tunables.py`
- Modify: `src/epsilon/plugins/channels.py` (`settings_for`: nombre JSON)
- Test: `tests/settings/test_registry.py`

**Interfaces:**
- Produces: campos nuevos de `Setting`: `name: str` (clave JSON; vacío para secretos y bootstrap), `kind: str` (`"text" | "int" | "float" | "bool" | "list"`), `minimum`/`maximum: float | None`, `choices: tuple[str, ...]`, `section: str`, `help: str` (una línea para el comentario y el schema), `loosens: bool`, `safer: str` (`"lower"` para números, el valor seguro `"si"`/`"no"` para bool, `"more"`/`"fewer"` para listas), `bootstrap: bool`. Funciones: `config.to_json(s: Setting, text: str) -> object` y `config.from_json(s: Setting, raw: object) -> str` (valida con `s.validate`; `ValueError` en español). `tunables.TUNABLES: tuple[Setting, ...]`; `config.all_settings()` las incluye.

Nombres JSON de los ajustes existentes (sección = primer tramo):

| Clave interna | `name` | kind | loosens |
|---|---|---|---|
| GEMINI_MODEL | `ai.gemini_model` | text | |
| AI_MODEL, AI_MODEL_CHAT/ASK/SUMMARY/CSV | `ai.model`, `ai.model_chat`, `ai.model_ask`, `ai.model_summary`, `ai.model_csv` | text | |
| AI_THINKING_EFFORT | `ai.thinking_effort` (choices) | text | |
| OLLAMA_URL, OLLAMA_NUM_CTX (nuevo, 512–131072) | `ai.ollama_url`, `ai.ollama_num_ctx` | text, int | |
| USER_NAME, AGENT_NAME | `general.user_name`, `general.agent_name` | text | |
| WEB_PORT | `web.port` | int | |
| USD_ARS | `usage.usd_ars` | float | |
| WEATHER_CITY | `weather.city` | text | |
| CHAT_LIMIT_USD | `chat.limit_usd` | float | sí, `lower` |
| CHAT_MAX_FILES, CHAT_MAX_MB | `chat.max_files`, `chat.max_mb` | int | sí, `lower` |
| CHAT_MAX_TURNS, CHAT_MAX_TURNS_TELEGRAM | `chat.max_turns`, `chat.max_turns_telegram` | int | sí, `lower` |
| WHISPER_LANGUAGE, WHISPER_IDLE_MINUTES, WHISPER_DEVICE | `voice.language`, `voice.idle_minutes`, `voice.device` | text, float, text | |
| ASSISTANT_NAME | `voice.wake_names` | text | |
| VOICE_ENABLED, VOICE_CONFIRM | `voice.enabled`, `voice.confirm` | bool | |
| TTS_ENABLED, TTS_ENGINE, TTS_VOICE, TTS_MODE, TTS_MAX_CHARS | `tts.enabled`, `tts.engine`, `tts.voice`, `tts.mode`, `tts.max_chars` | bool, text, text, text, int | |
| TTS_LANG, TTS_SPEED, TTS_LENGTH_SCALE, TTS_VOICES_DIR, KOKORO_MODELS_DIR (nuevos; validar como lo hacen hoy sus lectores) | `tts.lang`, `tts.speed`, `tts.length_scale`, `tts.voices_dir`, `tts.kokoro_models_dir` | text, float, float, text, text | |
| TELEGRAM_ENABLED | `channels.telegram_enabled` | bool | |
| BROWSER_ENABLED | `browser.enabled` | bool | sí, safer `"no"` |
| BROWSER_GUARD | `browser.guard` | bool | sí, safer `"si"` |
| BROWSER_TRUSTED_SITES | `browser.trusted_sites` | list | sí, `fewer` |
| BROWSER_BLOCKED_SITES | `browser.blocked_sites` | list | sí, `more` |
| ROUTINES_ENABLED | `routines.enabled` | bool | sí, safer `"no"` |

Números del código (`tunables.py`; el `default` es el valor que hoy tiene la constante; `live=True` salvo que se diga):

| Clave interna | `name` | default | rango | loosens | Constante que reemplaza |
|---|---|---|---|---|---|
| CHAT_MAX_TOOL_STEPS | `chat.max_tool_steps` | 6 | 1–50 | sí | `chat.MAX_TOOL_STEPS` |
| CHAT_MAX_BROWSER_STEPS | `chat.max_browser_steps` | 16 | 1–100 | sí | `chat.MAX_BROWSER_STEPS` |
| CHAT_MAX_OUTPUT_TOKENS | `chat.max_output_tokens` | 1500 | 100–32000 | sí | `chat.MAX_TOKENS` |
| CHAT_MAX_CALLS_PER_STEP | `chat.max_calls_per_step` | 8 | 1–32 | sí | `core.MAX_CALLS_PER_STEP` |
| TOOLS_MAX_RESULT_CHARS | `tools.max_result_chars` | 4000 | 500–50000 | sí | `core.MAX_RESULT_CHARS` y `connectors.manager.MAX_RESULT_CHARS` |
| ASK_MAX_QUESTION_CHARS | `ask.max_question_chars` | 1000 | 100–10000 | sí | `ask.MAX_QUESTION` |
| BROWSER_MAX_ACTIONS | `browser.max_actions` | 40 | 1–200 | sí | `browser_guard.MAX_BROWSER_ACTIONS` |
| BROWSER_MAX_NEW_TABS | `browser.max_new_tabs` | 5 | 1–20 | sí | `browser_guard.MAX_NEW_TABS` |
| BROWSER_MAX_SEARCHES | `browser.max_searches` | 3 | 1–10 | sí | `browser_guard.MAX_SEARCHES` |
| BROWSER_MAX_READ_CHARS | `browser.max_read_chars` | 3000 | 500–20000 | sí | `navegador.MAX_TEXT` |
| BROWSER_MAX_READ_ELEMENTS | `browser.max_read_elements` | 40 | 10–200 | sí | `navegador.MAX_ELEMENTS` |
| BROWSER_SCROLL_PIXELS | `browser.scroll_pixels` | 600 | 100–3000 | | `navegador.SCROLL_PIXELS` |
| FILES_MAX_CREATE_CHARS | `files.max_create_chars` | 100000 | 1000–1000000 | sí | `notes.MAX_CREATE_CHARS` |
| FILES_TABLE_MAX_ROWS | `files.table_max_rows` | 30 | 5–500 | sí | `tabular.MAX_ROWS` |
| SUMMARY_MAX_CHARS | `summary.max_chars` | 300000 | 10000–2000000 | sí | `summarize.MAX_TEXT` |
| SUMMARY_MAX_FILE_MB | `summary.max_file_mb` | 15 | 1–100 | sí | `summarize.MAX_FILE` (en bytes: ×1024²) |
| SKILLS_MAX_LISTED | `skills.max_listed` | 20 | 1–50 | sí | `skills.runtime.MAX_LISTED` |
| IMAGES_MAX_SIDE, IMAGES_MAX_SIDE_CSV | `images.max_side`, `images.max_side_csv` | 1024, 2048 | 256–4096 | | `images.MAX_SIDE`, `MAX_SIDE_CSV` |
| IMAGES_JPEG_QUALITY | `images.jpeg_quality` | 85 | 40–100 | | `images.JPEG_QUALITY` |
| VOICE_MAX_AUDIO_MB | `voice.max_audio_mb` | 25 | 1–200 | | `transcribe.MAX_BYTES` (×1024²) |
| VOICE_MAX_AUDIO_MINUTES | `voice.max_audio_minutes` | 10 | 1–120 | | `transcribe.MAX_SECONDS` (×60) |
| CONNECTORS_IDLE_CLOSE_SECONDS | `connectors.idle_close_seconds` | 600 | 60–86400 | | `manager.IDLE_CLOSE` |
| CONNECTORS_MAX_RUNNING | `connectors.max_running` | 3 | 1–10 | | `manager.MAX_RUNNING` |
| CONNECTORS_CALL_TIMEOUT_SECONDS | `connectors.call_timeout_seconds` | 30 | 5–600 | | `manager.CALL_TIMEOUT` |
| CONNECTORS_OPEN_TIMEOUT_SECONDS | `connectors.open_timeout_seconds` | 60 | 10–600 | | `manager.OPEN_TIMEOUT` |
| ROUTINES_MAX_RUN_SECONDS | `routines.max_run_seconds` | 300 | 30–3600 | | `runner.RUN_BUDGET` |
| ROUTINES_MAX_WAIT_SECONDS | `routines.max_wait_seconds` | 60 | 1–600 | | `steps.MAX_WAIT` |
| DOLLAR_CACHE_SECONDS, WEATHER_CACHE_SECONDS | `utilities.dollar_cache_seconds`, `utilities.weather_cache_seconds` | 600 | 60–86400 | | `dolar.CACHE_SECONDS`, `clima.CACHE_SECONDS` |
| TELEGRAM_PROGRESS_AFTER | `telegram.progress_after_seconds` | 3.0 (float) | 0.5–60 | | `telegram.PROGRESS_AFTER` |
| TELEGRAM_TYPING_EVERY | `telegram.typing_every_seconds` | 4.0 (float) | 1–5 | | `telegram.TYPING_EVERY` |
| WEB_MAX_UPLOAD_MB | `web.max_upload_mb` | 20 | 1–200 | | `web.MAX_UPLOAD` (×1024²) |

Los ajustes de plugins: `name = f"plugin.{group}.{key.lower()}"`, `kind="text"`, `section="plugin.<group>"`.

- [ ] **Step 1: Tests**

```python
def test_every_setting_has_a_unique_json_name_except_secrets_and_bootstrap():
    names = [s.name for s in config.all_settings() if not s.secret and not s.bootstrap]
    assert all(names) and len(names) == len(set(names))
    assert all(not s.name for s in config.all_settings() if s.secret or s.bootstrap)

def test_bootstrap_is_exactly_allowed_user_and_data_dir():
    assert {s.key for s in config.all_settings() if s.bootstrap} == {"ALLOWED_USER_ID", "DATA_DIR"}

def test_numbers_have_ranges_and_default_inside():
    for s in config.all_settings():
        if s.kind in ("int", "float"):
            assert s.minimum is not None and s.maximum is not None
            assert s.minimum <= float(s.default or s.minimum) <= s.maximum

def test_loosening_settings_say_which_way_is_safer():
    for s in config.all_settings():
        if s.loosens:
            assert s.safer, s.key

def test_every_setting_has_help():
    assert all(s.help for s in config.all_settings() if s.name)

def test_defenses_are_not_tunable():
    names = {s.name for s in config.all_settings()}
    assert not names & {"volume.max", "volume.max_step"}

@pytest.mark.parametrize("raw,text", [(6, "6"), ("6", "6")])
def test_number_from_json(raw, text):
    assert config.from_json(config.BY_KEY["CHAT_MAX_TOOL_STEPS"], raw) == text

@pytest.mark.parametrize("raw,text", [(True, "si"), (False, "no"), ("si", "si")])
def test_bool_from_json(raw, text):
    assert config.from_json(config.BY_KEY["VOICE_ENABLED"], raw) == text

def test_list_from_json_and_back():
    s = config.BY_KEY["BROWSER_BLOCKED_SITES"]
    assert config.from_json(s, ["a.com", "B.com"]) == "a.com,b.com"
    assert config.to_json(s, "a.com,b.com") == ["a.com", "b.com"]

def test_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        config.from_json(config.BY_KEY["CHAT_MAX_TOOL_STEPS"], 999)
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implementar** los campos (con valores por defecto, así los plugins y los tests viejos siguen armando `Setting`), los nombres de la tabla, `tunables.py` (cada entrada con `validate=config._whole(min, max, ...)` o un `_decimal(min, max, ...)` nuevo) y `to_json`/`from_json`. `BY_KEY` pasa a incluir los tunables.
- [ ] **Step 4: Verde.** `uv run pytest`.
- [ ] **Step 5: Commit** `feat: settings registry knows JSON names, types, ranges and tunables`

---

### Task 3: `settings/store.py`: leer, aplicar, escribir

**Files:**
- Create: `src/epsilon/settings/store.py`
- Modify: `tests/conftest.py` (fixture autouse: si `notes.DATA_DIR` es el real, `store` usa `tmp_path`; y `store.reset()` antes de cada test)
- Test: `tests/settings/test_store.py`

**Interfaces:**
- Consumes: `jsonc.*` (Tarea 1); `config.all_settings`, `from_json`, `to_json`, `ConfigError` (Tarea 2).
- Produces:
  - `settings_path() -> Path`, `default_path() -> Path`, `schema_path() -> Path`.
  - `@dataclass Issue: name: str; message: str` (nunca lleva un valor secreto).
  - `load_initial() -> None`: lee `settings.json` y fija los valores aplicados (todos, también los de reinicio).
  - `refresh() -> bool`: si cambió el `mtime` (o el tamaño), relee; aplica los `live` y, de los `loosens`, solo los que `tightens`. Devuelve si cambió algo.
  - `applied(key: str) -> str | None`: el valor aplicado desde el archivo (`None` = no está en el archivo).
  - `saved(key: str) -> str | None`: lo que dice hoy el archivo (aplicado o no).
  - `issues() -> list[Issue]`; `broken() -> str | None` (motivo con número de línea).
  - `write(changes: dict[str, str], clear: list[str]) -> None`: claves internas, valores ya validados como texto; bajo un `threading.Lock`, relee el texto actual del disco, aplica `jsonc.set_value`/`remove` con `to_json`, escribe por archivo temporal + `os.replace`, y llama a `refresh()`. Si `broken()`, lanza `ConfigError({"settings.json": "El archivo tiene un error (línea N): arreglalo a mano antes de guardar desde acá."})` sin tocarlo.
  - `tightens(s: Setting, old: str, new: str) -> bool` (pura).
  - `reset() -> None` (solo tests).

- [ ] **Step 1: Tests** (con `notes.DATA_DIR` apuntado a `tmp_path`)

```python
def test_missing_file_means_defaults():
    store.load_initial()
    assert store.applied("AI_MODEL") is None and store.issues() == []

def test_values_are_read_and_typed(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_steps": 9, "voice.enabled": false}')
    store.load_initial()
    assert store.applied("CHAT_MAX_TOOL_STEPS") == "9" and store.applied("VOICE_ENABLED") == "no"

def test_schema_key_is_ignored(tmp_path):
    write_file(tmp_path, '{"$schema": "./settings.schema.json"}')
    store.load_initial()
    assert store.issues() == []

def test_unknown_key_suggests(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_step": 3}')
    store.load_initial()
    assert "chat.max_tool_steps" in store.issues()[0].message

def test_bad_value_falls_back_and_warns(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_steps": 999}')
    store.load_initial()
    assert store.applied("CHAT_MAX_TOOL_STEPS") is None
    assert store.issues()[0].name == "chat.max_tool_steps"

def test_secret_in_file_is_ignored_without_echo(tmp_path):
    write_file(tmp_path, '{"GEMINI_API_KEY": "AIzaSECRETSECRETSECRETSECRET"}')
    store.load_initial()
    assert "SECRET" not in repr(store.issues()) and store.applied("GEMINI_API_KEY") is None

def test_broken_at_start_uses_defaults(tmp_path):
    write_file(tmp_path, '{"ai.model": ')
    store.load_initial()
    assert store.broken() and store.applied("AI_MODEL") is None

def test_broken_while_running_keeps_last_good(tmp_path):
    write_file(tmp_path, '{"general.user_name": "Ana"}'); store.load_initial()
    write_file(tmp_path, '', bump=True); store.refresh()
    assert store.applied("USER_NAME") == "Ana" and store.broken()

def test_live_change_applies_and_restart_change_waits(tmp_path):
    write_file(tmp_path, '{}'); store.load_initial()
    write_file(tmp_path, '{"general.user_name": "Ana", "web.port": 9000}', bump=True)
    assert store.refresh()
    assert store.applied("USER_NAME") == "Ana"
    assert store.applied("WEB_PORT") is None and store.saved("WEB_PORT") == "9000"

def test_loosening_waits_tightening_applies(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_steps": 6}'); store.load_initial()
    write_file(tmp_path, '{"chat.max_tool_steps": 30}', bump=True); store.refresh()
    assert store.applied("CHAT_MAX_TOOL_STEPS") == "6"
    write_file(tmp_path, '{"chat.max_tool_steps": 3}', bump=True); store.refresh()
    assert store.applied("CHAT_MAX_TOOL_STEPS") == "3"

@pytest.mark.parametrize("key,old,new,expected", [
    ("CHAT_MAX_TOOL_STEPS", "6", "3", True), ("CHAT_MAX_TOOL_STEPS", "6", "9", False),
    ("BROWSER_ENABLED", "si", "no", True), ("BROWSER_GUARD", "si", "no", False),
    ("BROWSER_BLOCKED_SITES", "a.com", "a.com,b.com", True), ("BROWSER_BLOCKED_SITES", "a.com,b.com", "a.com", False),
    ("BROWSER_TRUSTED_SITES", "a.com,b.com", "a.com", True), ("BROWSER_TRUSTED_SITES", "a.com", "c.com", False),
])
def test_tightens(key, old, new, expected):
    assert store.tightens(config.BY_KEY[key], old, new) is expected

def test_write_keeps_comments_and_concurrent_edit(tmp_path):
    write_file(tmp_path, '{\n  // mío\n  "ai.model": "gemini:x"\n}\n'); store.load_initial()
    write_file(tmp_path, '{\n  // mío\n  "ai.model": "gemini:x",\n  "weather.city": "Rosario"\n}\n', bump=True)  # lo guardó el editor
    store.write({"USER_NAME": "Ana"}, [])
    text = store.settings_path().read_text()
    assert "// mío" in text and "Rosario" in text and '"general.user_name": "Ana"' in text

def test_write_refuses_broken_file(tmp_path):
    write_file(tmp_path, '{"a": '); store.load_initial()
    with pytest.raises(config.ConfigError):
        store.write({"USER_NAME": "Ana"}, [])
    assert store.settings_path().read_text() == '{"a": '

def test_duplicate_key_warns(tmp_path):
    write_file(tmp_path, '{"general.user_name": "A", "general.user_name": "B"}'); store.load_initial()
    assert store.applied("USER_NAME") == "B" and any("repetido" in i.message for i in store.issues())
```

(`write_file(tmp_path, text, bump=False)` es un ayudante del archivo de test: escribe y, con `bump`, adelanta el `mtime` con `os.utime` para que `refresh` lo note sin dormir.)

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implementar.** Mensajes: clave desconocida → «No existe este ajuste.» + « ¿Quisiste decir «X»?» (`difflib.get_close_matches`, n=1, cutoff 0.8); repetida → «Está repetido: vale el último.»; archivo roto → «El archivo tiene un error en la línea N: se usan los últimos valores que funcionaban.» (o «…los valores de fábrica» al arrancar).
- [ ] **Step 4: Verde.**
- [ ] **Step 5: Commit** `feat: settings store reads, applies and edits settings.json`

---

### Task 4: `config.py` lee y escribe por el store; el `.env` se achica

**Files:**
- Modify: `src/epsilon/__init__.py` (`PROCESS_ENV = frozenset(os.environ)` antes de `load_dotenv`)
- Modify: `src/epsilon/settings/config.py` (`value`, `number`, `environ`, `_current`, `status`, `enabled`, `site_list`, `pending_restart`, `update`, `saved`, `set_plugin_settings`; al importar, saca de `os.environ` los ajustes con `name` que no estaban en `PROCESS_ENV`)
- Test: `tests/settings/test_config.py` (adaptar) y `tests/settings/test_env_file.py`

**Interfaces:**
- Consumes: `store.*` (Tarea 3).
- Produces:
  - `config.value(key: str) -> str`: `os.environ[key]` si está (el proceso gana) → `store.applied(key)` → `setting.default`.
  - `config.number(key: str) -> int | float` (según `kind`; un valor del entorno que no valida cae al de fábrica).
  - `config.environ() -> dict[str, str]`: `os.environ` + los valores efectivos de todos los ajustes con `name` (para `is_configured(env)` de los canales y plugins).
  - `config.from_process(key: str) -> bool` (para «viene del entorno»).
  - `status()` agrega por entrada: `"name"`, `"kind"`, `"from_env"`; para `pending_restart` de un no secreto, `"saved"` (lo nuevo) y `"value"` (lo de ahora), ya existentes.
  - `update(changes, clear, env_file=None)`: secretos (y bootstrap editables) al `.env` como hoy (con `os.environ[key]` si `live`); el resto a `store.write` (sin tocar `os.environ`). Todo o nada: valida todo antes de escribir cualquier cosa.

- [ ] **Step 1: Tests**

```python
def test_process_env_wins(monkeypatch, tmp_settings):
    tmp_settings('{"general.user_name": "Ana"}')
    monkeypatch.setenv("USER_NAME", "Beto")
    assert config.value("USER_NAME") == "Beto" and config.from_process("USER_NAME")

def test_file_then_default(tmp_settings):
    tmp_settings('{"general.user_name": "Ana"}')
    assert config.value("USER_NAME") == "Ana" and config.value("AGENT_NAME") == "Epsilon"

def test_number_typed(tmp_settings):
    tmp_settings('{"telegram.progress_after_seconds": 1.5}')
    assert config.number("TELEGRAM_PROGRESS_AFTER") == 1.5 and config.number("CHAT_MAX_TOOL_STEPS") == 6

def test_update_writes_non_secrets_to_settings_json_and_secrets_to_env(tmp_path, tmp_settings):
    tmp_settings("{}")
    env = tmp_path / "x.env"
    config.update({"USER_NAME": "Ana", "GEMINI_API_KEY": "a" * 30}, [], env_file=env)
    assert "USER_NAME" not in env.read_text() and "GEMINI_API_KEY" in env.read_text()
    assert "a" * 30 not in store.settings_path().read_text()
    assert config.value("USER_NAME") == "Ana" and "USER_NAME" not in os.environ

def test_update_all_or_nothing(tmp_settings):
    tmp_settings("{}")
    with pytest.raises(config.ConfigError):
        config.update({"USER_NAME": "Ana", "CHAT_MAX_TOOL_STEPS": "999"}, [])
    assert config.value("USER_NAME") == ""

def test_loosening_from_the_page_waits_for_restart(tmp_settings):
    tmp_settings("{}")
    config.update({"CHAT_MAX_TOOL_STEPS": "20"}, [])
    assert config.number("CHAT_MAX_TOOL_STEPS") == 6
    assert any(p["key"] == "CHAT_MAX_TOOL_STEPS" for p in config.pending_restart())

def test_env_file_only_loads_secrets_and_bootstrap(tmp_path):
    # subprocess: EPSILON_ENV_FILE con USER_NAME y GEMINI_API_KEY → en el hijo, USER_NAME no está en os.environ y GEMINI_API_KEY sí
    ...
```

(`tmp_settings(text)` es un fixture nuevo en `tests/conftest.py`: escribe `settings.json` en el `DATA_DIR` del test y llama a `store.load_initial()`. El último test sigue el patrón de `tests/settings/test_env_file.py`, que ya arranca un proceso hijo.)

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implementar.** El aviso de reinicio de un `loosens` dice el motivo fijo «Afloja una protección: se aplica al reiniciar, así ningún programa puede cambiarla sin que lo veas.»
- [ ] **Step 4: `uv run pytest`** (lo que rompa por leer `os.environ` se arregla en la Tarea 5; si algo del resto se rompe acá, arreglarlo acá).
- [ ] **Step 5: Commit** `feat: non-secret settings live in settings.json; .env keeps keys and bootstrap`

---

### Task 5: Todo el código lee con `config.value` / `config.number`

**Files:**
- Modify (lectores de `os.environ` de un ajuste registrado): `menu.py`, `core.py` (WEATHER_CITY, MAX_CALLS_PER_STEP, MAX_RESULT_CHARS), `main.py` (pasar `config.environ()` a `check_configured`, `enabled_channels`, `is_configured`), `channels/web.py` (USER_NAME, AGENT_NAME, USD_ARS, overrides, `read_port`, `MAX_UPLOAD`, `check_configured`), `channels/telegram.py` (TTS_MODE, `read_config(config.environ())`, PROGRESS_AFTER, TYPING_EVERY), `ai/attachments.py`, `ai/llm.py`, `ai/chat.py`, `ai/ask.py`, `ai/images.py`, `ai/summarize.py`, `ai/browser_guard.py`, `ai/providers/ollama.py`, `ai/providers/gemini.py` (AI_THINKING_EFFORT), `voice/kokoro_engine.py`, `voice/tts.py`, `voice/transcribe.py`, `utils/navegador.py`, `utils/dolar.py`, `utils/clima.py`, `data/notes.py` (MAX_CREATE_CHARS), `data/tabular.py`, `skills/runtime.py`, `connectors/manager.py`, `routines/runner.py`, `routines/steps.py`
- Modify: los tests que hacen `monkeypatch.setattr(<módulo>, "<CONSTANTE>")` de una constante que ahora es ajuste: pasan a `monkeypatch.setenv("<CLAVE>", "<valor>")`.
- Test: `tests/test_structure.py`

**Interfaces:**
- Consumes: `config.value`, `config.number`, `config.environ` (Tarea 4).
- Produces: las constantes de la tabla de la Tarea 2 desaparecen de sus módulos (la fuente es el registro); los valores se leen **en el momento de usarlos**, nunca al importar.

- [ ] **Step 1: Tests**

```python
def test_no_module_reads_a_registered_setting_from_os_environ():
    keys = {s.key for s in config.all_settings() if s.name}
    offenders = []
    for path in Path("src/epsilon").rglob("*.py"):
        if path.parts[-2] == "settings":
            continue
        text = path.read_text()
        for key in keys:
            if re.search(rf'environ(\.get)?\(?\[?\s*f?"{key}"', text) or f'getenv("{key}"' in text:
                offenders.append(f"{path}: {key}")
    assert offenders == []

def test_tunable_constants_are_gone():
    from epsilon.ai import chat, browser_guard
    assert not hasattr(chat, "MAX_TOOL_STEPS") and not hasattr(browser_guard, "MAX_SEARCHES")
```

Y en un test existente de cada grupo, que el ajuste se respeta: `monkeypatch.setenv("CHAT_MAX_TOOL_STEPS", "2")` corta el bucle a 2 pasos (reemplaza el actual `setattr(core.chat, "MAX_TOOL_STEPS")`); `monkeypatch.setenv("BROWSER_MAX_SEARCHES", "1")` bloquea la segunda búsqueda.

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Reemplazar** lector por lector. El test que vigilaba que `manager.MAX_RESULT_CHARS == core.MAX_RESULT_CHARS` pasa a vigilar que los dos leen `TOOLS_MAX_RESULT_CHARS`.
- [ ] **Step 4: `uv run pytest`** en verde.
- [ ] **Step 5: Commit** `refactor: read settings through config instead of os.environ and module constants`

---

### Task 6: Generar `settings_default.json`, el schema y migrar al arrancar

**Files:**
- Create: `src/epsilon/settings/generate.py`
- Modify: `src/epsilon/main.py` (`main()`: `setup_settings()` antes que todo lo demás; si falla, se imprime redactado y sigue con valores de fábrica)
- Modify: `src/epsilon/settings/config.py` (`set_plugin_settings` llama a `store.migrate_keys` para los nuevos y regenera)
- Modify: `src/epsilon/.env.example` (solo claves, `ALLOWED_USER_ID`, `DATA_DIR`, y un comentario que manda a `settings.json`)
- Test: `tests/settings/test_generate.py`

**Interfaces:**
- Consumes: registro (Tarea 2), `store`, `jsonc` (Tareas 1 y 3).
- Produces:
  - `default_text() -> str`: JSONC. Primera línea: `// Lo genera Epsilon: no lo edites. Tus cambios van en settings.json (se vuelve a escribir al arrancar).` Por sección, un comentario `// --- <Sección> ---` (títulos en español: General, IA, Chat con IA, Pregunta suelta, Herramientas, Archivos, Resúmenes, Imágenes, Voz, Respuestas con audio, Telegram, Canales, Navegador, Rutinas, Conectores, Skills, Utilidades, Uso, Clima, Web, y «Plugin <nombre>»), y por ajuste `// <help> (<rango o valores>)[. Pide reinicio: <reason o el motivo de loosens>]` arriba de `"name": <default en JSON>`.
  - `schema() -> dict`: draft-07, `type: object`, `additionalProperties: false`, `allowComments: true`, `allowTrailingCommas: true`, `properties`: `$schema` (`type: string`) y cada ajuste con `type` (`string`/`integer`/`number`/`boolean`/`array` de `string`), `description` (= el comentario), `default`, `minimum`/`maximum`, `enum` si hay `choices`.
  - `write_generated() -> None`: escribe `settings_default.json` y `settings.schema.json` por archivo temporal, solo si cambió el contenido.
  - `store.migrate(env_values: dict[str, str]) -> list[str]`: si `settings.json` no existe, lo crea con el encabezado `// Tus ajustes. Lo que no está acá usa el valor de fábrica (mirá settings_default.json).`, `"$schema": "./settings.schema.json"` y los ajustes con `name` que estén en `env_values` (validados con `from_json(to_json(...))`; lo que no valida se saltea y queda como `Issue`). Devuelve las claves migradas. Si existe, no hace nada.
  - `store.migrate_keys(env_values, keys) -> list[str]`: agrega al `settings.json` existente las de `keys` que falten (sin pisar ninguna).
  - `store.leftover_env() -> list[str]`: claves con `name` que siguen en el `.env` (solo nombres): la pantalla las lista para que la persona las borre.
  - `main.setup_settings() -> None`: `migrate(dotenv_values(ENV_FILE))` → `load_initial()` → `write_generated()` → `store.start_watcher(interval=2.0)` (hilo daemon que llama a `refresh()`; un error en una pasada se registra redactado y el hilo sigue).

- [ ] **Step 1: Tests**

```python
def test_default_file_parses_and_has_every_setting():
    data = jsonc.loads(generate.default_text())
    assert set(data) == {s.name for s in config.all_settings() if s.name}

def test_default_file_has_no_secret_names_or_values(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 30)
    text = generate.default_text()
    assert "GEMINI_API_KEY" not in text and "AIza" not in text

def test_each_setting_has_its_comment_above():
    lines = generate.default_text().splitlines()
    i = next(n for n, l in enumerate(lines) if '"chat.max_tool_steps"' in l)
    assert lines[i - 1].strip().startswith("//") and "1" in lines[i - 1] and "50" in lines[i - 1]

def test_loosening_comment_says_restart():
    text = generate.default_text()
    line = next(l for l in text.splitlines() if "browser.guard" in l)
    above = text.splitlines()[text.splitlines().index(line) - 1]
    assert "reinicio" in above.lower()

def test_schema_matches_registry():
    schema = generate.schema()
    assert schema["additionalProperties"] is False
    steps = schema["properties"]["chat.max_tool_steps"]
    assert steps == {**steps, "type": "integer", "minimum": 1, "maximum": 50, "default": 6}
    assert schema["properties"]["ai.thinking_effort"]["enum"] == ["desactivado", "bajo", "medio", "alto"]

def test_migrate_creates_file_with_schema_and_values(tmp_data):
    moved = store.migrate({"USER_NAME": "Ana", "GEMINI_API_KEY": "a" * 30, "ALLOWED_USER_ID": "1"})
    text = store.settings_path().read_text()
    assert moved == ["USER_NAME"] and '"$schema": "./settings.schema.json"' in text
    assert "a" * 30 not in text and "ALLOWED_USER_ID" not in text

def test_migrate_never_touches_an_existing_file(tmp_data):
    store.settings_path().write_text("// mío\n{}")
    assert store.migrate({"USER_NAME": "Ana"}) == []
    assert store.settings_path().read_text() == "// mío\n{}"

def test_write_generated_is_idempotent(tmp_data):
    generate.write_generated(); first = store.default_path().stat().st_mtime_ns
    generate.write_generated()
    assert store.default_path().stat().st_mtime_ns == first
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Verde.**
- [ ] **Step 5: Commit** `feat: generate settings_default.json and its schema, migrate the .env once`

---

### Task 7: Abrir los archivos, comando `/ajustes` y la pantalla

**Files:**
- Create: `src/epsilon/settings/editor.py`
- Modify: `src/epsilon/tools.py` (entrada `/ajustes`), `src/epsilon/core.py` (`COMMANDS["ajustes"]`)
- Modify: `src/epsilon/data/library.py` (`SYSTEM_FILES = {"settings.json", "settings_default.json", "settings.schema.json"}` ocultos en la raíz, y `resolve` los rechaza con el mismo «Ese archivo no existe en la biblioteca»)
- Modify: `src/epsilon/channels/web.py` (`config_state()` suma `settings_file`; `POST /api/config/open`)
- Modify: `src/epsilon/channels/web_static/config.html`, `js/config.js`, `css/style.css`
- Test: `tests/settings/test_editor.py`, `tests/test_core_tools.py` (o el archivo de tests de comandos que corresponda), `tests/channels/web/test_config_api.py`, `tests/channels/web/test_config_page.py`, `tests/channels/web/test_library.py`

**Interfaces:**
- Consumes: `store.settings_path/default_path/issues/broken/leftover_env` (Tareas 3 y 6).
- Produces:
  - `editor.can_open() -> bool` (hay `DISPLAY` o `WAYLAND_DISPLAY`, y `xdg-open` o `gtk-launch`).
  - `editor.open_file(which: str) -> None` (`which` en `{"settings", "default"}`; otro valor → `ValueError`): asegura que `settings.json` exista (`store.migrate({})`), pide `xdg-mime query default text/plain` y, si da un `.desktop`, lanza `gtk-launch <id> <ruta>`; si no, `xdg-open <ruta>`. `OSError("Este equipo no tiene un editor de texto para abrir")` si `not can_open()`. `_run(args: list[str]) -> str` y `_launch(args: list[str]) -> None` son los ÚNICOS puntos de procesos (sin shell, `start_new_session=True`, `_run` con `timeout=5`), bloqueados en `tests/conftest.py`.
  - `GET /api/config` → `"settings_file": {"issues": [{"name", "message"}], "broken": str | None, "leftover_env": [str], "can_open": bool}`.
  - `POST /api/config/open` con `{"which": "settings" | "default"}` → `{"ok": true}`; 409 con el motivo si no se puede; 422 si `which` no es válido.
  - Comando `/ajustes [defecto]`: `Tool(label="/ajustes", name="ajustes", usage="/ajustes [defecto]", help="abrir settings.json en tu editor (con «defecto», los valores de fábrica)", section=<la de configuración que use /config hoy>, done="")` sin `ai`, sin `links`, sin `menu`. En Telegram contesta «Esto abre un archivo en la pantalla de la PC: usalo desde la web.» y no lanza nada. En la web contesta «Abrí settings.json en tu editor.» (o el error de `OSError`).

- [ ] **Step 1: Tests**

```python
def test_open_prefers_the_text_editor(monkeypatch, tmp_data):
    calls = []
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(editor, "_run", lambda args: "org.gnome.TextEditor.desktop\n")
    monkeypatch.setattr(editor, "_launch", calls.append)
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/usr/bin/" + name)
    editor.open_file("settings")
    assert calls == [["gtk-launch", "org.gnome.TextEditor.desktop", str(store.settings_path())]]
    assert store.settings_path().exists()

def test_open_falls_back_to_xdg_open(...): ...  # _run devuelve "" → ["xdg-open", ruta]

def test_open_without_screen_raises(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False); monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(OSError):
        editor.open_file("settings")

def test_open_rejects_other_names():
    with pytest.raises(ValueError):
        editor.open_file("../../etc/passwd")

def test_ajustes_is_not_for_the_ai_nor_links():
    tool = next(t for t in tools.TOOLS if t.name == "ajustes")
    assert not tool.ai and not tool.links

def test_ajustes_in_telegram_does_not_launch(...): ...  # Session.web=False → texto fijo, _launch no se llama

def test_settings_files_hidden_from_library(tmp_data):
    for name in ("settings.json", "settings_default.json", "settings.schema.json"):
        (notes.DATA_DIR / name).write_text("{}")
        with pytest.raises(ValueError):
            library.resolve(name)
    assert not any(i["path"].startswith("settings") for i in library_listing())

def test_config_api_reports_issues_without_secret(client, tmp_settings):
    tmp_settings('{"GEMINI_API_KEY": "AIzaSECRETSECRETSECRETSECRET", "chat.max_tool_step": 3}')
    data = client.get("/api/config").json()["settings_file"]
    assert len(data["issues"]) == 2 and "SECRET" not in json.dumps(data)

def test_open_endpoint(client, monkeypatch):
    monkeypatch.setattr(editor, "open_file", lambda which: None)
    assert client.post("/api/config/open", json={"which": "default"}).json() == {"ok": True}
    assert client.post("/api/config/open", json={"which": "x"}).status_code == 422

def test_config_page_has_the_buttons_and_uses_textcontent():
    html = read_static("config.html"); js = read_static("js/config.js")
    assert 'id="open-settings"' in html and 'id="open-defaults"' in html and 'id="settings-issues"' in html
    assert "innerHTML" not in js
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implementar.** Pantalla: arriba del índice de secciones, una tarjeta «Archivo de ajustes» con «Abrir settings.json» y «Ver valores por defecto» (deshabilitados con el motivo si `can_open` es falso), la lista de avisos (`#settings-issues`, cada uno «<name>: <message>»), el aviso de archivo roto (y el botón Guardar deshabilitado mientras `broken`), y, si `leftover_env` no está vacía, «Estas líneas del .env ya no se usan (ahora viven en settings.json); podés borrarlas: …». En el aviso de reinicio que ya existe, los `loosens` muestran «ahora X → al reiniciar Y» (no secretos). Los campos con `from_env` muestran «viene del entorno del servicio» y no se editan.
- [ ] **Step 4: `uv run pytest`** en verde.
- [ ] **Step 5: Commit** `feat: open settings.json from Configuración and /ajustes`

---

### Task 8: Documentación y servicio

**Files:**
- Modify: `docs/plan-config.md` (sección nueva: los tres archivos, precedencia, `loosens` y `tightens`, migración, el vigilante), `docs/plan-plugins.md` (`is_configured(env)` recibe `config.environ()`; un plugin que lea `os.environ` directo solo ve sus claves), `CLAUDE.md` (línea de `settings/` en Estructura con `store.py jsonc.py tunables.py generate.py editor.py`, la viñeta de `settings/config.py`, y «Más proveedores … Y un archivo de configuración propio» sale de Pendiente).
- Test: ninguno nuevo; `uv run pytest`.

- [ ] **Step 1: Escribir los cambios de documentación.**
- [ ] **Step 2: `uv run pytest`** en verde.
- [ ] **Step 3: Commit** `docs: settings.json in plan-config, plan-plugins and CLAUDE.md`
- [ ] **Step 4: `systemctl --user restart epsilon && systemctl --user is-active epsilon`** → `active`. Revisar `journalctl --user -u epsilon -n 50`: sin errores, migró los ajustes, y `DATA_DIR/settings.json`, `settings_default.json`, `settings.schema.json` existen.
- [ ] **Step 5: Prueba a mano en la web:** «Abrir settings.json» abre el editor; cambiar `general.agent_name` y guardar lo aplica en ≤ 2 s; poner `chat.max_tool_steps: 30` aparece en el aviso de reinicio sin aplicarse; una coma de más no rompe nada.
