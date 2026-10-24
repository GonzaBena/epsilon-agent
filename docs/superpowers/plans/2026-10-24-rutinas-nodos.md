# Rutinas con activador y pasos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que una rutina sea un activador más una cadena de pasos (cualquier herramienta de Epsilon, con la salida de un paso disponible para los siguientes), que se arma en un modal con pizarra de nodos (pantalla grande) o lista vertical (pantalla chica).

**Architecture:** `routines/rules.py` sigue puro y gana el modelo de pasos y las plantillas; un `routines/runner.py` nuevo ejecuta la cadena en un hilo por rutina con un ejecutor que `core` registra (así `routines/` no importa el núcleo); el motor (`engine.tick`) solo adelanta la próxima vez y lanza el runner. La web suma un catálogo de pasos, crear/editar/probar con confirmación exigida por el servidor, y un editor de dos vistas sobre los mismos datos.

**Tech Stack:** Python 3 / FastAPI / pytest (`uv run pytest`), JS plano en `channels/web_static/js/` (sin CDN, sin estilos en línea por la CSP, todo lo del servidor con `textContent`).

**Spec:** `docs/superpowers/specs/2026-10-24-rutinas-nodos-design.md`

## Global Constraints

- Los tests nunca ejecutan herramientas reales ni llaman a un modelo; el ejecutor de pasos se inyecta (`runner.set_executor`) y `time.sleep`/`notify` se reemplazan.
- Todo acceso a archivos por `notes.safe_path`; `routines.json` se reescribe por archivo temporal; todo cambio por `store.update` con candado; tope 50 rutinas.
- `routines/rules.py` no tiene hilos, reloj, archivos ni procesos.
- Mensajes al usuario en español, nombres de código en inglés. Todo lo que entra a un registro o a la web pasa por `config.redact`.
- Confirmación al crear = permiso (opción A): una herramienta en `ask` o `allow` corre en una rutina; una `deny` (o desconocida, o apagada: navegador con `BROWSER_ENABLED=no`, conector apagado) corta la cadena. Los bloqueos duros de `navegador.py` (`file:`, red local, `BROWSER_BLOCKED_SITES`) siguen valiendo; `browser_guard.review` NO se llama.
- Topes: `MAX_STEPS = 10` por rutina, `MAX_OUTPUT = 1000` caracteres de salida por paso, `esperar` de 1 a 60 s, `RUN_BUDGET = 300` s por ejecución, `MAX_NAME = 60`.
- Una cadena se corta en el primer paso que falla y se avisa cuál (notificación + sumideros) y por qué; una línea por paso en `routines.log`.
- Sin IA en los pasos: se excluyen del catálogo las herramientas con `uses_ai`, el grupo `rutinas`, `enviar` y las que dependen de una conversación.
- Después de tocar el código del bot: `systemctl --user restart epsilon` y confirmar que quedó activo. Commit sin firma de Claude (memoria del proyecto: no `Co-Authored-By`), nunca push.

## Review Focus

1. Referencia a un paso que no existe o posterior (`{{paso 3}}` en un paso 2) → se rechaza al guardar, con el motivo en español.
2. Salida de un paso con comillas, saltos de línea o una clave (`sk-…`) metida en los argumentos JSON de un conector → la sustitución se hace sobre los valores ya parseados (nunca sobre el texto JSON), redactada y recortada.
3. Una rutina vieja `when`/`do` (incluida una de condición) se migra y el aviso sigue diciendo «⚠ … (el disco al 93 %)».
4. Dos disparos de la misma rutina a la vez → el segundo se omite y se avisa; un paso colgado no frena el reloj ni a las otras rutinas, y `engine.stop()` interrumpe un `esperar`.
5. Editar pasos o activador sin `confirmed: true` → 422; renombrar o mover nodos no lo exige.
6. Una herramienta bloqueada en `/permisos` después de crear la rutina corta la cadena antes de ese paso (los anteriores ya corrieron).

## File Structure

