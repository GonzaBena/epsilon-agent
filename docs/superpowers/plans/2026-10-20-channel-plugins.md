# Canales como plugins: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un canal de terceros sea una carpeta en `DATA_DIR/plugins/<nombre>/` que aparece en la página Canales (apagado por defecto), se prende desde ahí y usa los mismos ajustes, redacción de claves y estados que Telegram.

**Architecture:** Un módulo nuevo `plugins/channels.py` lee y valida manifiestos `plugin.toml` sin ejecutar código y sabe importar el `channel.py` de un plugin por ruta. `main.py` mantiene `CHANNELS` al día con los plugins descubiertos (`refresh_plugins`) e importa el código recién al prender. `settings/config.py` suma los ajustes de los plugins a los propios. La web y `canales.js` toman nombre, texto y aviso de la API.

**Tech Stack:** Python 3.12 (`tomllib`, `importlib.util`), FastAPI, pytest, JS plano sin dependencias.

**Spec:** `docs/superpowers/specs/2026-10-20-channel-plugins-design.md`

## Global Constraints

- Nombres de código en inglés; mensajes al usuario en español (las pruebas buscan el texto en español).
- Toda ruta pasa por `notes.safe_path`; nunca armar rutas con texto del plugin a mano.
- Las claves nunca salen del servidor: ni a la web (solo «cargada» y largo), ni a logs, ni a errores (`config.redact`). Todo valor se valida sin saltos de línea antes de escribir el `.env`; solo `config.py` escribe el `.env`.
- Listar un plugin NO ejecuta su código. El código se importa recién en `main.load` al prenderlo.
- Un plugin que falla (al importar o al correr) deja su canal «detenido» con el motivo; la web y los demás canales siguen.
- Nombre de plugin: `^[a-z][a-z0-9_]{1,19}$` (fullmatch, ASCII), igual al de la carpeta, distinto de `web`, `telegram` y de otros plugins. Claves de ajustes: prefijo `<NOMBRE>_` en mayúsculas, sin chocar con ajustes de Epsilon, `<NOMBRE>_ENABLED` reservada. `<NOMBRE>_ENABLED` vale «no» por defecto.
- Los tests no llaman a Gemini ni cargan Whisper (`conftest.py` lo bloquea). Nada de `innerHTML`: lo del servidor se muestra con `textContent`.
- Después de cada tarea: `uv run pytest -q` en verde y commit (nunca push). Mensajes de commit terminan con las líneas `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` y `Claude-Session: https://claude.ai/code/session_01SkNTZverCgJs7V6vcDL6Rv`.
- Decisiones mías de valores que el spec no fija: `label` ≤ 40 caracteres, `about` ≤ 200, hasta 10 ajustes por plugin, `plugin.toml` ≤ 16 KB.

## Review Focus

Cada línea tiene su test en la tarea que se indica.

1. `plugin.toml` mal formado, no UTF-8 o gigante → plugin «inválido» con motivo, sin romper la lista (Tarea 1).
2. Un `channel.py` que falla al importarse (o hace `SystemExit`) → canal «detenido» con motivo redactado; la web y Telegram siguen (Tareas 2 y 4).
3. Choques: nombre `web`/`telegram`, nombre distinto al de la carpeta, clave de un ajuste de Epsilon (`TELEGRAM_TOKEN`), clave repetida entre plugins → inválido (Tarea 1).
4. `entry` que escapa de la carpeta (`../x.py`, symlink a afuera) → inválido, nada se importa (Tarea 1).
5. El secreto de un plugin se tacha en logs y errores incluso si la carpeta se borró después de cargarlo (Tarea 3).

---

### Task 1: Manifiestos y descubrimiento

**Files:**
- Create: `src/epsilon/plugins/__init__.py` (vacío, como las demás capas), `src/epsilon/plugins/channels.py`
- Create: `tests/plugins/__init__.py`, `tests/plugins/test_channels.py`

