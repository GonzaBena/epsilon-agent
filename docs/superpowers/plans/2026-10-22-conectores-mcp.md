# Conectores MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Epsilon pueda enchufar servidores MCP locales (stdio) como «conectores»: se agregan desde la web, dan herramientas a la IA (con permisos) y a la persona (`/c` y una grilla de botones).

**Architecture:** Capa nueva `src/epsilon/connectors/` (`store`, `manager`, `bridge`, `buttons`). Un solo hilo con un ciclo asíncrono maneja todos los procesos MCP, arrancados bajo demanda y cerrados por inactividad. `tools.py` (que no importa nada del proyecto) gana un «proveedor» de herramientas dinámicas; el resto (`permissions`, `chat._specs`, `core._run_calls`) las trata como a las demás. Un conector apagado simplemente no aporta herramientas.

**Tech Stack:** Python, SDK `mcp` (nuevo), FastAPI, JS plano en `channels/web_static/`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-conectores-mcp-design.md`

## Global Constraints

- Nombres de código en inglés; mensajes al usuario en español. Commits sin firma de Claude (regla del proyecto).
- Todo archivo de datos por `notes.safe_path`, escritura por archivo temporal + `os.replace`, cambios con candado (modelo: `routines/store.py`).
- La web muestra todo con `textContent`; las claves/valores de `env` nunca salen del servidor (solo «cargada» y su largo) y pasan por `config.redact` en logs y errores.
- El comando del conector se parte con `shlex.split` y se lanza SIN shell. Entorno del proceso: solo `PATH`, `HOME`, `LANG` y lo que la persona cargó; nunca el de Epsilon.
- Nombre de conector: `^[a-z][a-z0-9_]{1,19}$`, distinto de todo grupo de `tools.AI_GROUPS` y de todo `ai_id` de `tools.py`. Máx. 10 conectores.
- `IDLE_CLOSE = 600` s, `MAX_RUNNING = 3`, `CALL_TIMEOUT = 30` s, resultado recortado a `core.MAX_RESULT_CHARS`, descripciones recortadas a 300 caracteres.
- Todas las herramientas de un conector: `ai_default="ask"`. Solo texto como resultado.
- `/c` NO lleva `links` (un enlace `/?cmd=` nunca lo ejecuta). Agregar/quitar conectores: solo desde la web, nunca por la IA.
- Los tests no arrancan procesos: `manager._open` se bloquea en `tests/conftest.py`; se reemplaza por un conector falso.
- Tests nuevos en carpetas espejo: `tests/connectors/` (con `__init__.py`), web en `tests/channels/`. Después de cada tarea: `uv run pytest` verde y commit.

## Review Focus

1. El proceso muere o no responde en medio de una llamada → error en español para la IA/persona, conector «detenido», Epsilon sigue (Task 2).
2. Nombres de herramienta MCP con `-`, `.`, mayúsculas o repetidos tras normalizar (`get-events` / `get_events`) → acción válida y única, sin romper `route` (Task 3).
3. El modelo manda JSON roto, claves de más, tipo equivocado o texto suelto → no se llama al conector y el error dice qué recibió (Task 3).
4. Dos llamadas a la vez y el cierre por inactividad justo en ese momento → no se pierde ni se duplica ninguna (Task 2).
5. `connectors.json` o `buttons.json` editado a mano/corrupto, o un botón cuya herramienta ya no existe → la página y la IA siguen andando, el botón avisa (Tasks 1, 7).

---

### Task 1: Dependencia, bloqueo en tests y `connectors/store.py`

**Files:**
- Modify: `pyproject.toml` (agregar `mcp`), `tests/conftest.py`
- Create: `src/epsilon/connectors/__init__.py`, `src/epsilon/connectors/store.py`, `tests/connectors/__init__.py`, `tests/connectors/test_store.py`

**Interfaces:**
- Produces (`store.py`): `FILE = "connectors.json"`, `MAX_CONNECTORS = 10`, `class StoreError(Exception)` (texto en español); `list_all() -> list[dict]`; `get(name) -> dict | None`; `add(name: str, argv: list[str], env: dict[str, str]) -> dict`; `update(name, **fields) -> dict` (campos permitidos: `enabled`, `autostart`, `env`, `tools`, `error`); `remove(name) -> None`; `public(entry: dict) -> dict` (sin valores de `env`: `{"KEY": {"set": True, "length": n}}`). Entrada: `{"name","argv","env","enabled": True,"autostart": False,"tools": [],"error": ""}`; cada tool: `{"name","description","schema","hash"}`.

- [ ] **Step 1: Agregar la dependencia.** `uv add mcp`; confirmar que `uv run python -c "import mcp"` anda.
- [ ] **Step 2: Tests en `test_store.py`** (con `DATA_DIR` temporal como en `tests/routines/test_store.py`):
  `test_add_and_get_roundtrip`; `test_name_must_match_pattern` (`"Luces"`, `"a"`, `"x"*21` → `StoreError`); `test_name_cannot_be_an_ai_group_or_tool` (`"notas"`, `"navegador"`, `"fecha"` → `StoreError`); `test_duplicate_name_rejected`; `test_max_connectors` (el 11.º falla); `test_argv_must_be_nonempty_single_line_strings`; `test_env_keys_must_look_like_variables_and_values_single_line` (`"mi-clave"`, valor con `\n` → `StoreError`); `test_public_never_contains_env_values` (`public(...)["env"] == {"TOKEN": {"set": True, "length": 6}}` y el valor no aparece en `json.dumps`); `test_corrupt_file_reads_as_empty` (archivo con basura → `list_all() == []`); `test_update_rejects_unknown_fields`; `test_remove`.
- [ ] **Step 3: Correr y ver fallar.** `uv run pytest tests/connectors/test_store.py -v` → falla por import.
- [ ] **Step 4: Implementar `store.py`** con la forma de `routines/store.py` (`_read`/`_write`/`_lock`); la lista de nombres reservados sale de `tools.AI_GROUPS` y `{tools.ai_id(t) for t in tools.ai_tools()}`.
- [ ] **Step 5: Bloquear `_open` en tests.** En `tests/conftest.py`, fixture autouse `block_real_connectors` que reemplaza `epsilon.connectors.manager._open` por una función que lanza `RuntimeError("Un test intentó arrancar un conector de verdad")` (se activa en la Task 2, cuando exista `manager`; hasta entonces no se agrega).
- [ ] **Step 6: Correr todo** `uv run pytest` → verde. **Commit:** `git add -A && git commit -m "feat: connectors store — connectors.json with validated names, argv and env (values never public)"`.

---

### Task 2: `connectors/manager.py` (hilo único, arranque perezoso, cierre por inactividad)

**Files:**
- Create: `src/epsilon/connectors/manager.py`, `tests/connectors/fakes.py`, `tests/connectors/test_manager.py`
- Modify: `tests/conftest.py` (fixture del Step 5 de la Task 1)

**Interfaces:**
- Consumes: `store.get/update`.
- Produces (`manager.py`):
  - constantes de Global Constraints; `class ConnectorError(Exception)` (español, ya redactado).
  - `minimal_env(extra: dict[str, str]) -> dict[str, str]`.
  - `async def _open(argv: list[str], env: dict[str, str]) -> Link` — ÚNICO punto que lanza el proceso (SDK `mcp`: `stdio_client` + `ClientSession`). `Link` expone `async list_tools() -> list[dict]` (`name`, `description`, `schema`), `async call(name: str, args: dict) -> str` (une los bloques de texto; avisa si hubo contenido no-texto), `async read_resource(uri: str) -> str | None`, `async close()`.
  - `start(name: str) -> list[str]`: arranca si hace falta, descubre herramientas, las guarda en el store (`hash` = sha256 de nombre+esquema+descripción) y devuelve los **nombres de herramientas nuevas o cambiadas**; llama al gancho `on_changed`.
  - `stop(name)`, `stop_all()`, `call(name, tool, args: dict) -> str`, `read_resource(name, uri) -> str | None`, `status(name) -> str` (`"apagado" | "dormido" | "activo" | "detenido"`), `tick(now: float) -> None` (cierra los inactivos; el hilo lo llama cada 30 s, pero se prueba directo), `set_changed_hook(fn: Callable[[str, list[str]], None]) -> None`.
- `tests/connectors/fakes.py`: `FakeLink` (herramientas configurables, `calls` registrado, puede fallar, tardar o morir) y `install(monkeypatch, links: dict[str, FakeLink])` que reemplaza `manager._open`.

- [ ] **Step 1: Tests** (`test_manager.py`):
  `test_minimal_env_has_only_path_home_lang_and_extra` (con `GEMINI_API_KEY` en `os.environ`, `minimal_env({"X": "1"})` no la tiene); `test_open_is_lazy_start_happens_on_first_call` (agregar conector, `status == "dormido"`, tras `call` → `"activo"`, `_open` se llamó una vez); `test_disabled_connector_never_opens` (`call` → `ConnectorError("…apagado…")`, `_open` sin llamadas); `test_start_stores_tools_and_reports_new_then_changed` (primer `start` → todas nuevas; segundo igual → `[]`; cambiar el esquema de una → solo esa); `test_idle_connector_is_closed_by_tick` (`tick(now + IDLE_CLOSE + 1)` cierra y `FakeLink.closed`; una `call` posterior lo reabre); `test_tick_does_not_close_a_busy_or_recent_connector`; `test_max_running_closes_the_least_recently_used` (4.º conector abierto cierra el más viejo); `test_call_timeout_returns_spanish_error_and_stops_connector` (`FakeLink` que tarda más que `CALL_TIMEOUT` parcheado a 0.05 → `ConnectorError`, `status == "detenido"`, `store.get(...)["error"]` no vacío); `test_crash_mid_call_marks_stopped_and_next_call_restarts`; `test_open_failure_marks_stopped_with_redacted_reason` (el error contiene un valor de `env` → no aparece); `test_concurrent_calls_both_complete` (dos hilos llamando a la vez, ambos resultados correctos); `test_call_racing_with_idle_close_reopens` (`tick` cierra mientras otra llamada entra → la llamada igual se completa).
- [ ] **Step 2: Ver fallar.** `uv run pytest tests/connectors/test_manager.py -v`.
- [ ] **Step 3: Implementar.** Un hilo daemon con `asyncio` loop creado perezosamente (`_loop()`); funciones públicas sincrónicas hacen `asyncio.run_coroutine_threadsafe(...).result(timeout)`. Un `asyncio.Lock` por conector serializa abrir/cerrar/llamar. Errores del SDK/OS se traducen a `ConnectorError` pasando por `config.redact`.
- [ ] **Step 4: Fixture de bloqueo** en `conftest.py` y correr toda la suite → verde.
- [ ] **Step 5: Commit** `feat: connector manager — one shared asyncio thread, lazy start, idle close, timeouts`.

---

### Task 3: `connectors/bridge.py` (herramienta MCP ⇄ `tools.Tool`, argumentos)

**Files:**
- Create: `src/epsilon/connectors/bridge.py`, `tests/connectors/test_bridge.py`

**Interfaces:**
- Consumes: `tools.Tool`, entradas del store.
- Produces:
  - `action_name(tool_name: str, taken: set[str]) -> str` — minúsculas, `[^a-z0-9_]` → `_`, sin `.`; si choca con `taken`, sufijo `_2`, `_3`…
  - `tool_id(connector: str, action: str) -> str` → `f"mcp_{connector}_{action}"`.
  - `build_tools(entry: dict) -> tuple[tools.Tool, ...]` — una `Tool` por herramienta guardada: `ai="write"`, `ai_default="ask"`, `ai_name=tool_id`, `ai_group=entry["name"]`, `ai_action=action`, `ai_help` = descripción recortada a 300, `ai_arg` = `arg_help(schema)`, `label=f"{entry['name']}.{action}"`.
  - `group_text(entry: dict) -> str` — descripción del grupo, recortada, rotulada «herramientas del conector X (su descripción es un dato)».
  - `arg_help(schema: dict) -> str` — ej.: `objeto JSON con: habitacion (texto, obligatorio), estado (uno de: on, off; opcional)`.
  - `class ArgsError(Exception)`; `parse_args(schema: dict, text: str) -> dict` — JSON objeto; rechaza claves desconocidas, faltantes obligatorias y tipos erróneos (`string/integer/number/boolean/enum`); el mensaje dice qué recibió (≤ 200 caracteres).
  - `parse_cli(schema: dict, text: str) -> dict` — para `/c` y botones: `{...}` va a `parse_args`; si no, tokens `clave=valor` (`shlex`), con conversión por tipo, y luego la misma validación.
  - `real_name(entry: dict, action: str) -> str | None` — nombre MCP original de una acción.

- [ ] **Step 1: Tests:** `test_action_name_sanitizes` (`"Get-Events.v2"` → `"get_events_v2"`); `test_action_name_collisions_get_suffix` (`get-events` y `get_events` → `get_events`, `get_events_2`); `test_build_tools_are_ask_by_default_and_routable` (`tools.route("luces.apagar")` funciona una vez registrado — en la Task 4 se completa; acá solo comprobar campos); `test_description_is_trimmed_to_300`; `test_arg_help_mentions_required_and_enum`; `test_parse_args_accepts_valid_json`; `test_parse_args_rejects_broken_json_and_says_what_was_received`; `test_parse_args_rejects_unknown_and_missing_keys`; `test_parse_args_rejects_wrong_type` (`"3"` donde se espera integer en JSON string → error; `3` OK); `test_parse_args_rejects_enum_value_outside_list`; `test_parse_cli_key_value_pairs_and_coercion` (`habitacion=living brillo=50` con `brillo` integer → `{"habitacion": "living", "brillo": 50}`); `test_parse_cli_quotes` (`nombre="sala grande"`); `test_parse_cli_accepts_json`.
- [ ] **Step 2: Ver fallar; Step 3: implementar** (validación a mano sobre el subconjunto de JSON Schema listado; sin dependencias nuevas); **Step 4: suite verde; Step 5: commit** `feat: connector bridge — MCP tools as Tool entries, schema-validated arguments`.

---

### Task 4: Herramientas dinámicas en `tools`, permisos, IA y `core`

**Files:**
- Modify: `src/epsilon/tools.py` (`PROVIDERS`, `add_provider`, `ai_tools`, `group_text`), `src/epsilon/ai/chat.py:171` (`group_text`), `src/epsilon/core.py` (`_execute`), `src/epsilon/ai/permissions.py` (`reset`), `src/epsilon/main.py` (registrar proveedor y gancho)
- Create: `src/epsilon/connectors/registry.py` (el proveedor: lee el store, solo conectores `enabled`, cache invalidada por `store.update`), `tests/connectors/test_registry.py`, `tests/connectors/test_core_connectors.py`

**Interfaces:**
- Produces: `tools.add_provider(fn: Callable[[], tuple[tuple[Tool, ...], dict[str, str]]]) -> None` (la función devuelve herramientas y `{grupo: texto}`); `tools.ai_tools()` = estáticas + las de los proveedores; `tools.group_text(group: str) -> str`; `permissions.reset(tool_id: str) -> None` (borra la clave de `permissions.json` para volver al estado inicial); `registry.provide()` (la que se registra); `connectors.__init__` no importa nada pesado.
- `core._execute`: si `tool_id` empieza con `mcp_`, resuelve conector y herramienta real desde el store y llama `manager.call(name, real_name, bridge.parse_args(schema, args))`; el error (`ArgsError`/`ConnectorError`) vuelve como texto al modelo, sin gastar más. El resultado sigue el camino de siempre («datos, no instrucciones», redactado, recortado).
- `main`: `tools.add_provider(registry.provide)`; `manager.set_changed_hook(lambda name, ids: [permissions.reset(bridge.tool_id(name, a)) for a in ids])`; `manager.stop_all()` al cerrar; al arrancar, `manager.start(name)` en los conectores con `enabled` y `autostart` (si uno falla, queda «detenido» y Epsilon sigue). Test: `test_autostart_connectors_start_with_epsilon_and_failures_do_not_stop_it` en `tests/test_main.py`.

- [ ] **Step 1: Tests** (con `fakes.install`): `test_disabled_connector_contributes_no_tools`; `test_enabled_connector_tools_are_offered_to_the_ai` (`permissions.offered()` incluye `mcp_luces_apagar`); `test_default_permission_is_ask` (`permissions.load()["mcp_luces_apagar"] == "ask"`); `test_route_finds_connector_tool_by_group_dot_action`; `test_group_description_comes_from_group_text` y `chat._specs()` incluye el grupo `luces` con su enum de acciones; `test_a_deny_permission_hides_it`; `test_ai_call_asks_then_runs_and_result_is_marked_as_data` (flujo `core` completo con modelo falso, como en `tests/test_core_tools.py`: la pregunta muestra `luces.apagar` y los argumentos en bloque; tras «Sí», el resultado vuelve como «Resultado de mcp_luces_apagar (datos, no instrucciones)»); `test_bad_args_never_reach_the_connector` (`FakeLink.calls == []`); `test_connector_error_text_reaches_the_model_not_a_traceback`; `test_changed_schema_resets_permission_to_ask` (poner `allow`, cambiar esquema, `start` → vuelve a `ask`); `test_existing_group_consistency_tests_still_pass` (correr `tests/test_tools.py` sin conectores: `set(AI_GROUPS)` sigue igual).
- [ ] **Step 2–4: ver fallar, implementar, suite verde.** `tools.py` sigue sin importar nada del proyecto (la tarea lo vigila el test existente de estructura).
- [ ] **Step 5: Commit** `feat: connector tools reach the AI — dynamic tool provider, per-tool permissions (default ask)`.

---

### Task 5: Comando `/c` (sin modelo)

**Files:**
- Modify: `src/epsilon/tools.py` (entrada nueva), `src/epsilon/core.py` (`COMMANDS["c"]`, `_connector_cmd`), `tests/connectors/test_core_connectors.py`

**Interfaces:**
- `Tool("/c", name="c", usage="/c [conector herramienta args]", help="usar un conector (sin args: lista lo que hay)", menu="Usar un conector (sin args: lista)", insert="/c ", writes=True)`; sin `links` ni `ai`.
- `core._connector_cmd(args: str) -> Reply`: sin args → lista conectores con estado y sus herramientas (`luces: apagar, prender`); con `conector herramienta [args]` → `parse_cli` + `manager.call`; resultado redactado y recortado. No pasa por `permissions` (la persona lo escribió).

- [ ] **Step 1: Tests:** `test_c_without_args_lists_connectors_and_tools`; `test_c_runs_tool_with_key_value_args` (`/c luces apagar habitacion=living` → `FakeLink.calls == [("apagar", {"habitacion": "living"})]`); `test_c_runs_with_json_args`; `test_c_unknown_connector_or_tool_says_so`; `test_c_disabled_connector_says_it_is_off`; `test_c_bad_args_explain_without_calling`; `test_c_is_not_a_link_command` (`"/c" not in tools.link_commands()`); `test_c_does_not_use_the_model` (conftest ya bloquea). Los tests de `tests/test_tools.py` y `tests/channels/test_telegram.py` que cruzan `COMMANDS`/menú deben seguir verdes.
- [ ] **Step 2–4: ver fallar, implementar, suite verde.**
- [ ] **Step 5: Commit** `feat: /c command — run a connector tool without the model`.

---

### Task 6: API y página `/conectores`

**Files:**
- Modify: `src/epsilon/channels/web.py` (rutas, `connectors_view()`, ruta de la página), todas las `channels/web_static/*.html` (entrada «Conectores» en el menú izquierdo, entre Permisos y Rutinas), `docs`/`CLAUDE.md` (Task 8)
- Create: `src/epsilon/channels/web_static/conectores.html`, `src/epsilon/channels/web_static/js/conectores.js`, `tests/channels/test_web_connectors.py`

**Interfaces:**
- `connectors_view() -> dict` → `{"connectors": [{"name","command","enabled","autostart","status","error","env": {KEY: {"set","length"}},"tools": [{"action","description","changed"}]}]}`. Nunca contiene valores de `env`.
- `POST /api/connectors/preview` `{command}` → `{"argv": [...]}` (422 con motivo en español si `shlex` falla o queda vacío).
- `POST /api/connectors` `{name, command, env: {KEY: value}, confirm: bool}` → rechaza (422, «Confirmá que querés correr este programa») si `confirm` no es `true`; si cierra, guarda, hace `manager.start(name)` (si falla, el conector queda guardado «detenido» con el motivo) y devuelve la lista.
- `PUT /api/connectors/{name}` `{enabled?, autostart?, env?}` (apagar llama `manager.stop`; prender hace `start`); `DELETE /api/connectors/{name}` (para el proceso y borra sus botones, ver Task 7). Todas contestan `connectors_view()`.
- Página: lista con interruptor y estado, formulario «Agregar» (pegar comando → «Ver cómo lo entiendo» muestra el `argv` y el aviso de la spec → botón «Confirmar y agregar»), ficha con herramientas (marca «nueva/cambió»), variables de entorno (solo «cargada, N caracteres»), «Quitar». Todo con `textContent`; patrón visual y de refresco de `rutinas.js`.

- [ ] **Step 1: Tests** (patrón de `tests/channels/` para rutinas): `test_add_requires_explicit_confirmation`; `test_add_never_uses_a_shell` (`command="echo hi; rm -rf /"` → `argv == ["echo", "hi;", "rm", "-rf", "/"]`, y `_open` recibe esa lista); `test_preview_returns_parsed_argv_without_running` (`_open` sin llamadas); `test_view_never_contains_env_values` (el valor secreto no aparece en el JSON de ninguna respuesta); `test_bad_name_gives_spanish_422`; `test_toggle_off_stops_and_removes_tools_from_ai`; `test_toggle_on_starts`; `test_delete_stops_process`; `test_failed_start_is_saved_as_stopped_with_reason`; `test_endpoints_validate_host_and_origin` (como los demás endpoints); `test_conectores_page_is_served` y `test_menu_is_identical_on_every_page` (el test existente que vigila menú y barra tiene que seguir verde con la entrada nueva).
- [ ] **Step 2–4: ver fallar, implementar, suite verde.** Probar a mano con `uv run epsilon` y `docs/ejemplos/luces_mcp.py` (Task 8) una vez exista; hasta entonces, con el conector falso de los tests.
- [ ] **Step 5: Commit** `feat: Conectores page — add with confirmation, toggle, configure, remove`.

---

### Task 7: Botones (grilla de n×2) y sugerencias

**Files:**
- Create: `src/epsilon/connectors/buttons.py`, `tests/connectors/test_buttons.py`, `tests/channels/test_web_buttons.py`
- Modify: `src/epsilon/channels/web.py`, `conectores.html`, `js/conectores.js`, `css/style.css` (grilla simple de 2 columnas)

**Interfaces:**
- `buttons.py` (`DATA_DIR/buttons.json`, mismo patrón de store): `list_all() -> list[dict]` (`id: int, label: str, connector: str, tool: str, args: dict`); `add(label, connector, tool, args) -> dict` (valida que el conector y la acción existan y `args` contra el esquema; `label` 1–40 caracteres, una línea; máx. 40 botones); `remove(button_id)`; `remove_for(connector)`; `run(button_id) -> str` (revalida contra el esquema HOY; si la herramienta ya no existe lanza `StoreError` en español); `suggestions(connector: str) -> list[dict]` (lee `epsilon://buttons` con `manager.read_resource`, parsea JSON, **descarta** entradas inválidas o de herramientas inexistentes, máx. 10, `label` recortado).
- API: `GET /api/buttons` (lista + `suggestions` por conector activo, sin arrancar procesos dormidos: las sugerencias se piden con `GET /api/connectors/{name}/suggestions`), `POST /api/buttons {label, connector, tool, args}`, `DELETE /api/buttons/{id}`, `POST /api/buttons/{id}/run` → `{"text": "..."}` (no toca ninguna conversación).
- UI: en `/conectores`, sección «Botones»: grilla de n filas × 2 columnas; cada card = nombre de la acción + botón «Ejecutar» + zona de resultado (`textContent`); formulario «Crear botón» (conector, herramienta, argumentos en `clave=valor`/JSON, nombre); «Sugeridos» con botón «Agregar».

- [ ] **Step 1: Tests:** `test_add_validates_args_against_schema`; `test_add_rejects_unknown_connector_or_tool`; `test_label_rules`; `test_run_calls_tool_without_ai_permissions` (herramienta en `deny` igual corre desde el botón); `test_run_revalidates_and_explains_when_tool_disappeared`; `test_run_disabled_connector_says_it_is_off`; `test_corrupt_buttons_file_reads_as_empty`; `test_suggestions_filter_invalid_entries` (JSON roto → `[]`; entrada con herramienta inexistente descartada; más de 10 → 10); `test_connector_without_the_resource_has_no_suggestions` (`read_resource` devuelve `None`); `test_suggestions_are_never_created_automatically`; `test_deleting_a_connector_removes_its_buttons`; web: `test_run_endpoint_returns_text_and_creates_no_conversation`; `test_buttons_page_data_has_no_env_values`.
- [ ] **Step 2–4: ver fallar, implementar, suite verde.**
- [ ] **Step 5: Commit** `feat: connector buttons — 2-column grid, run without the model, connector suggestions`.

---

### Task 8: Ejemplo, documentación y puesta en marcha

**Files:**
- Create: `docs/ejemplos/luces_mcp.py` (servidor MCP mínimo con SDK `mcp`: herramienta `luces(habitacion: str, estado: "on"|"off")` que solo imprime/registra, y recurso `epsilon://buttons` con dos sugerencias), `docs/plan-conectores.md` (guía corta: cómo agregar un conector, el recurso de botones, seguridad), `tests/connectors/test_example.py`
- Modify: `CLAUDE.md` (estructura: carpeta `connectors/`, `/c`, página Conectores, reglas: conectores nunca por la IA, entorno mínimo), `docs/plan-plugins.md` (quitar «las integraciones serán MCP: otro proyecto» → apuntar a `plan-conectores.md`)

- [ ] **Step 1: Test del ejemplo:** `test_example_server_defines_the_tool_and_the_buttons_resource` (importa el módulo del ejemplo y comprueba, sin arrancar procesos, que el JSON de `epsilon://buttons` es una lista cuyas entradas pasan `buttons.suggestions` contra el esquema de su herramienta).
- [ ] **Step 2: Escribir el ejemplo y las docs; actualizar `CLAUDE.md`.**
- [ ] **Step 3: Verificación a mano** (`uv run epsilon`): agregar `uv run python docs/ejemplos/luces_mcp.py` en `/conectores`, confirmar, ver las herramientas, crear un botón y ejecutarlo; en un chat de modo IA pedir «apagá las luces del living» y comprobar que pregunta antes de correr; apagar el conector y comprobar que la IA ya no lo ve; esperar el cierre por inactividad (o bajar `IDLE_CLOSE` en la prueba) y ver que el proceso se va (`pgrep -f luces_mcp`).
- [ ] **Step 4: Suite completa** `uv run pytest` → verde.
- [ ] **Step 5: Commit** `docs: MCP connectors guide and example server`; luego `systemctl --user restart epsilon` y `systemctl --user status epsilon` para confirmar que quedó activo.