- Create `src/epsilon/routines/steps.py` — puro: plantillas, validación de cadena, catálogo local (`avisar`, `esperar`), texto «a > b».
- Create `src/epsilon/routines/runner.py` — ejecutor de cadenas (hilo por rutina, no solapar, presupuesto de tiempo, avisos).
- Modify `src/epsilon/routines/rules.py` — modelo `{name, when, steps}`, migración, `describe`/`view`.
- Modify `src/epsilon/routines/store.py` — `add` con pasos, `replace`, migración al leer.
- Modify `src/epsilon/routines/engine.py` — `tick` lanza el runner en vez de `_run`.
- Modify `src/epsilon/core.py` — `run_step`, `routine_catalog`, `Reply.failed`, `/rutina` con cadenas, preview, registro del ejecutor.
- Modify `src/epsilon/channels/web.py` — catálogo, crear/editar/probar.
- Modify `src/epsilon/channels/web_static/rutinas.html`, `js/rutinas.js`, `css/style.css`; create `js/rutinas-editor.js` (modal + lista) y `js/rutinas-canvas.js` (pizarra).
- Modify `CLAUDE.md` (párrafo de Rutinas), `docs/plan-planificador.md`.
- Tests: `tests/routines/test_steps.py`, `test_runner.py` (nuevos) y los existentes de `rules`/`store`/`engine`; `tests/test_core_tools.py`; `tests/channels/web/test_routines_api.py`, `test_routines_page.py`.

---

### Task 1: Spike del despertador (descartable, sin tests)

**Files:** ninguno del proyecto (script en el scratchpad). Anotar el resultado en una línea al final de la spec.

- [ ] **Step 1:** Con Chromium cerrado, correr `uv run python -c` que llame a `core._nav_open("<link de la playlist de la persona>")` y espere 5 s. Anotar: ¿lo lanza solo `ensure_running`?, ¿suena sin tocar nada?
- [ ] **Step 2:** Si no suena, correr después `core._music_cmd("play")` y anotar si alcanza. El resultado decide la plantilla de ejemplo que se muestra en el modal (con o sin paso `musica play`); no cambia el resto del plan. Avisar a la persona el resultado.

### Task 2: Plantillas y cadena (puro)

**Files:** Create `src/epsilon/routines/steps.py`; Test `tests/routines/test_steps.py`

**Interfaces:**
- Produces:
  - `MAX_STEPS`, `MAX_OUTPUT`, `MAX_WAIT = 60`, `LOCAL = {"avisar": ..., "esperar": ...}` (id → `{"label", "hint"}`), `ALIASES = {"abrir": "nav_abrir"}`.
  - `refs(text: str) -> list[int | str]`: referencias en un texto (`{{paso 2}}` → `2`, `{{disparador}}` → `"disparador"`).
  - `validate_chain(steps: list[dict], known: set[str]) -> None`: `ValueError` en español si un paso tiene herramienta fuera de `known | LOCAL`, hay más de `MAX_STEPS`, o referencia un paso no anterior.
  - `render(text: str, outputs: dict[int, str], trigger: str = "") -> str`: sustituye; salida faltante → `""`; cada valor recortado a `MAX_OUTPUT`.
  - `render_args(args: str, outputs, trigger) -> str`: si `args` es un objeto JSON, sustituye en sus valores string ya parseados y vuelve a serializar; si no, `render` sobre el texto.
  - `parse_chain(text: str, known: set[str]) -> list[dict]`: «volumen 15 > abrir https://… > esperar 5 > avisar Suena {{paso 2}}» → `[{"tool", "args"}]` (aplica `ALIASES`); `ValueError` si no se entiende.
  - `describe_step(step: dict) -> str`.

- [ ] **Step 1: Write the failing tests**, con estas aserciones:
  - `render("Suena {{paso 2}}", {2: "Canción X"}) == "Suena Canción X"`; sin salida → `"Suena "`; con salida de 5000 caracteres el resultado mide ≤ `MAX_OUTPUT + len("Suena ")`.
  - `render_args('{"q": "{{paso 1}}"}', {1: 'a"b\nc'})` → JSON válido que al parsear da `{"q": 'a"b\nc'}` (Review Focus 2).
  - `validate_chain([{"tool":"avisar","args":"{{paso 1}}"}], {...})` y una referencia a un paso posterior → `ValueError`; 11 pasos → `ValueError`; `esperar 90` se valida en el runner, no acá.
  - `parse_chain("abrir yt > esperar 5", {"nav_abrir"})` → `[{"tool":"nav_abrir","args":"yt"},{"tool":"esperar","args":"5"}]`.
