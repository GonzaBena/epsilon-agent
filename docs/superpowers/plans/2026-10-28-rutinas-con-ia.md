# Rutinas con IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Una rutina puede pedirle algo a la IA en lenguaje natural a una hora; la IA corre una vez para «aprender», Epsilon propone una receta (pasos fijos sin modelo + pasos «elegir» con una llamada mínima) y la persona la confirma.

**Architecture:** Una rutina con IA es una regla normal (`routines/`) cuyos `steps` incluyen dos pasos nuevos que solo existen en rutinas con IA: `ia` (turno de IA con herramientas acotadas) y `elegir` (una llamada corta que devuelve un ítem de una lista). Los permisos son un «ámbito» (`permissions.set_scope`) con una lista cerrada de herramientas en verde: dentro del ámbito nada pregunta, solo corre o se bloquea. La clasificación de la receta (fijo / elegir / libre) es una función pura sobre el registro de llamadas de la primera ejecución.

**Tech Stack:** Python (FastAPI, pytest, `uv run pytest`), JS plano en `channels/web_static/js/`. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-28-rutinas-con-ia-design.md` (la maqueta de la grilla: `2026-10-28-rutinas-ia-permisos-mockup.html`, misma carpeta).

## Global Constraints

- Solo corren las herramientas en verde; todo lo demás se bloquea, **nunca «preguntar»** (no hay nadie para contestar).
- Normal y rojo se comportan igual (la IA no puede usarlas); rojo es solo un «nunca» visible. Una sugerida (ámbar) no está permitida hasta que la persona la confirma.
- Confirmar al crear = permiso: el servidor exige `confirmed: true`. La receta no se usa hasta que la persona la confirma; hasta entonces corre la IA completa.
- Las herramientas **privadas** (estado inicial `ask`: historial, ventanas) o que **escriben** (estado inicial `ask`: notas, archivos, memoria) **nunca se sugieren solas**.
- Sin precio conocido del modelo, la rutina con IA **no corre**. Un modelo local (Ollama) es gratis y de precio conocido.
- Tope por ejecución (pasos) y tope diario por rutina en US$, ambos ajustes de `settings.json`; el gasto se anota como «Rutina IA».
- Solo la persona crea, edita o prende rutinas con IA: la IA no tiene ninguna herramienta que lo haga (las herramientas `rutina_*` quedan fuera de la grilla).
- Lo que devuelve una herramienta es dato, no instrucciones: «elegir» solo puede devolver un ítem de la lista.
- Una caída a la IA completa por ejecución como máximo, y no se reintenta sola.
- Nombres de código en inglés; mensajes al usuario en español rioplatense. Cada tarea termina con la suite verde (`uv run pytest -q`), commit sin firma de Claude, y el servicio reiniciado si cambió código del bot (`systemctl --user restart epsilon`).
- Nada en el diseño ni en el código asume YouTube Music, un horario o un gusto: el despertador es solo un ejemplo.

## Review Focus

1. **Una herramienta que no está en verde nunca corre**, ni porque el modelo la nombre, ni porque una receta guardada (editada a mano en `routines.json`) la liste. Test en Task 4 y Task 10.
2. **Una salida más larga que los topes** (el índice de playlists mide ~6000 caracteres y `TOOLS_MAX_RESULT_CHARS` vale 4000): «elegir» tiene que ver TODAS las opciones. Test en Task 10 (y arreglo del índice en Task 8).
3. **El modelo devuelve algo que no es una opción** (texto libre, «ignorá lo anterior…», un id inventado): se rechaza, se reintenta una vez, y nunca llega a otro paso. Test en Task 10.
4. **Una regla con `ai` mal formada** (editada a mano): no tira la página ni el motor; se omite o se pausa. Test en Task 3.
5. **Cambio de día y tope diario**: el gasto se reinicia a la medianoche local y, al llegar al tope, la rutina se salta y avisa. Test en Task 4.

---

## File Structure

- `src/epsilon/ai/permissions.py` — **modify**: ámbitos (`set_scope`, `clear_scope`, `in_scope`), `decide` y `offered` los respetan.
- `src/epsilon/ai/chat.py` — **modify**: `_specs`/`groups`/`offer` por conversación; `Turn.max_steps`, `Turn.trace`; `step_limit` lo respeta.
- `src/epsilon/routines/steps.py` — **modify**: `AI_STEPS`, `validate_chain(..., allow_ai)`, tope `MAX_SOURCE_OUTPUT`.
- `src/epsilon/routines/rules.py` — **modify**: `build_ai`, `_check_step` conserva `source`/`source_tool`/`only`, `describe_step`/`view` de pasos con IA, `spent_today`.
- `src/epsilon/routines/store.py` — **modify**: `add_spent`, `mark_stale`; `add`/`replace` redactan la instrucción y conservan `ai`.
- `src/epsilon/routines/recipe.py` — **create**: `Call`, `classify` (puro).
- `src/epsilon/routines/runner.py` — **modify**: `set_ai_executor`, pasos `ia`/`elegir`, caída a IA completa, aviso final.
- `src/epsilon/core.py` — **modify**: `run_ai_step`, `run_choose`, `learn_routine`, `routine_suggestions`, `routine_catalog` (`sensitive`), `stamp_steps`, registro del ejecutor.
- `src/epsilon/tools.py` — **modify**: campo `Tool.items` y su declaración en las 3 herramientas de música.
- `src/epsilon/data/music_index.py` — **modify**: `view(..., limit)` para que el índice entre en el tope.
- `src/epsilon/settings/tunables.py`, `README.md` — **modify**: `ROUTINES_AI_MAX_STEPS`, `ROUTINES_AI_DAILY_USD`.
- `src/epsilon/channels/web.py` — **modify**: endpoints.
- `src/epsilon/channels/web_static/js/rutinas-editor.js`, `rutinas.js`, `rutinas.html`, `css/style.css` — **modify**: UI.
- Tests: `tests/ai/test_permissions.py`, `tests/ai/test_chat.py`, `tests/routines/test_{steps,rules,store,runner}.py`, `tests/routines/test_recipe.py` (nuevo), `tests/test_core_routines_ai.py` (nuevo), `tests/channels/web/test_routines_ai_api.py` (nuevo), `tests/data/test_music_index.py`.

---

## Fase 1 — Rutina con IA completa, permisos y tope (útil por sí sola)

### Task 1: Ámbitos de permisos (nada pregunta dentro de una rutina)

**Files:** Modify `src/epsilon/ai/permissions.py`, `src/epsilon/ai/chat.py`, `src/epsilon/core.py` (`_run_calls`). Test: `tests/ai/test_permissions.py`, `tests/ai/test_chat.py`, `tests/test_core_routines_ai.py`.

**Interfaces:**
- Produces: `permissions.set_scope(scope_id: str, allowed: set[str]) -> None`, `permissions.clear_scope(scope_id: str) -> None`, `permissions.in_scope(scope_id: str) -> bool`; `permissions.decide(conversation_id, tool_id, version=None)` devuelve `"run"` si el ámbito de `conversation_id` incluye `tool_id`, si no `"block"` (nunca `"ask"`); `permissions.offered(conversation_id: str | None = None)` filtra por el ámbito; `chat._specs(conversation_id: str | None = None)`, `chat.groups(conversation_id: str | None = None)`; `chat.offer(turn)` y `core._load_groups` pasan `turn.conversation_id`.

- [ ] **Step 1: Write failing tests**
  - `test_scope_runs_only_listed_tools_and_never_asks`: con `set_scope("rutina:1", {"musica_poner"})`, `decide("rutina:1","musica_poner")=="run"`, `decide("rutina:1","nota")=="block"` (aunque `nota` sea `allow` global), `decide("rutina:1","yt_historial")=="block"`.
  - `test_scope_does_not_leak_to_other_conversations`: `decide("otra","nota")` sigue el estado global.
  - `test_offered_in_scope_is_the_allowed_tools_only` y `test_clear_scope_restores_global`.
  - `test_specs_in_scope_only_declare_allowed_tools` (en `test_chat.py`): `chat._specs("rutina:1")` solo trae el grupo `musica` con la acción `poner`.
  - `test_ask_from_the_browser_guard_becomes_block_inside_a_scope` (core): con `browser_guard.review` forzado a `Review("ask")` y el ámbito activo, `_run_calls` no devuelve pregunta (`session.pending_tool is None`) y el resultado dice que la rutina no puede preguntar.
- [ ] **Step 2: Run** `uv run pytest tests/ai tests/test_core_routines_ai.py -q` → Expected: FAIL (funciones no definidas).
- [ ] **Step 3: Implement** el ámbito como `dict[str, frozenset[str]]` protegido por `_lock`; `decide` consulta primero el ámbito (y respeta `_browser_off`); en `_run_calls`, tras `browser_guard.review`, `if decision == "ask" and permissions.in_scope(cid): decision = "block"` con `review.message` = «Esta rutina no puede preguntar: marcá la herramienta en verde al crearla.»; saltear también el cambio a `ask` por `ALWAYS_ASK` dentro de un ámbito (queda bloqueada).
- [ ] **Step 4: Run** `uv run pytest -q` → Expected: todo verde (ajustar los conteos de tests de permisos solo si cambió el catálogo; no debería).
- [ ] **Step 5: Commit** `feat: permission scopes so unattended AI turns can only use an allowlist`

### Task 2: Ajustes de tope y `Turn.max_steps` / `Turn.trace`

**Files:** Modify `src/epsilon/settings/tunables.py`, `README.md`, `src/epsilon/ai/chat.py`, `src/epsilon/core.py` (`_run`). Test: `tests/ai/test_chat.py`, `tests/settings/` (el test que exige que el README liste todo ajuste editable, `tests/test_tools.py::test_the_readme_lists_every_setting_a_person_can_edit`).

**Interfaces:**
- Produces: ajustes `ROUTINES_AI_MAX_STEPS` (int, 1–20, defecto `6`, `routines.ai_max_steps`, `loosens=True`) y `ROUTINES_AI_DAILY_USD` (float, 0.01–50, defecto `0.05`, `routines.ai_daily_usd`; seguir el patrón de `_float` y de un ajuste decimal existente en `tunables.py`/`config.py`; subirlo deja gastar más, así que marcarlo como el resto de los que `loosens`); `chat.Turn.max_steps: int | None = None`; `chat.Turn.trace: list[tuple[str, str, str, bool]]` = `(tool_id, args, result, ok)`; `chat.step_limit(turn)` devuelve `turn.max_steps` si está puesto.
- Consumes: `core._run` ya conoce `tool_id`, `args`, `ok`, `shown`.

- [ ] **Step 1: Write failing tests** — `test_step_limit_uses_the_turns_own_cap`, `test_run_records_each_call_in_the_trace` (una herramienta `ver` ejecutada por `_run` agrega `("ver", args, texto, True)`; una que falla agrega `ok=False`), y el test del README (agregar las dos filas a `README.md`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** los dos ajustes (con su `help` en español), los campos de `Turn`, y en `core._run` el `state.turn.trace.append(...)` después de ejecutar (con el texto legible `shown`, sin el rótulo de «datos»).
- [ ] **Step 4: Run** `uv run pytest -q` → verde.
- [ ] **Step 5: Commit** `feat: AI routine caps as settings and a per-turn call trace`

### Task 3: La forma de una regla con IA

**Files:** Modify `src/epsilon/routines/steps.py`, `rules.py`, `store.py`. Test: `tests/routines/test_steps.py`, `test_rules.py`, `test_store.py`.

**Interfaces:**
- Produces:
  - `steps.AI_STEPS: dict[str, dict]` con `"ia"` (`label` «Pedir a la IA») y `"elegir"` (`label` «Elegir con IA»); **no** se suman a `LOCAL` (no aparecen en el selector manual de pasos).
  - `steps.MAX_SOURCE_OUTPUT = 12000`.
  - `steps.validate_chain(chain, known, allow_ai: bool = False)`: con `allow_ai=True` acepta `ia`/`elegir`; un `elegir` exige `source` entero anterior y `source_tool` en `known`.
  - `rules.build_ai(name: str, when_text: str, instruction: str, tools: dict[str, str], now: datetime, known: set[str] | frozenset[str], layout: dict | None = None, recipe: list[dict] | None = None) -> dict`: valida `instruction` (1–`MAX_INSTRUCTION = 500` caracteres), `tools` (ids de `known`, estados `"green"|"red"`, al menos una verde), arma `steps = [{"tool": "ia", "args": instruction}]` si no hay `recipe` (si la hay, `steps = recipe` validada con `allow_ai=True` y solo con herramientas verdes), y `rule["ai"] = {"instruction", "tools", "recipe": bool, "stale": False, "spent": {"day": "", "usd": 0.0}}`.
  - `rules._check_step` conserva `source` (int), `source_tool` (str) y `only` (lista de ids) en pasos de IA.
  - `rules.spent_today(rule: dict, today: date) -> float` (0.0 si `spent.day` no es hoy o la regla no tiene `ai`).
  - `store.add_spent(rule_id: int, usd: float, today: date) -> float` (suma al día; reinicia si cambió el día; devuelve el total de hoy), `store.mark_stale(rule_id: int, stale: bool) -> None`; `store.add`/`replace` redactan `ai.instruction` y conservan `ai.spent` al reemplazar.
  - `rules.view(rule)` suma `"ai": {"instruction", "tools", "has_recipe", "stale", "spent_today"}` (o `None`) y el `text` de un paso `ia` es «IA: …».

- [ ] **Step 1: Write failing tests** — `test_build_ai_makes_a_single_ia_step`, `test_build_ai_rejects_empty_or_long_instruction`, `test_build_ai_needs_at_least_one_green_tool`, `test_build_ai_rejects_unknown_tools_and_bad_states`, `test_ia_steps_are_not_valid_in_a_manual_chain` (`validate_chain(..., allow_ai=False)` falla con «no está disponible»), `test_elegir_needs_an_earlier_source`, `test_spent_today_resets_on_a_new_day`, `test_add_spent_accumulates_and_rolls_over`, `test_replace_keeps_the_spent_counter`, `test_view_of_a_broken_ai_rule_is_omitted_not_raised` (una regla con `ai` = `"x"` hace que `rules.view` levante `TypeError`/`KeyError` como ya hace con reglas rotas, y `web.routines_view` la omite — verificar que el filtro existente cubre este caso).
- [ ] **Step 2: Run** `uv run pytest tests/routines -q` → FAIL.
- [ ] **Step 3: Implement** según Interfaces; reutilizar `_assemble` (`build_ai` arma la cadena y agrega `ai`).
- [ ] **Step 4: Run** `uv run pytest -q` → verde.
- [ ] **Step 5: Commit** `feat: AI routine rule shape (ia step, tool states, daily spend counter)`

### Task 4: Correr el paso `ia` (turno acotado, tope y gasto)

**Files:** Modify `src/epsilon/routines/runner.py`, `src/epsilon/core.py`. Test: `tests/routines/test_runner.py`, `tests/test_core_routines_ai.py`.

**Interfaces:**
- Consumes: Task 1 (`set_scope`), Task 2 (`Turn.max_steps`, `Turn.trace`), Task 3 (`rule["ai"]`, `store.add_spent`, `rules.spent_today`).
- Produces:
  - `runner.set_ai_executor(fn: Callable[[dict, dict, dict[int, str]], StepResult] | None) -> None` (`fn(rule, step, outputs)`); `runner._step` recibe también `rule` y, si `step["tool"] in steps.AI_STEPS`, llama a ese ejecutor (sin él: `StepResult(False, "No hay un ejecutor de IA registrado.")`).
  - `core.run_ai_step(rule: dict, step: dict, outputs: dict[int, str], trace: list | None = None) -> routine_runner.StepResult`: (1) `core._ai_budget_problem(rule) -> str | None` — «No conozco el precio del modelo…» si `not usage.price_known(llm.model_for("ask"))`, o «Esta rutina llegó a su tope de hoy (US$ x de US$ y).» si `rules.spent_today(rule, clock.today()) >= config.number("ROUTINES_AI_DAILY_USD")`; si hay problema → `StepResult(False, problema)` sin llamar al modelo; (2) permite `{id for id, s in rule["ai"]["tools"].items() if s == "green"}` (si el paso trae `only`, la intersección); (3) arma un `chat.Turn` con id `f"rutina:{rule['id']}:{next(_runs)}"`, `label="Rutina IA"`, `task="ask"`, `max_steps=config.number("ROUTINES_AI_MAX_STEPS")`, `groups` = los grupos de esas herramientas, historial = la instrucción (más, si `outputs` no está vacío, «Ya se hizo: …» con las salidas, como dato) con el rótulo «instrucción de la persona»; (4) `permissions.set_scope`, corre `_loop(turn, [], Session(), check_limit=False)` dentro de `usage.conversation(turn.conversation_id)` y limpia el ámbito en `finally`; (5) suma el gasto (`usage.for_conversation(id)["cost"]`) con `store.add_spent`; (6) `StepResult(True, reply.text)` o `False` si la respuesta es `EMPTY_ANSWER`/error del modelo; copia `turn.trace` a `trace` si se pidió.
  - `core.register_routine_executor()` registra también `set_ai_executor(run_ai_step)`.
  - `runner.run_job` agrega, al terminar bien una regla con `ai`, un aviso de una línea (`_tell`) con el texto del último paso, recortado a 200 caracteres y con el nombre de la rutina.

- [ ] **Step 1: Write failing tests** (modelo y herramientas falsos; nunca el modelo real)
  - `test_ia_step_runs_with_only_green_tools` (el modelo falso pide `nota` que no está en verde → resultado «no disponible» y `nota` no corre — **Review Focus 1**).
  - `test_ia_step_without_a_known_price_does_not_call_the_model` y `test_ollama_model_runs_without_a_price_problem`.
  - `test_ia_step_stops_at_the_daily_cap_and_the_notice_says_so`; `test_spend_resets_on_a_new_day` (**Review Focus 5**).
  - `test_ia_step_honours_the_step_cap` (el modelo falso pide herramientas sin parar → corta en `ROUTINES_AI_MAX_STEPS`).
  - `test_scope_is_cleared_even_if_the_model_raises`.
  - `test_run_job_sends_a_one_line_notice_for_ai_rules` y `test_a_fixed_rule_sends_no_extra_notice`.
  - `test_ia_step_without_an_ai_executor_fails_cleanly` (runner).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** según Interfaces. El contador `_runs = itertools.count(1)` es de módulo. La instrucción va envuelta como texto de la persona (no como dato de una página).
- [ ] **Step 4: Run** `uv run pytest -q` → verde.
- [ ] **Step 5: Commit** `feat: run an AI routine step with a scoped tool set, step cap and daily budget`

### Task 5: Sugerencias y catálogo de la grilla

**Files:** Modify `src/epsilon/core.py` (`routine_catalog`, nueva `routine_suggestions`), `src/epsilon/channels/web.py` (endpoint). Test: `tests/test_core_routines_ai.py`, `tests/channels/web/test_routines_ai_api.py`.

**Interfaces:**
- Produces: `core.routine_catalog()` suma `"sensitive": bool` a cada paso (`permissions.default_state(tool) != "allow"`; los de conectores, `True`); `core.routine_suggestions(text: str) -> list[str]`: ids de herramientas del catálogo cuyo grupo se nombra en `text` (misma comparación que `chat.preload`, sin modelo; extraer de `preload` un helper `chat.groups_named(text) -> set[str]` y que `preload` lo use) **excluyendo** las `sensitive`, las de `LOCAL`, y las que gastan IA; además `fecha` si el texto no es vacío (para saber la hora); `GET /api/routines/suggest?text=…` → `{"tools": [ids]}`.
- [ ] **Step 1: Write failing tests** — `test_suggestions_for_play_music_include_music_tools_and_not_private_ones` (con «poné música para despertarme»: `musica_poner`, `musica_playlists`, `musica_buscar` sí; `yt_historial`, `ventanas`, `nota`, `crear_archivo` no), `test_suggestions_never_include_routine_tools_or_ai_spenders`, `test_suggestions_of_unrelated_text_are_empty_but_the_clock`, `test_catalog_marks_sensitive_tools`, `test_suggest_endpoint_returns_ids` y que valida el largo del texto (≤ 500).
- [ ] **Step 2–4:** run (FAIL) → implement → `uv run pytest -q` verde.
- [ ] **Step 5: Commit** `feat: tool suggestions for AI routines by keywords, never private or writing ones`

### Task 6: API de rutinas con IA

**Files:** Modify `src/epsilon/channels/web.py` (`RoutineIn`, `build_routine`, `create_routine`, `edit_routine`, `test_routine`, `routines_view`). Test: `tests/channels/web/test_routines_ai_api.py`.

**Interfaces:**
- Consumes: `rules.build_ai`, `core.run_ai_step`.
- Produces: `RoutineIn` suma `instruction: str = ""` y `ai_tools: dict[str, str] = {}`; con `instruction` no vacío `build_routine` usa `routine_rules.build_ai(...)`; crear y editar exigen `confirmed: true` (422 «Falta confirmar…»); `POST /api/routines/test` con `instruction` corre el paso `ia` de verdad una vez y devuelve `{"results": [{"ok", "text"}]}`; `routines_view` incluye `ai`. Editar solo el nombre o la posición no exige confirmar de nuevo (igual que hoy: `same_plan` compara también `ai`).
- [ ] **Step 1: Write failing tests** — `test_create_ai_routine_needs_confirmation`, `test_create_ai_routine_needs_a_green_tool`, `test_create_ai_routine_rejects_tools_outside_the_catalog`, `test_edit_ai_routine_changing_the_instruction_needs_confirmation`, `test_test_endpoint_runs_the_ia_step_once` (ejecutor falso), `test_the_ai_cannot_create_ai_routines` (no hay herramienta de IA con `instruction`: `rutina_crear` rechaza/ignora el campo y sigue siendo solo cadenas fijas), `test_routines_list_exposes_ai_info_and_spent_today`.
- [ ] **Step 2–4:** run (FAIL) → implement → verde.
- [ ] **Step 5: Commit** `feat: web API to create, edit and test AI routines`

### Task 7: UI de la fase 1 (campo + grilla de cuatro estados)

**Files:** Modify `channels/web_static/js/rutinas-editor.js`, `js/rutinas.js`, `rutinas.html`, `css/style.css`. Test: los de estructura que vigilan el HTML (`tests/channels/web/`: los que comparan cabecera/menú) y una verificación manual.

**Interfaces:** consume `GET /api/routines/steps` (ahora con `sensitive`), `GET /api/routines/suggest?text=`, `POST /api/routines`, `PUT /api/routines/{id}/edit`, `POST /api/routines/test`.

- [ ] **Step 1: Implement** en el editor: un selector «Pasos fijos / Pedir a la IA»; en «Pedir a la IA», el campo «Qué le pedís a la IA» (máx. 500) y la grilla agrupada por `group` con los cuatro estados (`normal → verde → rojo → normal`; la sugerida pasa a verde al primer toque), colores según la maqueta (verde `#2e9d57`, rojo `#d64545`, ámbar `#e8960c` punteado), sugerencias pedidas al endpoint con un retardo de 400 ms mientras se escribe (las sugeridas nunca pisan lo que la persona ya marcó), todo el texto del servidor con `textContent`, y los estados con texto además de color (accesibilidad). «Probar ahora» y «Guardar» muestran la instrucción, el cuándo y las herramientas **en verde** y piden confirmar (`confirmed: true`). La lista de `/rutinas` muestra «IA» y «gastó hoy US$ …» en las rutinas con `ai`.
- [ ] **Step 2: Verify** `uv run pytest -q` verde; reiniciar el servicio; abrir `http://127.0.0.1:8765/rutinas` y comprobar en el navegador (skill `run`): crear una rutina con IA «Poné música para despertarme», ver las sugeridas en ámbar, confirmar, «Probar ahora» y que suene algo.
- [ ] **Step 3: Commit** `feat: editor for AI routines with the four-state tool grid`