**Interfaces:**
- Consumes: `notes.safe_path(name: str) -> Path`, `config.BY_KEY`.
- Produces (en `epsilon.plugins.channels`):
  - `NAME_PATTERN: re.Pattern`
  - `class PluginError(Exception)` (mensaje listo para mostrar, en español)
  - `@dataclass(frozen=True) class PluginSetting: key: str; label: str; secret: bool; pattern: str; message: str`
  - `@dataclass(frozen=True) class Plugin: name: str; label: str; about: str; entry: Path; folder: Path; settings: tuple[PluginSetting, ...]`
  - `@dataclass(frozen=True) class Discovery: plugins: dict[str, Plugin]; invalid: dict[str, str]` (inválidos: carpeta → motivo)
  - `read_manifest(folder: Path) -> Plugin` (lanza `PluginError`)
  - `discover() -> Discovery` (mira `safe_path("plugins")`; si no existe, vacío; ignora archivos y carpetas que empiezan con `.` o `_`; ordenado por nombre)

- [ ] **Step 1: Escribir los tests de `read_manifest`/`discover`** en `tests/plugins/test_channels.py`, con un helper `make_plugin(root, name, toml, code="")` que crea la carpeta con `plugin.toml` y `channel.py` y una fixture que apunta `notes.DATA_DIR` a `tmp_path`. Casos, cada uno con su assert:
  - `test_valid_manifest_is_read`: manifiesto del spec (`name`, `label`, `about`, un `[[settings]]` con `secret = true`) → `Plugin` con `entry == folder/"channel.py"` y `settings[0].key == "DISCORD_TOKEN"`.
  - `test_no_plugins_folder_is_empty`: `discover()` → `Discovery({}, {})`.
  - `test_discover_sorts_and_skips_hidden_and_files`.
  - Inválidos, parametrizados, cada uno deja el motivo en `invalid` y no tira excepción en `discover()`: TOML roto; bytes no UTF-8; archivo de más de 16 KB; falta `plugin.toml`; `name` distinto de la carpeta; `name` con mayúsculas o `../`; `name` = `web` / `telegram`; `label` vacío o de 41 caracteres; `about` de 201; 11 ajustes; clave sin prefijo `DISCORD_`; clave `DISCORD_ENABLED`; clave `TELEGRAM_TOKEN` (la de otro ajuste de Epsilon, vía `config.BY_KEY`); clave repetida dentro del plugin; regex que no compila; `entry = "../fuera.py"`; `entry` que es un symlink a un archivo fuera de la carpeta; `entry` que no existe.
  - `test_same_key_in_two_plugins_marks_the_second_invalid` (el segundo por orden de nombre).

- [ ] **Step 2: Correr** `uv run pytest tests/plugins/test_channels.py -q` → falla (no existe el módulo).

- [ ] **Step 3: Implementar** `read_manifest` y `discover` en `src/epsilon/plugins/channels.py`. Usar `tomllib.loads` sobre texto leído con `utf-8` estricto y tope de 16 KB; `entry` por defecto `channel.py`, validado con `(folder / entry).resolve().is_relative_to(folder.resolve())` y `is_file()`. Defaults de un ajuste: `secret=False`, `pattern=r"[^\r\n]{1,200}"`, `message="Valor no válido."`. Los motivos de error en español y con el nombre de la carpeta o la clave involucrada.

- [ ] **Step 4: Correr** el mismo comando → pasa. Luego `uv run pytest -q` completo.

- [ ] **Step 5: Commit** `feat: read and validate channel plugin manifests`.

---

### Task 2: Importar el código de un plugin

**Files:**
- Modify: `src/epsilon/plugins/channels.py`
- Test: `tests/plugins/test_channels.py` (nuevos casos)

**Interfaces:**
- Consumes: `Plugin` de la Tarea 1; `config.redact(text) -> str`.
- Produces: `load_module(plugin: Plugin) -> types.ModuleType` (lanza `PluginError`). Importa `plugin.entry` con `importlib.util.spec_from_file_location(f"epsilon_plugin_{plugin.name}", plugin.entry)`. Verifica que el módulo tenga `is_configured`, `run` y `stop` invocables.