- [ ] **Step 2:** `uv run pytest tests/routines/test_steps.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3: Implement** las funciones de arriba en `steps.py`. Las plantillas usan la expresión regular `\{\{\s*(paso\s+\d+|disparador)\s*\}\}`; los números de paso empiezan en 1.
- [ ] **Step 4:** mismo comando → PASS.
- [ ] **Step 5: Commit** `feat: step chains and templates for routines (pure)`.

### Task 3: Modelo de reglas y migración

**Files:** Modify `rules.py`, `store.py`; Test `tests/routines/test_rules.py`, `test_store.py`

**Interfaces:**
- Consumes: `steps.validate_chain`, `steps.parse_chain`, `steps.describe_step`.
- Produces:
  - Regla guardada: `{"id", "name", "enabled", "when", "steps": [{"tool","args"}], ...next_run | tripped/last_run}`. (Decisión del plan: se conserva la clave `when` en disco, no `trigger`, para no tocar motor, web y tests; la spec lo llama activador.)
  - `rules.migrate(rule: dict) -> dict`: una regla con `do` pasa a `steps` (`avisar`→`{"tool":"avisar","args":texto}`, `volumen`→`{"tool":"volumen","args":arg}`, `musica`→`{"tool":"musica","args":arg}`), `name` = `describe_do` de lo viejo; una de condición con `avisar` pasa a `args = "⚠ " + texto + " ({{disparador}})"`. Idempotente.
  - `rules.build(text: str, now, known: set[str]) -> dict`: «cuándo | pasos» (pasos con `parse_chain`); mantiene el mensaje de uso. Nombre automático = `describe_chain`.
  - `rules.build_from(name: str, when_text: str, steps: list[dict], now, known) -> dict`: la forma estructurada (web).
  - `rules.describe(rule)`, `rules.view(rule)` ahora devuelven `name`, `steps` y una frase por paso (`"steps": [{"tool","label","args"}]`); `view` mantiene las claves actuales (`id, kind, when, active, next, next_text`) y agrega `name` y `steps`.
  - `store.add(rule)` redacta `args` de cada paso; `store._read()` migra al leer; `store.replace(rule_id: int, rule: dict) -> dict | None` reemplaza `name/when/steps` conservando `id` y recalculando `next_run`.
  - `MAX_NAME = 60`.

- [ ] **Step 1: Write the failing tests:** migración de `avisar`, `volumen` y de una condición (Review Focus 3, comparando el texto final con `steps.render(..., trigger="el disco al 93 %")`); `build("23:00 | volumen 20", now, known)["steps"] == [{"tool":"volumen","args":"20"}]`; el mensaje de uso sigue en `ValueError`; `store.add` redacta una clave en `args`; `store._read` de un archivo viejo devuelve reglas con `steps`; `store.replace` de un id inexistente → `None`.
- [ ] **Step 2:** `uv run pytest tests/routines -v` → FAIL.
- [ ] **Step 3: Implement** lo anterior. `parse_do`/`ACTIONS` se conservan solo para la migración y el aviso de uso.
- [ ] **Step 4:** `uv run pytest tests/routines -v` → PASS (los tests viejos de `rules`/`store` se actualizan al nuevo formato).
- [ ] **Step 5: Commit** `feat: routines store steps, migrate old rules`.

### Task 4: Runner de cadenas

**Files:** Create `src/epsilon/routines/runner.py`; Test `tests/routines/test_runner.py`

**Interfaces:**
- Consumes: `steps.render_args`, `steps.MAX_WAIT`, `notify.send`, `engine._log` (mover `_log` a `runner` y que `engine` lo importe; `engine._log` queda como alias para sus tests).
- Produces:
  - `@dataclass StepResult: ok: bool; text: str`.
  - `set_executor(fn: Callable[[str, str], StepResult] | None)`: ejecutor de herramientas (lo registra `core`).
  - `run_chain(rule: dict, trigger: str = "", on_step: Callable[[int, StepResult], None] | None = None) -> list[StepResult]`: ejecuta en orden en el hilo que la llama; `avisar` usa `notify.send`, `esperar` espera con `_stop.wait` (valida 1–`MAX_WAIT`, si no → falla), cualquier otra va al ejecutor; corta en el primer `ok=False`; respeta `RUN_BUDGET` (corta con «Se pasó del tiempo»); la salida de cada paso se redacta y recorta a `MAX_OUTPUT` antes de guardarse como `outputs[n]`.
  - `start(rule: dict, trigger: str = "") -> bool`: lanza `run_chain` en un hilo `rutina-<id>` y avisa del resultado; `False` si esa rutina ya está corriendo (el aviso «sigue en curso, omití esta vez» y una línea en el registro).
  - `describe_failure(rule, index, result) -> str`: «No pude cumplir la rutina 3 (paso 2, abrir): motivo».
  - `stop()`: fija el evento que corta los `esperar`.

- [ ] **Step 1: Write the failing tests** (ejecutor falso, `notify.send` y `_stop.wait` reemplazados): la salida del paso 1 llega al paso 2 por `{{paso 1}}`; un fallo en el 2 corta, no ejecuta el 3 y avisa el número; `esperar 90` falla sin dormir; dos `start` seguidos de la misma regla (el primero bloqueado con un evento) → el segundo devuelve `False` (Review Focus 4); `stop()` libera un `esperar` en curso; sin ejecutor registrado → falla clara; el presupuesto se agota con un reloj falso.
- [ ] **Step 2:** `uv run pytest tests/routines/test_runner.py -v` → FAIL.
- [ ] **Step 3: Implement** el módulo. El ejecutor se llama fuera de cualquier candado.
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat: routine runner (chains, no overlap, budget)`.