---

## Fase 2 — Registro y receta (sin modelo)

### Task 8: Herramientas con ítems seleccionables y el índice que entra en el tope

**Files:** Modify `src/epsilon/tools.py`, `src/epsilon/data/music_index.py`, `src/epsilon/ai/music.py`. Test: `tests/test_tools.py`, `tests/data/test_music_index.py`, `tests/ai/test_music.py`.

**Interfaces:**
- Produces: `tools.Tool.items: str = ""` (regex con UN grupo que extrae los ids de la salida) y `tools.items_pattern(tool_id: str) -> re.Pattern | None`; declarado en `musica_playlists` (`r"\(id: ([A-Za-z0-9_-]{10,64})"`), `musica_buscar` (`r"id: ([A-Za-z0-9_-]{10,64})"`) y `yt_historial` (`r"id: ([A-Za-z0-9_-]{11})"`); `music_index.view(index, now=None, rng=None, limit: int | None = None)` — con `limit`, si el texto no entra, primero se quitan los «artistas» y después se corta por playlists enteras (nunca a mitad de una línea); `ai/music.playlists_text` pasa `limit = config.number("TOOLS_MAX_RESULT_CHARS") - 300`.
- [ ] **Step 1: Write failing tests** — `test_items_patterns_extract_the_ids_of_the_real_outputs` (usa la forma real de las tres salidas), `test_tools_without_items_have_no_pattern`, `test_view_with_a_limit_drops_artists_then_whole_playlists_but_never_cuts_a_line` (índice de 24 playlists → `len(view) <= limit` y cada línea termina con «pusiste…»), `test_playlists_text_fits_the_tool_result_cap`.
- [ ] **Step 2–4:** run (FAIL) → implement → verde. (Esto también arregla que la IA veía cortada la lista del índice en el chat.)
- [ ] **Step 5: Commit** `fix: the playlist index fits the tool result cap; tools declare selectable items`