- [ ] **Step 1: Tests**:
  - `test_load_module_returns_a_module_with_the_contract`.
  - `test_import_error_becomes_plugin_error`: `channel.py` con `raise RuntimeError("boom TOKENSECRETO123")` y `TOKENSECRETO123` cargado como secreto → `PluginError` cuyo mensaje contiene «RuntimeError» y NO el secreto.
  - `test_system_exit_at_import_becomes_plugin_error`.
  - `test_syntax_error_becomes_plugin_error`.
  - `test_missing_stop_is_a_plugin_error` (mensaje nombra `stop`); idem `run` e `is_configured`.
  - `test_listing_never_imports_the_code`: tras `discover()` de un plugin cuyo `channel.py` hace `raise RuntimeError` y escribe un archivo marcador, no existe el marcador ni `"epsilon_plugin_<nombre>"` en `sys.modules`.
- [ ] **Step 2: Correr** → falla.
- [ ] **Step 3: Implementar** `load_module`; atrapar `Exception` y `SystemExit`, devolver `PluginError(config.redact(f"{type(e).__name__}: {e}"))`. No dejar el módulo a medias en `sys.modules` si falla.
- [ ] **Step 4: Correr** suite completa → verde.
- [ ] **Step 5: Commit** `feat: import channel plugin code on demand`.

---

### Task 3: Ajustes de plugins en `config.py`

**Files:**
- Modify: `src/epsilon/settings/config.py` (`status`, `redact`, nuevo registro)
- Modify: `src/epsilon/plugins/channels.py` (`settings_for`)
- Modify: `tests/conftest.py` (fixture autouse que deja el registro vacío entre tests)
- Test: `tests/settings/test_config.py`, `tests/plugins/test_channels.py`

**Interfaces:**
- Consumes: `Plugin`, `PluginSetting`; `config.Setting`.
- Produces:
  - `config.set_plugin_settings(items: list[Setting]) -> None`: reemplaza el conjunto anterior de ajustes de plugins (saca sus claves de `BY_KEY`, pone las nuevas). No puede pisar una clave de `SETTINGS`.
  - `config.all_settings() -> tuple[Setting, ...]`: `SETTINGS` + los de plugins. `status()` y `redact()` pasan a usarla. `SETTINGS` sigue siendo la tupla de los propios.
  - `plugins.channels.settings_for(plugin: Plugin) -> list[config.Setting]`: un `Setting` por ajuste del manifiesto (`live=True`, `page="config"`, `validate` = quita espacios, exige `fullmatch` con su `pattern`, rechaza caracteres de control incluidos `\r` y `\n`, error = su `message`) más `<NOMBRE>_ENABLED` (`default="no"`, `live=True`, `page="canales"`, `validate=config._yes_no`, `label=f"Canal de {plugin.label} prendido"`).

- [ ] **Step 1: Tests**:
  - `test_plugin_settings_are_editable_and_validated`: tras `set_plugin_settings(settings_for(p))`, `config.update({"DISCORD_TOKEN": "..."}, [], env_file)` escribe el valor; uno con `\n` o que no cumple el patrón lanza `ConfigError`.
  - `test_plugin_enabled_defaults_to_no`: `config.enabled("DISCORD_ENABLED") is False`; con «si» en el entorno, `True`.
  - `test_status_lists_plugin_settings_without_secret_values` (entrada con `set` y `length`, sin `value`).
  - `test_replacing_plugin_settings_removes_old_keys` y `test_cannot_shadow_a_builtin_key`.
  - **Review Focus 5:** `test_redact_still_hides_a_plugin_secret_after_its_plugin_is_gone`: registrar, poner el secreto en `os.environ`, llamar `set_plugin_settings([])`, `redact("x SECRETO12345 y")` sigue tachándolo (implementar con un conjunto de claves secretas de plugins que solo crece durante el proceso).
  - Fixture en `tests/conftest.py`: antes y después de cada test, `config.set_plugin_settings([])` y limpiar ese conjunto vía una función `config.reset_plugin_settings()` (solo la usan los tests).
- [ ] **Step 2: Correr** → falla.
- [ ] **Step 3: Implementar** lo de las Interfaces. `_current` y `enabled()` ya trabajan con `BY_KEY`, no hace falta tocarlos.
- [ ] **Step 4: Correr** suite completa → verde (revisar que ningún test existente dependa de `SETTINGS` incluyendo plugins).
- [ ] **Step 5: Commit** `feat: plugin settings join the config (redaction included)`.

---

### Task 4: `main.py` descubre, carga y arranca plugins