### Task 5: Motor usa el runner

**Files:** Modify `engine.py`; Test `tests/routines/test_engine.py`

**Interfaces:**
- Consumes: `runner.start(rule, trigger)`.
- Produces: `engine.tick(now)` conserva todo lo que hace (adelantar antes de ejecutar, GRACE, perdidas, reposo de condiciones) pero para cada regla disparada llama `runner.start(rule, trigger_text)`, con `trigger_text = f"{rules.METRICS[metric]} al {reading:.0f} %"` para condiciones y `""` si no. `engine._run` desaparece; `engine.stop` también llama `runner.stop`.

- [ ] **Step 1: Write the failing tests:** `tick` con una regla diaria llama a `runner.start` (falso) una vez; con una condición pasa el `trigger` correcto; un `runner.start` que lanza no frena a las demás; las pruebas de «perdidas» y de reglas rotas siguen igual.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** `uv run pytest tests/routines -v` → PASS.
- [ ] **Step 5: Commit** `refactor: engine launches chains through the runner`.

### Task 6: Ejecutor y catálogo en el núcleo

**Files:** Modify `core.py` (cerca de `_execute`, línea ~1071, y `Reply`), `tools.py` si hace falta un helper; Test `tests/test_core_tools.py`

**Interfaces:**
- Consumes: `permissions.decide(scope, tool_id, hash)`, `AI_RUNNERS`, `_execute_connector`, `connector_registry`.
- Produces:
  - `Reply.failed: bool = False`; `_nav` lo pone en `True` ante `NavegadorError`; `_volume_cmd`, `_music_cmd` y `_windows_cmd` lo ponen ante sus errores (buscar los `except` de `core.py:501`); no cambia lo que ve el modelo.
  - `core.routine_catalog() -> list[dict]`: pasos disponibles ahora: `{"id", "label", "hint", "group", "destructive", "schema"}`. Salen de `tools.ai_tools()` menos `uses_ai`, grupo `rutinas`, `enviar` y las que la persona tiene bloqueadas (`permissions.decide(...) == "block"`), más las de conectores (con `schema`) y `steps.LOCAL`. `destructive` = `tool.ai == "delete"` o `nota_editar`.
  - `core.run_step(tool_id: str, args: str) -> runner.StepResult`: rechaza ids fuera de `routine_catalog()` («Ya no está disponible»), vuelve a decidir permiso con scope `"rutina"` (`block` → falla), llama al runner de `AI_RUNNERS` o a `_execute_connector` (conector: error de hash o de argumentos → `ok=False`), `ok = not reply.failed` y sin excepción; el texto sin el rótulo «Resultado de …».
  - Al importar `core` (o en `main`, donde ya se enchufan los sumideros): `runner.set_executor(run_step)`.

- [ ] **Step 1: Write the failing tests:** el catálogo incluye `volumen`, `musica`, `nav_abrir`, `avisar`, `esperar` y excluye `resumir`, `enviar`, `rutina_crear`; una herramienta en `deny` no está y `run_step` de ella falla (Review Focus 6); `ask` corre; un conector con hash cambiado falla; un `NavegadorError` simulado da `ok=False`.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** `uv run pytest tests/test_core_tools.py tests/test_core.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: core step executor and routine step catalog`.