### Task 9: Clasificar el registro (puro) y aprender

**Files:** Create `src/epsilon/routines/recipe.py`; Modify `src/epsilon/core.py` (`learn_routine`, `stamp_steps`), `src/epsilon/channels/web.py` (`POST /api/routines/learn`). Test: `tests/routines/test_recipe.py`, `tests/test_core_routines_ai.py`, `tests/channels/web/test_routines_ai_api.py`.

**Interfaces:**
- Produces:
  - `recipe.Call` (dataclass frozen: `tool: str`, `args: str`, `output: str`, `ok: bool`).
  - `recipe.classify(instruction: str, calls: list[Call], items: Callable[[str], re.Pattern | None]) -> list[dict]`: ignora las llamadas con `ok=False`; devuelve pasos `{"tool", "args", "kind": "fijo"|"elegir"|"libre", "text"}` en orden, **numerados desde 1 por posición**. Reglas, en este orden: (1) si el argumento contiene exactamente un ítem de la salida de una llamada anterior cuya herramienta declara `items` y esa salida tiene ≥ 2 ítems → se inserta antes un paso `{"tool": "elegir", "args": instruction, "kind": "elegir", "source": <n del paso fuente>, "source_tool": <id>}` y la llamada queda `fijo` con el ítem reemplazado por `{{paso <n del elegir>}}`; (2) argumento vacío, o cuyo texto normalizado (minúsculas, sin acentos) aparece en la instrucción normalizada → `fijo`; (3) el resto → `libre`.
  - `core.learn_routine(rule_draft: dict) -> dict`: corre el paso `ia` con `trace` y devuelve `{"results": [{"ok","text"}], "recipe": [...pasos de classify...] | None}` (`recipe=None` si la corrida falló o no usó herramientas).
  - `core.stamp_steps` permite `{{paso N}}` en un paso de herramienta que normalmente no lo acepta (`NO_TEMPLATE_TOOLS`, destructivos) **solo si todas las referencias apuntan a pasos `elegir`**.
  - `POST /api/routines/learn` (body como `RoutineIn`, exige `confirmed`) devuelve lo de `learn_routine`; no guarda nada.