**Files:**
- Modify: `src/epsilon/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `plugins.channels.discover/load_module/settings_for`, `PluginError`, `config.set_plugin_settings`, `state.set`.
- Produces (en `epsilon.main`):
  - `refresh_plugins() -> None`: llama `discover()`; para cada plugin nuevo agrega `CHANNELS[name] = "plugin:" + name` (después de los incluidos, por nombre) y, si `state` no lo conoce, `state.set(name, "off", "Apagado.")`; saca de `CHANNELS` los plugins que ya no están en disco salvo que su hilo siga vivo; deja los ajustes registrados con `config.set_plugin_settings` (de los plugins actuales); guarda los inválidos en `INVALID: dict[str, str]`.
  - `is_plugin(name: str) -> bool`.
  - `load(name)`: igual que hoy para los incluidos; para un plugin importa con `load_module` una sola vez (caché por nombre) y convierte `PluginError` en `ChannelError`.
  - `switchable(name)` es `True` para un plugin SIN cargarlo (el contrato ya exige `stop`).
  - `enabled_channels` no carga los que están apagados (`switched_off` se evalúa antes que `load`), y si un plugin prendido falla al cargar lo deja en estado «stopped» con el motivo y sigue.
  - `main()` llama `refresh_plugins()` después de `install_log_redaction()` y no pisa con «skipped» un estado «stopped» ya anotado; un plugin apagado queda «off» sin cargarse.

- [ ] **Step 1: Tests** (con un plugin real en `tmp_path` como en la Tarea 1; usar `monkeypatch.setattr(main, "CHANNELS", dict(main.CHANNELS))` para no ensuciar el global):
  - `test_refresh_adds_plugins_after_builtins_and_marks_them_off`.
  - `test_refresh_does_not_import_plugin_code` (no está en `sys.modules`).
  - `test_disabled_plugin_is_not_loaded_at_startup`: `enabled_channels({...})` no lo incluye y no lo importa aunque esté configurado.
  - `test_enabled_plugin_starts_in_its_own_thread`: con `FAKE_ENABLED=si` y `is_configured` verdadero, `start_channel` lo corre en un hilo que no es el principal y `stop_channel` lo termina (`channel.py` de prueba con `threading.Event`).
  - **Review Focus 2:** `test_plugin_that_fails_to_import_is_stopped_but_others_run`: `main.main()` con web de mentira, un plugin prendido cuyo `channel.py` hace `raise RuntimeError("x")` → web corre igual; `state.snapshot()[name]["state"] == "stopped"` con el motivo; `start_channel(name)` lanza `ChannelError`.
  - `test_plugin_removed_from_disk_leaves_channels_unless_running`.
  - Los tests existentes de `test_main.py` siguen pasando sin cambios.
- [ ] **Step 2: Correr** `uv run pytest tests/test_main.py -q` → falla.
- [ ] **Step 3: Implementar** según Interfaces. Actualizar el comentario sobre `CHANNELS` en la parte de arriba de `main.py` (ahora también hay plugins, y qué contrato exigen).
- [ ] **Step 4: Correr** suite completa → verde.
- [ ] **Step 5: Commit** `feat: main discovers and runs channel plugins`.

---

### Task 5: Conversaciones de canales de plugins

**Files:**
- Modify: `src/epsilon/data/conversations.py` (`CHANNELS`, `_channel`, `current`, `for_channel`, `close_channel`, el título fijo en la línea `fixed = CHANNEL_TITLES.get(...)`)
- Test: `tests/data/test_conversations.py`

**Interfaces:**
- Consumes: `plugins.channels.NAME_PATTERN`.
- Produces: `conversations.is_channel(name) -> bool` (`name in CHANNELS` o `NAME_PATTERN.fullmatch(name)`); `conversations.fixed_title(channel) -> str | None` (`CHANNEL_TITLES.get(channel)`, y para un plugin `channel.capitalize()`; `None` para `web` o un nombre no válido). Las tres funciones de canal rechazan con `ValueError("Canal no válido")` lo que no cumpla `is_channel` o sea `"web"`.

- [ ] **Step 1: Tests**: `for_channel("discord")` crea una conversación con línea `meta` y `channel(id) == "discord"`; `current("discord")` la devuelve; `close_channel` la cierra; el título listado es «Discord»; `for_channel("../x")`, `for_channel("Web")` y `for_channel("web")` lanzan `ValueError`; un archivo con `meta` de canal inválido se lee como «web» (como hoy).
- [ ] **Step 2: Correr** → falla.
- [ ] **Step 3: Implementar.** Buscar con `grep -rn "CHANNELS\|== \"telegram\"" src/epsilon/data src/epsilon/channels/web.py` que no quede otra comparación que deje afuera a los plugins (el listado de la web debe seguir marcando solo lo de Telegram con su estilo).
- [ ] **Step 4: Correr** suite completa → verde.
- [ ] **Step 5: Commit** `feat: conversations accept channel plugins`.

---

### Task 6: API de la web: listar, confirmar y prender plugins

**Files:**
- Modify: `src/epsilon/channels/web.py` (`ChannelIn`, `channels_state`, `set_channel`)
- Test: `tests/channels/web/test_plugin_channels.py` (nuevo, usa las fixtures `client`/`cfg` de `conftest.py`)

**Interfaces:**
- Consumes: `main.refresh_plugins`, `main.INVALID`, `main.is_plugin`, `main.switchable`, `main.start_channel`, `main.stop_channel`.
- Produces: `GET /api/channels` llama `main.refresh_plugins()` y devuelve por canal los campos de siempre más `label: str | None`, `about: str | None`, `source: "builtin" | "plugin"` y `warning: str | None` (solo plugins: «Este canal corre dentro de Epsilon y puede ver tus claves. Prendelo solo si confiás en quien lo escribió.»). Cada plugin inválido sale como `{"name", "label": <carpeta>, "state": "invalid", "detail": <motivo>, "switchable": false, "enabled": false, "source": "plugin", "about": null, "warning": null}`. `PUT /api/channels/<nombre>` acepta `{"enabled": bool, "confirmed": bool = false}`; al prender un plugin sin `confirmed` responde 428 con `detail` = el aviso, sin tocar el `.env`.

- [ ] **Step 1: Tests** con una fixture `fake_plugin` que crea en `tmp_path/plugins/fake/` un `plugin.toml` (un ajuste `FAKE_TOKEN`, no secreto) y un `channel.py` con `is_configured(env)` = `bool(env.get("FAKE_TOKEN"))`, `run()` que espera un `threading.Event`, `stop()` que lo activa y `reset()` que lo limpia:
  - `test_plugin_is_listed_off_with_label_warning_and_source`, y que listar no lo importa.
  - `test_invalid_plugin_is_listed_as_invalid_and_cannot_be_toggled` (PUT → 404).
  - `test_enabling_without_confirmation_is_428_and_writes_nothing`.
  - `test_enabling_unconfigured_plugin_is_409`.
  - `test_enable_and_disable_plugin`: con `FAKE_TOKEN` en el entorno y `confirmed: true` → estado `running`; PUT `enabled: false` → `off`, y `FAKE_ENABLED` queda «no» en el `.env` temporal.
  - `test_plugin_that_fails_to_import_returns_409_with_reason`.
  - `test_plugin_token_never_appears_in_api_responses`.
  - Los tests existentes de `test_channels.py` siguen pasando (la fixture `switchable_telegram` parchea `CHANNELS` y `load`).
- [ ] **Step 2: Correr** `uv run pytest tests/channels/web -q` → falla.
- [ ] **Step 3: Implementar** según Interfaces. El orden en `set_channel` es: nombre existente y `switchable` (si no, 404) → confirmación (428) → `is_configured` (409) → escribir `<NOMBRE>_ENABLED` → `start_channel`.
- [ ] **Step 4: Correr** suite completa → verde.
- [ ] **Step 5: Commit** `feat: web API lists, confirms and toggles channel plugins`.

---

### Task 7: Página Canales con plugins

**Files:**
- Modify: `src/epsilon/channels/web_static/js/canales.js`, `src/epsilon/channels/web_static/css/style.css` (solo si hace falta estilo para el estado `invalid`)
- Test: `tests/channels/web/test_pages.py` (o el archivo de tests de páginas que ya vigila los JS; agregar los casos ahí)

**Interfaces:**
- Consumes: campos de la Tarea 6.
- Produces: `canales.js` usa `channel.label || NAMES[name] || name` y `channel.about || ABOUT[name] || ""`; `STATES.invalid = "Plugin inválido"`; los inválidos no llevan interruptor ni «Siempre activa» sino el motivo; al prender un canal con `warning` hace `window.confirm(channel.warning)` y, si acepta, manda `confirmed: true` (si cancela, no manda nada); el ícono de un plugin es el genérico que ya hay (`ICONS.web`).

- [ ] **Step 1: Test**: uno que lea `canales.js` y compruebe que no usa `innerHTML` y que menciona `channel.warning`, `confirmed` y `invalid`; si ya hay un test que recorre todos los `js/*.js` buscando `innerHTML`, alcanza con confirmar que cubre este archivo.
- [ ] **Step 2: Correr** → falla.
- [ ] **Step 3: Implementar** los cambios de `canales.js`.
- [ ] **Step 4: Correr** suite completa → verde.
- [ ] **Step 5: Verificación a mano** (CLAUDE.md pide probarlo en la app): crear una carpeta temporal de datos con `plugins/demo/` (manifiesto + `channel.py` de mentira), arrancar con `EPSILON_ENV_FILE=<copia de .env.example> DATA_DIR=<esa carpeta> uv run epsilon`, abrir http://127.0.0.1:8765/canales y comprobar: aparece «Demo» apagado; prenderlo pide confirmación; sin la clave cargada dice «Falta configurarlo»; con una carpeta rota aparece «Plugin inválido» con el motivo. Cerrar el proceso al terminar.
- [ ] **Step 6: Commit** `feat: Canales page shows and toggles plugins`.

---

### Task 8: Documentación y reinicio del servicio

**Files:**
- Create: `docs/plan-plugins.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1:** Escribir `docs/plan-plugins.md` (en español, como los otros `docs/plan-*.md`): para quien escribe un plugin, el contrato (`plugin.toml` con todos sus campos y límites, `channel.py` con `is_configured`/`run`/`stop`/`reset`/`explain`, cómo hablar con `core.handle` y con `conversations.for_channel`), un plugin de ejemplo completo y mínimo, y el aviso de que corre dentro de Epsilon. Aclarar que las integraciones serán MCP (otro proyecto) y que memoria y voz no son plugins.
- [ ] **Step 2:** En `CLAUDE.md`: agregar `plugins/  channels.py   canales de terceros (manifiesto, descubrimiento, carga)` al árbol de «Estructura», una viñeta sobre `plugins/channels.py` y su regla (listar no ejecuta código), y en la viñeta de `main.py` mencionar `refresh_plugins`. Cambiar la regla de «Pendiente» solo si hace falta (el cliente MCP sigue pendiente).
- [ ] **Step 3: Correr** `uv run pytest -q` → todo verde.
- [ ] **Step 4: Commit** `docs: channel plugin contract and CLAUDE.md`.
- [ ] **Step 5:** `systemctl --user restart epsilon` y confirmar con `systemctl --user is-active epsilon` que dice `active` (CLAUDE.md: el bot sigue con el código viejo si no se reinicia).

---

## Self-review

- **Cobertura del spec:** carpeta y manifiesto (T1), listar sin ejecutar (T1, T2, T4), importar al prender y errores como «detenido» (T2, T4), ajustes y redacción (T3), apagado por defecto (T3, T4), página Canales y aviso de primera vez (T6, T7), conversaciones (T5), documentación (T8). Quedan fuera a propósito: Telegram sin migrar, tienda, MCP.
- **Consistencia de nombres:** `Plugin`, `PluginSetting`, `Discovery`, `PluginError`, `settings_for`, `load_module`, `refresh_plugins`, `INVALID`, `is_plugin`, `is_channel`, `fixed_title` se usan igual en todas las tareas.
- **Decisión sobre el spec:** el spec dice «ajustes con `page="canales"`»; sigo el precedente de Telegram: solo `<NOMBRE>_ENABLED` va en la página Canales y las claves del plugin se editan en Configuración (`page="config"`).
- **Título de conversación:** el spec dice «título fijo = su label»; para no acoplar `data/` con `plugins/`, uso el nombre del canal con la primera letra en mayúscula (decisión mía; el `label` solo se ve en la página Canales).