### Task 7: `/rutina`, `/rutinas` y la IA con cadenas

**Files:** Modify `core.py` (`_routine_create`, `_routine_preview`, `_routines_cmd`), `tools.py` (`ai_arg`/`ai_help` de «Rutina: crear», `AI_GROUPS["rutinas"]`); Test `tests/test_core.py`

**Interfaces:**
- Consumes: `rules.build(text, now, known)` con `known` = ids del `routine_catalog()`.
- Produces: `/rutina 8:00 | volumen 15 > abrir <link> > esperar 5 > musica play` crea una rutina de 4 pasos; `rutina_crear` de la IA acepta lo mismo y su pregunta de permiso muestra cada paso numerado en un bloque de código (`_fenced`) con `rules.describe`; si no se entiende lo dice y no guarda. `/rutinas` muestra el nombre y la cantidad de pasos. El texto de ayuda de la IA ya no dice «solo puede avisar, volumen o música» y lista los pasos posibles.

- [ ] **Step 1: Write the failing tests:** creación de una cadena por `/rutina`; la vista previa de `rutina_crear` lista los pasos; un paso desconocido no guarda nada; `/rutinas` sigue mostrando las viejas migradas.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** `uv run pytest tests/test_core.py tests/test_tools.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: /rutina and AI create multi-step routines`.

### Task 8: API web

**Files:** Modify `channels/web.py` (`RoutineIn`, rutas ~617-644, `routines_view`); Test `tests/channels/web/test_routines_api.py`

**Interfaces:**
- Produces:
  - `GET /api/routines/steps` → `{"steps": core.routine_catalog()}`.
  - `POST /api/routines` acepta `{"text"}` (como hoy) **o** `{"name", "when", "steps":[{"tool","args"}], "layout"?: {...}, "confirmed": true}`; sin `confirmed` en la forma estructurada → 422 «Falta confirmar los pasos».
  - `PUT /api/routines/{id}/edit` con el mismo cuerpo; exige `confirmed: true` solo si cambian `when` o `steps` respecto de lo guardado (renombrar o cambiar `layout` no) (Review Focus 5); conserva `enabled`.
  - `POST /api/routines/test` con `{"steps": [...], "when_text"?: ...}` y `confirmed: true`: corre `runner.run_chain` sincrónico (función `def`, hilo del servidor) y devuelve `{"results": [{"ok","text"}]}`; mismas reglas de catálogo y permisos.
  - Todas las que cambian algo contestan con `routines_view()`; los 422 llevan el motivo en español. `layout` (`{"<n>": {"x": int, "y": int}}`, coordenadas 0–5000) se guarda en la regla y `view` lo devuelve.
- La validación de `Host`/`Origin` existente cubre las rutas nuevas (no tocar).

- [ ] **Step 1: Write the failing tests:** los cuatro puntos de arriba, más 422 por paso desconocido, por referencia posterior y por más de 10 pasos; `layout` fuera de rango → 422.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** `uv run pytest tests/channels/web -v` → PASS.
- [ ] **Step 5: Commit** `feat: routines web API (catalog, create/edit/test with confirmation)`.

### Task 9: Modal y vista de lista

**Files:** Modify `rutinas.html`, `css/style.css`; Create `js/rutinas-editor.js`; Modify `js/rutinas.js`; Test `tests/channels/web/test_routines_page.py`

**Interfaces:**
- Consumes: `GET /api/routines/steps`, `POST/PUT /api/routines…`, `POST /api/routines/test`.
- Produces: botón «Nueva rutina» → `<dialog>` con nombre, activador (hora, días en botones, o «en N min», o umbral de disco/RAM → arma el texto de `when`), lista de pasos con «＋ agregar paso» (elige del catálogo; para pasos de conector arma un campo por parámetro del `schema`, reutilizando la función de `js/conectores.js` que ya lo hace; para el resto un campo de texto con el `hint`), etiquetas de salida (`{{paso N}}`, `{{disparador}}`) como botones que insertan en el campo con foco, reordenar con subir/bajar, borrar paso. «Guardar» y «Probar ahora» abren primero un panel de confirmación con la lista exacta de pasos (`textContent`) y marcan los destructivos; recién ahí mandan `confirmed: true`. Cada rutina de la lista tiene «Editar» (abre el mismo modal). Todo con clases CSS, sin `style=` en el HTML ni `innerHTML`.