- [ ] **Step 1: Write failing tests** — `test_exactly_one_item_of_an_earlier_output_becomes_choose_plus_templated_call`; `test_a_constant_matching_the_instruction_is_fixed`; `test_empty_args_are_fixed`; `test_args_from_nowhere_are_free` (el «lofi» inventado); `test_tool_without_declared_items_never_makes_choose` (**anti-meta**); `test_failed_calls_are_ignored`; `test_two_items_in_one_arg_are_free_not_choose`; `test_source_with_a_single_item_is_not_a_choice`; `test_learn_routine_returns_no_recipe_when_the_run_failed`; `test_stamp_steps_allows_choose_refs_but_not_other_refs_in_a_poner_step`.
- [ ] **Step 2–4:** run (FAIL) → implement → verde.
- [ ] **Step 5: Commit** `feat: learn a routine from one AI run and classify its steps (fixed/choose/free)`

### Task 10: Confirmar y guardar la receta

**Files:** Modify `src/epsilon/routines/rules.py`, `src/epsilon/channels/web.py` (`RoutineIn.recipe`, `create_routine`, `edit_routine`), `routines/store.py`. Test: `tests/routines/test_rules.py`, `tests/channels/web/test_routines_ai_api.py`.

**Interfaces:**
- Produces: `RoutineIn.recipe: list[RecipeStepIn] | None` con `RecipeStepIn(tool: str, args: str, kind: str, source: int | None, source_tool: str | None, decision: str = "")`, donde `decision` ∈ `""`, `"fijo"`, `"ia"` y solo importa si `kind == "libre"`. `rules.build_ai(..., recipe=...)` convierte: `fijo` → paso normal; `elegir` → paso `elegir`; `libre` + `decision == "fijo"` → paso normal; `libre` + `decision == "ia"` → `{"tool": "ia", "args": <instrucción>, "only": [tool]}`; un `libre` sin decisión → ValueError «Decidí qué hacer con el paso N». Validación del servidor: toda herramienta de un paso `fijo` y todo `only` están en verde; `elegir.source < su posición` y `source_tool` declara `items`; máx. `steps.MAX_STEPS`. Guardar con receta pone `ai.recipe = True`, `stale = False`.
- [ ] **Step 1: Write failing tests** — `test_recipe_with_a_non_green_tool_is_rejected` (**Review Focus 1**), `test_free_step_without_a_decision_is_rejected`, `test_free_step_decided_ia_becomes_an_ia_step_with_only`, `test_choose_step_needs_a_source_that_declares_items`, `test_saving_a_recipe_requires_confirmation`, `test_hand_edited_recipe_with_a_non_green_tool_fails_at_run_time` (en `test_runner.py`: el paso no corre y la rutina avisa), `test_editing_the_instruction_drops_the_recipe`.
- [ ] **Step 2–4:** run (FAIL) → implement → verde.
- [ ] **Step 5: Commit** `feat: confirm and save a learned recipe with the person's decisions`

---

## Fase 3 — Repetir la receta (lo barato)

### Task 11: El paso `elegir`

**Files:** Modify `src/epsilon/routines/runner.py`, `src/epsilon/core.py`, `src/epsilon/routines/steps.py`. Test: `tests/test_core_routines_ai.py`, `tests/routines/test_runner.py`.

**Interfaces:**
- Produces: `core.run_choose(rule: dict, step: dict, outputs: dict[int, str]) -> routine_runner.StepResult`: toma `outputs[step["source"]]`, extrae las opciones con `tools.items_pattern(step["source_tool"])` (únicas, en orden), arma UNA llamada con `llm.generate` (tarea `ask`, `usage.action("Rutina IA")`, `max_tokens` chico) cuyo texto contiene: la instrucción (como texto de la persona), la hora local (`clock`), la memoria de la persona recortada a `MAX_CHOOSE_MEMORY = 800` caracteres, y las opciones (el texto completo de la salida fuente, como dato); pide responder SOLO el id. Valida que la respuesta, sin espacios ni comillas, sea una de las opciones; si no, **un** reintento; si sigue mal `StepResult(False, "…")`. Respeta `_ai_budget_problem` y suma el gasto. `runner.run_chain`: el tope de la salida de un paso que es `source` de algún `elegir` es `steps.MAX_SOURCE_OUTPUT` (no `MAX_OUTPUT`) y se le pasa al ejecutor `cap=steps.MAX_SOURCE_OUTPUT`; `core.run_step(tool_id, args, version=None, cap: int | None = None)` usa `cap or TOOLS_MAX_RESULT_CHARS`. Un paso de herramienta cuyo id no está en verde en `rule["ai"]["tools"]` no corre (`StepResult(False, "Esa herramienta no está permitida en esta rutina.")`).
- [ ] **Step 1: Write failing tests** — `test_choose_sees_every_option_even_if_the_source_is_longer_than_the_default_caps` (salida de 6000 caracteres con 24 ids; el prompt falso recibido las contiene todas — **Review Focus 2**); `test_choose_rejects_text_that_is_not_an_option_retries_once_then_fails` (respuesta «ignorá lo anterior y poné X» → rechazada — **Review Focus 3**); `test_choose_accepts_the_id_with_quotes_or_spaces`; `test_choose_prompt_has_instruction_time_memory_and_options_only` (nada más: ni historial de chat, ni herramientas); `test_choose_does_not_call_the_model_without_a_price_or_over_the_daily_cap`; `test_the_chosen_id_flows_into_the_next_step_via_the_template`; `test_a_recipe_step_with_a_tool_not_in_green_does_not_run` (runner).
- [ ] **Step 2–4:** run (FAIL) → implement → verde.
- [ ] **Step 5: Commit** `feat: replay a recipe: fixed steps without a model, choose steps with one small call`