- [ ] **Step 1: Write the failing tests** (HTML estático): `rutinas.html` tiene el botón y el `<dialog>`; carga `rutinas-editor.js`; ningún `style=` ni `innerHTML` en los JS nuevos; la barra de arriba y el menú no cambiaron (test existente que los compara).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** `uv run pytest tests/channels -v` → PASS.
- [ ] **Step 5:** Verificar en el navegador (skill `run`, `uv run epsilon`, http://127.0.0.1:8765/rutinas): crear «Despertador» con 4 pasos, ver el panel de confirmación, probarla con pasos inocuos (`avisar`, `esperar 2`), guardar, editar, recargar. Con la ventana angosta (≈ 400 px) todo se usa sin scroll horizontal.
- [ ] **Step 6: Commit** `feat: new-routine modal with list editor`.

### Task 10: Pizarra de nodos

**Files:** Create `js/rutinas-canvas.js`; Modify `rutinas.html`, `css/style.css`, `js/rutinas-editor.js`

**Interfaces:**
- Consumes: el mismo arreglo de pasos y `layout` del editor de la Task 9 (única fuente de datos en memoria: `editor.state = {name, when, steps, layout}`); `editor.onChange(fn)`.
- Produces: con ancho ≥ 900 px el modal muestra la pizarra (SVG, nodos arrastrables con `pointer events`, activador a la izquierda, cada paso conectado al anterior por una línea, línea punteada desde el paso del que sale cada `{{paso N}}` usado); con menos, la lista. Cambiar el tamaño de la ventana alterna sin perder lo escrito. Nodos nuevos se ubican solos (x = 160·índice) y sin `layout` todo se auto-acomoda. Mover nodos guarda `layout` y no pide confirmación. Sin librerías externas.

- [ ] **Step 1: Write the failing test:** el HTML incluye `rutinas-canvas.js`; los JS no usan `innerHTML`, `style=` ni CDN.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5:** Verificar en el navegador a 1400 px y a 400 px: mismos pasos en ambas vistas, arrastrar un nodo, editar en la lista y verlo en la pizarra, línea punteada al usar `{{paso 2}}`.
- [ ] **Step 6: Commit** `feat: node canvas view for routines (wide screens)`.

### Task 11: Documentación y cierre

**Files:** Modify `CLAUDE.md` (párrafo «Rutinas»: pasos, runner, catálogo, confirmación = permiso, página con modal y dos vistas), `docs/plan-planificador.md`.

- [ ] **Step 1:** Actualizar ambos documentos (sin repetir lo que el código ya dice).
- [ ] **Step 2:** `uv run pytest` completo → todo PASS.
- [ ] **Step 3:** `systemctl --user restart epsilon && systemctl --user is-active epsilon` → `active`; crear en la web «Despertador» a un minuto de ahora con `avisar` y comprobar que llega la notificación y queda la línea en `routines.log`.
- [ ] **Step 4: Commit** `docs: routines with steps`.

---

## Self-review

- **Cobertura de la spec:** activador+cadena (T3-5); cualquier herramienta (T6); salida entre pasos (T2, T4); modal con nombre (T9); dos vistas sincronizadas (T9-10); omitir si la PC no está (no cambia: `GRACE`, T5); sin ramas (no hay tarea); confirmación al crear y al cambiar (T8-9); «Probar ahora» (T8-9); IA con pasos y vista previa (T7); condiciones y `{{disparador}}` (T3, T5); migración (T3); no solapar y 5 min (T4); despertador (T1).
- **Desvíos de la spec, a revisar:** (1) `abrir` NO es una herramienta nueva: ya existe `nav_abrir`, que además lanza Chromium si está cerrado (`navegador.ensure_running`), así que tampoco hace falta el respaldo con `xdg-open`; en texto se acepta el alias `abrir`. (2) `avisar` y `esperar` son pasos propios de las rutinas (en `steps.LOCAL`), no entradas de `tools.py`, para que no aparezcan como comandos ni en el menú de Telegram. (3) En disco se conserva la clave `when` en vez de `trigger`.
- **Tipos:** `StepResult`, `run_chain`, `start`, `run_step`, `routine_catalog`, `render_args` usan los mismos nombres en todas las tareas.
- **Proporción:** el plan es unas 2 veces la spec; los cuerpos de código quedan a quien implementa.