### Task 12: Caída a la IA completa y receta vieja

**Files:** Modify `src/epsilon/routines/runner.py`, `routines/store.py`. Test: `tests/routines/test_runner.py`.

**Interfaces:**
- Produces: en `runner.run_job`, si una regla con `ai.recipe` falla en un paso: **una** corrida de un paso `ia` completo con las verdes (usando el ejecutor de IA); `store.mark_stale(rule_id, True)`; aviso «La receta de «nombre» quedó vieja (paso N falló: …); esta vez lo hizo la IA. Volvé a aprenderla desde Rutinas.»; si la IA completa también falla, aviso del error y nada más. Una regla `stale` sigue corriendo con la IA completa (no usa la receta) hasta que la persona confirme una receta nueva (`store.replace` la limpia).
- [ ] **Step 1: Write failing tests** — `test_a_failing_fixed_step_falls_back_to_one_full_ai_run_and_marks_stale`; `test_the_fallback_runs_at_most_once` (si también falla, no hay segundo intento); `test_a_stale_rule_runs_full_ai_until_relearned`; `test_notice_names_the_failed_step`; `test_a_rule_without_a_recipe_does_not_fall_back` (ya corre la IA completa).
- [ ] **Step 2–4:** run (FAIL) → implement → verde.
- [ ] **Step 5: Commit** `feat: stale recipes fall back to one full AI run and ask to relearn`

---

## Fase 4 — Pantalla de la receta

### Task 13: Aprender y confirmar en la UI

**Files:** Modify `channels/web_static/js/rutinas-editor.js`, `js/rutinas.js`, `rutinas.html`, `css/style.css`.

- [ ] **Step 1: Implement** el botón «Aprender y probar» (usa `POST /api/routines/learn` tras confirmar el aviso de que corre de verdad); al volver muestra el resultado de la corrida y la **receta** con una etiqueta por paso (**fijo · elige la IA · libre**), con un selector «dejar fijo / que decida la IA» en cada paso libre; «Guardar» envía `recipe` con las decisiones y `confirmed: true`. En la lista, «receta vieja» (insignia) con el botón «Volver a aprender» y «Gasto de hoy». Todo texto del servidor con `textContent`; las etiquetas llevan texto además de color.
- [ ] **Step 2: Verify** `uv run pytest -q` verde; reiniciar el servicio; comprobar en el navegador (skill `run`): crear «Despertador» con IA, aprender, ver la receta `musica_playlists (fijo) → elegir → musica_poner (fijo)`, guardar, forzar la hora con «Probar ahora» y comprobar que la segunda ejecución hace UNA llamada al modelo (mirar `/uso`, «Rutina IA»).
- [ ] **Step 3: Update docs** `CLAUDE.md` (sección Rutinas: rutinas con IA, ámbitos, receta, topes) y `docs/plan-planificador.md`; commit `feat: learn-and-confirm screen for AI routine recipes`.

---

## Self-review (hecha)

- **Cobertura del spec:** crear con grilla (T5–T7), permisos sin «preguntar» (T1), aprender y receta (T9–T10, T13), ejecuciones baratas (T11), receta vieja (T12), topes y precio (T2, T4, T11), aviso final (T4), seguridad (T1, T4, T10, T11), datos (T3), sugerencias sin privadas ni de escritura (T5), la IA no crea rutinas con IA (T6). La fase de «libre» → `ia` con `only` se cubre en T10 y se ejecuta con el mismo `run_ai_step` (T4).
- **Consistencia de tipos:** `run_ai_step(rule, step, outputs, trace)`, `set_ai_executor(fn(rule, step, outputs))`, `Turn.trace` con 4-tuplas y `recipe.Call(tool, args, output, ok)` coinciden entre T2, T4 y T9; `source`/`source_tool`/`only` se definen en T3 y se usan en T9–T11.
- **Hallazgo que cambia el alcance:** el índice de playlists (6143 caracteres) supera `TOOLS_MAX_RESULT_CHARS` (4000): la IA ya lo ve cortado en el chat. Se arregla en T8 (antes de que lo use «elegir»).
- **Fuera de alcance (a propósito):** integrar un modelo de decisiones tipo Jev, condiciones disparadas por IA, y que la IA cree rutinas.
