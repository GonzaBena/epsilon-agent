# Varios modelos y proveedores (Gemini + Ollama) — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada tarea de IA (chat, `/ia`, resúmenes, imagen→CSV) use el modelo que la persona elija entre Gemini y Ollama, con una paleta de modelos en el botón del encabezado y un aviso modal al cambiar de modelo en medio de una conversación.

**Architecture:** Un formato de mensajes propio (`ai/messages.py`) reemplaza los objetos de Gemini en todo el código. Cada proveedor (`ai/providers/gemini.py`, `ollama.py`) lo traduce a su API detrás de una interfaz común; `ai/llm.py` queda como fachada que elige proveedor y modelo según la tarea, anota el gasto y valida lo que el modelo puede hacer. La web suma `GET /api/models`, la paleta (`shell.js`) y un componente de modal.

**Tech Stack:** Python 3 / FastAPI / google-genai / httpx (Ollama), JS sin framework, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-10-21-varios-modelos-design.md`

## Global Constraints

- Ids de modelo `proveedor:modelo` (`gemini:gemini-3.1-flash-lite`, `ollama:llama3.1:8b`). Se parte en el **primer** `:` solo si lo anterior es un proveedor conocido (`gemini`, `ollama`); sin proveedor conocido, el nombre entero es de Gemini y, si tiene `:`, el validador lo rechaza con «Falta el proveedor: usá gemini:… u ollama:…».
- Compatibilidad: con `AI_MODEL` vacío rige `GEMINI_MODEL` (o `llm.DEFAULT_MODEL`); quien no toca nada no nota cambios. Los registros de uso de Gemini siguen guardando el nombre **sin** proveedor; los de Ollama guardan `ollama:<modelo>`.
- Tareas: `chat`, `ask`, `summary`, `csv`. Ajustes (todos `live`): `AI_MODEL`, `AI_MODEL_CHAT`, `AI_MODEL_ASK`, `AI_MODEL_SUMMARY`, `AI_MODEL_CSV` (vacío = el predeterminado) y `OLLAMA_URL` (por defecto `http://127.0.0.1:11434`, solo `http`/`https`).
- Un modelo de Ollama es de **precio conocido y gratis** (0, 0): no bloquea el chat.
- Un proveedor caído no aporta modelos a la paleta ni ofrece «Usar»; solo deja una línea tenue al pie.
- Sin cambio automático de modelo ante un fallo. Cambiar de modelo avisa con un `<dialog>` modal (nunca `window.confirm`), sin bloquear en el servidor.
- Las claves nunca salen del servidor (ni en logs ni en errores): todo mensaje de error pasa por `config.redact` y no incluye el texto crudo de excepciones de los proveedores.
- Lo que la web muestra va con `textContent`; nada de `innerHTML`.
- `max_output_tokens` siempre limitado; el contenido de archivos y notas es DATO.
- Los tests no llaman a ningún modelo ni a Ollama (`conftest.py` los bloquea).
- Mensajes al usuario en español, nombres de código en inglés. UI: «IA», salvo en Configuración y en el botón del modelo.
- Commits en inglés y sin firma de Claude. Después de cada tarea: `uv run pytest -q` verde.

## Review Focus

- Un `.env` viejo con solo `GEMINI_MODEL` y `GEMINI_API_KEY` funciona igual que antes (modelo, precio, chat, resúmenes).
- Un registro de uso viejo (nombres sin proveedor) sigue sumando costo y apareciendo en Uso.
- Ollama apagado: el chat responde un error claro (sin trazas ni URLs con datos), la paleta no lista sus modelos y la prueba de conexión dice qué pasa.
- `ollama:llama3.1:8b` se parte bien; `llama3.1:8b` a secas se rechaza con el mensaje del proveedor faltante.
- Un turno de modelo con `raw` de Gemini se reenvía a Ollama sin usar `raw`, y viceversa (cambio de modelo en una conversación con herramientas ya usadas).
- Un modelo sin herramientas en el chat: no se ofrecen, no se rompe nada, y la barra/respuesta lo explica; un modelo sin visión recibe un PDF o imagen → error claro y no se llama.
- Ninguna clave ni cuerpo de respuesta crudo aparece en los errores de Gemini ni de Ollama.
- Cambiar de modelo en una conversación sin llamadas previas no muestra aviso; con llamadas previas de otro modelo, sí.

---

## File Structure

- Create `src/epsilon/ai/messages.py` — `Message`, `File`, `Call`, `Result`, `Step`, `ToolSpec`.
- Create `src/epsilon/ai/providers/__init__.py` (registro), `base.py` (interfaz y tipos), `gemini.py`, `ollama.py`.
- Modify `src/epsilon/ai/llm.py` — fachada por tarea.
- Modify `src/epsilon/ai/usage.py` — precio por proveedor, último modelo de una conversación.
- Modify `src/epsilon/ai/chat.py`, `ask.py`, `summarize.py`, `extract.py`, `attachments.py`, `core.py` — migran al formato neutro y a las tareas.
- Modify `src/epsilon/settings/config.py`, `checks.py`; `channels/web.py`; `channels/web_static/*.html`, `js/shell.js`, `js/app.js`, `js/config.js`, `css/style.css`; `README.md`, `CLAUDE.md`, `docs/plan-modelos.md`.
- Tests: `tests/ai/test_messages.py`, `tests/ai/providers/test_gemini.py`, `tests/ai/providers/test_ollama.py`, `tests/ai/test_llm.py`, `tests/ai/test_usage.py`, `tests/ai/test_chat.py`, `tests/settings/test_config.py`, `tests/settings/test_checks.py`, `tests/channels/web/test_models_api.py`, `tests/channels/web/test_chat_ui.py`, `tests/conftest.py`.

### Task 1: Mensajes neutros, interfaz de proveedor y proveedor Gemini

Todo código nuevo y sin usar todavía: la suite sigue verde porque nada lo llama.

**Files:**
- Create: `src/epsilon/ai/messages.py`, `src/epsilon/ai/providers/__init__.py`, `base.py`, `gemini.py`
- Test: `tests/ai/test_messages.py`, `tests/ai/providers/__init__.py`, `tests/ai/providers/test_gemini.py` (mover acá `FakeClient` y los casos de `tests/ai/test_llm.py` de `generate`/`chat_tools`/`check`, reescritos contra el proveedor; los de `llm.py` se rehacen en la Task 2)

**Interfaces:**
- Produces (`messages.py`): dataclasses `File(data: bytes, mime: str)`, `Call(name: str, args: str)`, `Result(name: str, result: str)`, `Message(role: str, parts: list[str | File], calls: list[Call], results: list[Result], raw: object | None)` con la propiedad `text -> str` (une las partes `str`); `ToolSpec(name, description, arg_description)`; `Step(text: str, calls: list[tuple[str, str]], turn: Message)`.
- Produces (`providers/base.py`): `Capabilities(tools: bool, images: bool, pdf: bool)`; `GenerateResult(text: str, input_tokens: int, output_tokens: int)`; `ChatResult(step: Step, input_tokens: int, output_tokens: int)`; clase base `Provider` con `name: str` y los métodos `generate(model, parts, prompt, max_tokens, json_mode) -> GenerateResult`, `chat(model, history: list[Message], system: str, tools: list[ToolSpec], max_tokens: int, allow_calls: bool) -> ChatResult`, `check(model) -> str`, `list_models() -> list[str]`, `capabilities(model) -> Capabilities`. Los errores esperables son `ValueError` con mensaje en español y sin claves.
- Produces (`providers/__init__.py`): `get(name: str) -> Provider` (`ValueError("Proveedor de IA desconocido: …")` si no existe), `names() -> list[str]` (`["gemini", "ollama"]` cuando exista Ollama; por ahora `["gemini"]`).

- [ ] **Step 1: Tests que fallan.** `test_messages.py`: `Message("user", ["a", File(b"x","image/png"), "b"]).text == "ab"`; valores por defecto vacíos. `test_gemini.py` (cliente falso `genai.Client` como el de hoy): `generate` devuelve `text` y tokens `(12, 8)` (salida = respuesta + pensamiento); sin `usage_metadata` devuelve `(0, 0)`; traduce `str`→texto y `File`→`Part.from_bytes`; `chat` con llamadas devuelve `Step.calls == [("nombre","arg")]`, `Step.turn.raw` es el contenido original del candidato, y al reenviar un `Message` con `raw` usa ese `raw` tal cual mientras que uno sin `raw` se traduce desde `parts/calls/results` (herramientas como `function_call`/`function_response`); `allow_calls=False` manda `FunctionCallingConfig(mode="NONE")`; `check` sin clave lanza «Falta la clave de Gemini…», con APIError 401 lanza «Gemini rechazó la clave…» sin la clave; `list_models` devuelve los nombres sin el prefijo `models/` solo de los modelos con `generateContent` en `supported_actions`; `capabilities` → todo `True`.
- [ ] **Step 2: Correr** `uv run pytest tests/ai/test_messages.py tests/ai/providers -q` → FALLA (módulos inexistentes).
- [ ] **Step 3: Implementar** `messages.py`, `providers/base.py`, `providers/__init__.py` y `providers/gemini.py` moviendo la lógica de `llm.py` (cliente en variable, `_record_usage` pasa a devolver los tokens, `_explain`). `gemini.py` conserva `DEFAULT_MODEL` fuera (queda en `llm.py`).
- [ ] **Step 4: Correr** los tests nuevos → PASAN; `uv run pytest -q` completo → PASA (el viejo `llm.py` sigue intacto y sus tests también).
- [ ] **Step 5: Commit** `feat: provider-neutral messages and Gemini provider`.

### Task 2: `llm.py` como fachada y migración al formato neutro

**Files:**
- Modify: `src/epsilon/ai/llm.py`, `usage.py`, `chat.py`, `ask.py`, `summarize.py`, `extract.py`, `attachments.py`, `core.py` (solo si usa tipos de Gemini), `tests/conftest.py`
- Test: `tests/ai/test_llm.py` (rehecho), `tests/ai/test_usage.py`, y los helpers de tests que hoy miran objetos de Gemini (`tests/test_core_tools.py::results`, `tests/ai/test_chat.py`, `tests/ai/test_attachments.py`, `tests/test_core_files.py`)

**Interfaces:**
- Consumes: Task 1.
- Produces (`llm.py`): constantes `DEFAULT_MODEL`, `TASKS = ("chat", "ask", "summary", "csv")`; `task(name: str)` (context manager con `contextvars`, como `usage.action`); `parse_model(model_id: str) -> tuple[str, str]`; `model_for(task: str | None = None) -> str` (id completo; por ahora lee solo `GEMINI_MODEL` → `gemini:<nombre>`); `current_model(task: str | None = None) -> str` (alias de `model_for`); `generate(parts: list, prompt: str, max_tokens: int = 2000, json_mode: bool = False) -> str`; `chat_tools(history, system, tools, max_tokens=1500, allow_calls=True) -> Step` (mismas firmas que hoy: los dobles de los tests no cambian); `text_turn(role, text) -> Message`, `parts_turn(role, parts) -> Message`, `results_turn(results: list[tuple[str, str]]) -> Message`; `check(task: str | None = None) -> str`; re-exporta `Step`, `ToolSpec`. La tarea activa se toma de `task(...)`; sin ninguna, es el predeterminado.
- Produces (`usage.py`): `price(model: str) -> tuple[float, float] | None` (`ollama:` → `(0.0, 0.0)`; `gemini:x` o `x` → `PRICES`); `price_known(model)` y todos los cálculos de costo (`_add`, `for_conversation`, el resumen de Uso) pasan por `price`; `stored_name(model_id: str) -> str` (quita `gemini:`, deja `ollama:…`).
- Produces: `summarize.build_part` y `extract` arman `messages.File` (no `types.Part`); `attachments.load` devuelve `str` y `File`; `chat.Turn` suma `task: str = "chat"`; `ask.begin` crea el turno con `task="ask"`; `chat.step` envuelve la llamada en `llm.task(turn.task)`; `summarize.summarize` en `llm.task("summary")`; `extract.img_to_rows` en `llm.task("csv")`.

- [ ] **Step 1: Tests que fallan.** `test_llm.py` (con `providers.get` reemplazado por un proveedor falso o el Gemini con cliente falso): `parse_model("gemini:x") == ("gemini","x")`, `parse_model("ollama:llama3.1:8b") == ("ollama","llama3.1:8b")`, `parse_model("modelo-a") == ("gemini","modelo-a")`; `model_for()` con `GEMINI_MODEL=modelo-z` → `gemini:modelo-z`, con `""` → `gemini:` + `DEFAULT_MODEL`; `generate` anota el uso con el nombre **sin** proveedor (`("modelo-a", 12, 8)`); `task("summary")` cambia el modelo resuelto dentro del bloque y lo restaura fuera; `text_turn("user","hola")` es un `Message` con `text == "hola"`. `test_usage.py`: `price("ollama:qwen3") == (0.0, 0.0)`, `price("gemini:modelo-a")` y `price("modelo-a")` dan el de `PRICES`, `price("otro") is None`; `for_conversation` de una conversación con llamadas de `ollama:qwen3` da costo 0 y `price_known` verdadero. En `test_chat.py`/`test_core_*`: los helpers que leían `.parts[i].text`, `.inline_data` o `.function_response` leen ahora `Message.parts`/`File`/`Message.results`.
- [ ] **Step 2: Correr** `uv run pytest tests/ai/test_llm.py tests/ai/test_usage.py -q` → FALLA.
- [ ] **Step 3: Implementar** la fachada y `usage.price`; migrar `chat`, `ask`, `summarize`, `extract`, `attachments`, `core`. `llm.generate`/`chat_tools` resuelven `provider, model = parse_model(model_for(task_actual))`, llaman a `providers.get(provider)`, anotan `usage.record(usage.stored_name(id), in, out)`. `conftest.py`: además de `llm.generate`/`chat_tools`, bloquear `providers.get` (que falle con «Un test intentó usar un proveedor de IA de verdad») salvo en los tests de proveedores, que lo reemplazan o instancian la clase directo.
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA completo (los tests viejos de Gemini ya viven en `tests/ai/providers/`).
- [ ] **Step 5: Commit** `refactor: llm is a facade over providers with provider-neutral messages`.

### Task 3: Ajustes de modelo por tarea

**Files:**
- Modify: `src/epsilon/ai/llm.py` (`model_for`), `src/epsilon/settings/config.py`, `channels/web_static/config.html`, `README.md`
- Test: `tests/ai/test_llm.py`, `tests/settings/test_config.py`, `tests/channels/web/test_config_page.py`

**Interfaces:**
- Produces: seis `Setting` `live=True`: `AI_MODEL`, `AI_MODEL_CHAT`, `AI_MODEL_ASK`, `AI_MODEL_SUMMARY`, `AI_MODEL_CSV` (validador `_model_id`) y `OLLAMA_URL` (validador `_url`, `default="http://127.0.0.1:11434"`). `llm.model_for(task)`: `AI_MODEL_<TASK>` → `AI_MODEL` → `GEMINI_MODEL` → `DEFAULT_MODEL`, siempre devuelto como `proveedor:modelo`. `llm.default_model() -> str` (el predeterminado sin mirar la tarea).
- `_model_id(value) -> str`: normaliza a `proveedor:modelo`; acepta sin proveedor solo si no tiene `:`; el modelo cumple `[\w.\-:/]{1,100}`; proveedor desconocido → «Falta el proveedor: usá gemini:… u ollama:…» o «Proveedor de IA desconocido»; sin saltos de línea.

- [ ] **Step 1: Tests que fallan.** `model_for("chat")` con `AI_MODEL_CHAT=ollama:qwen3`, `AI_MODEL=gemini:x`, `GEMINI_MODEL=y` → `ollama:qwen3`; sin la de tarea → `gemini:x`; sin `AI_MODEL` → `gemini:y`; sin nada → `gemini:` + `DEFAULT_MODEL`; `model_for(None)` ignora las de tarea. Config: `update({"AI_MODEL": "ollama:llama3.1:8b"})` guarda tal cual; `"gemini-x"` se guarda como `gemini:gemini-x`; rechaza `"llama3.1:8b"`, `"foo:bar"`, `"ollama:"`, `"a\nb"`, 101 caracteres; `OLLAMA_URL` acepta `http://localhost:11434`, rechaza `ftp://x`, `javascript:x`, `x`, saltos de línea. La página de configuración tiene un campo por cada ajuste nuevo (el test existente lo vigila). El README los lista (test existente).
- [ ] **Step 2: Correr** `uv run pytest tests/ai/test_llm.py tests/settings tests/channels/web/test_config_page.py tests/test_tools.py -q` → FALLA.
- [ ] **Step 3: Implementar** validadores, `Setting`s, la sección «Modelos de IA» de `config.html` (copiar el patrón de campos) y las filas del README.
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA.
- [ ] **Step 5: Commit** `feat: per-task model settings`.

### Task 4: Proveedor Ollama

**Files:**
- Create: `src/epsilon/ai/providers/ollama.py`; declarar `httpx` en `pyproject.toml` (ya está instalado como dependencia de `google-genai`; correr `uv add httpx` y comprobar que el conjunto instalado no cambia)
- Modify: `src/epsilon/ai/providers/__init__.py` (registrar), `src/epsilon/settings/checks.py`, `tests/conftest.py`
- Test: `tests/ai/providers/test_ollama.py`, `tests/settings/test_checks.py`

**Interfaces:**
- Consumes: `Provider`, `Message`, `Step`, `Capabilities`, `GenerateResult`, `ChatResult`.
- Produces: `OllamaProvider` (`name = "ollama"`) en `providers.get("ollama")`; lee `OLLAMA_URL` en cada llamada; usa `httpx.Client(timeout=...)` en un único punto (`_request(method, path, json=None, timeout=...) -> dict`) para poder bloquearlo en los tests. `checks.CHECKS["ollama"]`.
- Traducción: `Message`→`{"role": "user"|"assistant", "content": texto, "images": [base64]}`; llamadas del modelo→`tool_calls: [{"function": {"name", "arguments": {"args": …}}}]`; resultados→mensajes `role: "tool"` con `tool_name` y `content`; `system` va como primer mensaje `role: "system"`; las herramientas como `{"type":"function","function":{name, description, parameters}}` con el mismo esquema de un parámetro `args` de texto; `stream: false`, `options.num_predict = max_tokens`, `options.temperature = 0`, `format: "json"` con `json_mode`. Tokens: `prompt_eval_count` y `eval_count` (0 si faltan). Los `File` que no son imagen→`ValueError` («Ollama no lee PDFs…»). `raw` se ignora al enviar y este proveedor no lo escribe.
- `check(model)`: `GET /api/tags` y que `model` esté en la lista (si no: «Ollama está andando pero no tiene el modelo «x». Bajalo con `ollama pull x`.»); `list_models()`: nombres de `/api/tags`; `capabilities(model)`: `POST /api/show` y su lista `capabilities` (`"tools"`→`tools`, `"vision"`→`images`; `pdf` siempre `False`); si el campo no viene, todo `False`.
- Errores: conexión rechazada/timeout → `ValueError("No pude conectar con Ollama en <host:puerto>. ¿Está andando?")` (solo host y puerto, sin ruta ni cuerpo); respuesta HTTP de error → `ValueError` con el código y un texto fijo; nunca el cuerpo crudo.

- [ ] **Step 1: Tests que fallan** (con `_request` reemplazado por un doble que devuelve diccionarios): `generate` arma el cuerpo esperado y devuelve texto y tokens `(10, 4)`; con `json_mode` manda `format: "json"`; una imagen va en `images` en base64 y un PDF lanza «no lee PDFs»; `chat` devuelve `Step.calls == [("nombre","valor")]` desde `tool_calls`, el `Message` del modelo lleva `calls`; el historial con un `Message` de Gemini (`raw` presente) se traduce desde `parts/calls/results` y no usa `raw`; `allow_calls=False` no manda `tools`; `check` con modelo presente devuelve el nombre, ausente lanza el mensaje de `ollama pull`, con `httpx.ConnectError` lanza «No pude conectar con Ollama en 127.0.0.1:11434» sin la ruta; `list_models` y `capabilities` (con y sin el campo `capabilities`); `checks.run("ollama")` devuelve `{"ok": True/False, "detail": …}`. `conftest.py` bloquea `ollama._request` y `httpx.Client` salvo en estos tests.
- [ ] **Step 2: Correr** `uv run pytest tests/ai/providers/test_ollama.py tests/settings/test_checks.py -q` → FALLA.
- [ ] **Step 3: Implementar** `ollama.py`, registrarlo, y `CHECKS["ollama"]` (`"Ollama responde; el modelo X está disponible."` con el modelo predeterminado si es de Ollama, o `"Ollama responde (N modelos)."` si no). La config suma el botón «Probar conexión» de Ollama (`data-test="ollama"`) en `config.html` junto a su URL.
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA.
- [ ] **Step 5: Commit** `feat: Ollama provider`.

### Task 5: Capacidades del modelo y chat sin herramientas

**Files:**
- Modify: `src/epsilon/ai/llm.py`, `chat.py`, `core.py` (donde se arma la respuesta de error), `attachments.py`, `summarize.py`
- Test: `tests/ai/test_llm.py`, `tests/ai/test_chat.py`, `tests/test_core_files.py`

**Interfaces:**
- Produces: `llm.capabilities(task: str | None = None) -> Capabilities` (del proveedor y modelo de la tarea). `llm.generate`/`chat_tools` revisan, **antes** de llamar: si algún `File` es imagen y `images` es falso → `ValueError("El modelo «<id>» no lee imágenes. Elegí otro en el botón del modelo.")`; si es PDF y `pdf` es falso → ídem con «PDFs». `chat.step` no ofrece herramientas (`tools=[]`) si `tools` es falso en la tarea del turno.
- `core` ya captura las excepciones del chat y contesta «No pude responder.»; los `ValueError` de capacidades y de conexión deben llegar con **su mensaje** al usuario (y al registro la versión redactada): ajustar `_chat`/`_loop` para responder `str(e)` cuando el error es `ValueError` y mantener «No pude responder.» para el resto.

- [ ] **Step 1: Tests que fallan:** con un proveedor falso sin `pdf`, `@files/a.pdf` en el chat responde «no lee PDFs» y el modelo no se llama; con uno sin `tools`, `chat.step` llama a `chat_tools` con `tools=[]` y el chat responde con texto; un `ValueError` de conexión llega al usuario tal cual (sin clave: probar con una clave en el mensaje de la excepción cruda y que no salga); una excepción inesperada sigue dando «No pude responder.».
- [ ] **Step 2: Correr** `uv run pytest tests/ai tests/test_core_files.py tests/test_core_tools.py -q` → FALLA.
- [ ] **Step 3: Implementar** lo descrito. `summarize` y `/resumir` ya capturan `ValueError`: comprobar que muestran el mensaje.
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA.
- [ ] **Step 5: Commit** `feat: respect model capabilities (tools, images, PDFs)`.

### Task 6: `GET /api/models` y el modelo actual en `/api/config`

**Files:**
- Modify: `src/epsilon/channels/web.py`
- Test: `tests/channels/web/test_models_api.py`

**Interfaces:**
- Produces: `GET /api/models` → `{"models": [{"id": "gemini:x", "provider": "gemini"}, …], "providers": ["gemini", "ollama"], "errors": {"ollama": "No pude conectar…"}, "default": "gemini:x", "overrides": ["chat"]}`. Consulta `list_models()` de cada proveedor de `providers.names()` **en paralelo** (hilos, tope de ~3 s por proveedor); un proveedor que falla va a `errors` (mensaje ya redactado) y **no** a `models`; `providers` lista los que respondieron bien (aunque no tengan modelos). `overrides` = tareas con `AI_MODEL_<TASK>` definido. `GET /api/config` suma `"model": llm.default_model()` (sin tocar el resto de su respuesta).

- [ ] **Step 1: Tests que fallan:** con proveedores falsos, uno bien (`["a","b"]`) y otro caído: `models` solo trae los del bueno, `errors` el del caído, `providers` solo el bueno; un proveedor que tarda más del tope no cuelga la respuesta; `default` y `overrides` reflejan los ajustes; ninguna clave en la respuesta; `/api/config` incluye `model`; Host/Origin inválidos → 403 como el resto.
- [ ] **Step 2: Correr** `uv run pytest tests/channels/web/test_models_api.py -q` → FALLA.
- [ ] **Step 3: Implementar** el endpoint (sin bloquear el hilo principal más del tope).
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA.
- [ ] **Step 5: Commit** `feat: endpoint listing the available models`.

### Task 7: Botón del modelo y paleta

**Files:**
- Modify: las seis páginas `*.html` (el `<a class="model-chip" …>` pasa a `<button type="button" class="model-chip" id="model-chip" aria-haspopup="dialog" title="Cambiar el modelo de IA">`, igual en todas), `js/shell.js`, `css/style.css`
- Test: `tests/channels/web/test_chat_ui.py` (y el test del encabezado repetido que ya existe)

**Interfaces:**
- Consumes: `GET /api/models`, `GET /api/config` (`model`), `PUT /api/config`.
- Produces (`shell.js`): `window.epsilonModal({title, body, confirm, cancel}) -> Promise<boolean>` (un `<dialog>` armado con DOM y `textContent`, `showModal()`, Escape y clic afuera = cancelar, foco inicial en «Cancelar»); `window.epsilonModelGuard` (la define `app.js` en la Task 8; si no existe, la paleta no avisa); la paleta `openModelPalette()`.
- La paleta: `<dialog>` con `<input>` y `<ul role="listbox">` (accesible: `aria-activedescendant`, flechas, Enter, Escape). Filtra por coincidencia de texto sin distinguir mayúsculas sobre `proveedor:modelo`; el modelo actual aparece marcado. Si lo escrito es `proveedor:modelo` de un proveedor de `providers` (que respondió) y no está en la lista, agrega la fila «Usar `…`». Al pie, una línea tenue por cada entrada de `errors` («Ollama no responde: …») y, si `overrides` no está vacío, «Las tareas con modelo propio no cambian.». Elegir llama a `epsilonModelGuard()` (si devuelve texto de aviso, pide confirmación con `epsilonModal`; «Cambiar igual» sigue), guarda con `PUT /api/config` `{changes: {AI_MODEL: id}}`, actualiza el botón y cierra. El botón muestra `model` de `/api/config` (en vez de leer `GEMINI_MODEL`).

- [ ] **Step 1: Tests que fallan** (lectura de HTML/JS como los demás tests de UI): en las seis páginas el chip es un `<button` con `id="model-chip"` y el mismo markup (el test del encabezado ya compara páginas); `shell.js` llama a `/api/models` y a `showModal`; no usa `innerHTML` ni `window.confirm`; no lee `GEMINI_MODEL`.
- [ ] **Step 2: Correr** `uv run pytest tests/channels/web -q` → FALLA.
- [ ] **Step 3: Implementar** los HTML, la paleta, el modal y los estilos (tokens de color del tema claro/oscuro existentes).
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA.
- [ ] **Step 5: Verificar a mano** (Playwright no tiene Chrome en esta máquina: lo prueba la persona): el botón muestra `gemini:…`; abre la paleta; escribir filtra; con Ollama apagado no aparece y sale la línea tenue; elegir cambia el botón y `AI_MODEL` en Configuración.
- [ ] **Step 6: Commit** `feat: model palette in the header button`.

### Task 8: Aviso de cambio de modelo en una conversación

**Files:**
- Modify: `src/epsilon/ai/usage.py`, `src/epsilon/ai/chat.py`, `channels/web_static/js/app.js`
- Test: `tests/ai/test_usage.py`, `tests/ai/test_chat.py`, `tests/channels/web/test_chat_ui.py`

**Interfaces:**
- Produces: `usage.last_model(conversation_id: str) -> str | None` (el id completo de la última llamada de esa conversación; los nombres viejos sin proveedor se devuelven como `gemini:<nombre>`; se agrega al índice incremental existente, sin releer el registro). `chat.status(conversation_id)` suma `"model_change": {"from": id, "to": id} | None`: `to = llm.model_for("chat")`, `from = usage.last_model(...)`; `None` si no hay llamadas previas o son iguales.
- Produces (`app.js`): `window.epsilonModelGuard = async () => string | null` que devuelve el texto de aviso si la conversación abierta es de modo IA y ya empezó (`aiMode() && started`), y `null` si no. En `submit()`, solo en modo IA y `started`: antes de enviar pide `GET /api/conversations/<id>` y, si `chat.model_change`, abre `epsilonModal` («Enviar igual» / «Cancelar»); cancelar deja el texto en el cuadro sin enviar. El texto del aviso: el modelo nuevo no tiene la charla en caché y recibe todo el historial de nuevo (puede costar más), y los archivos mencionados antes no se reenvían.

- [ ] **Step 1: Tests que fallan:** `last_model` con líneas de `modelo-a` y luego `ollama:qwen3` devuelve `ollama:qwen3`; con una línea vieja sin proveedor devuelve `gemini:modelo-a`; sin llamadas, `None`; el índice se actualiza al agregar líneas. `chat.status`: `model_change` es `None` sin llamadas previas y con el mismo modelo, y `{"from":"gemini:modelo-a","to":"ollama:qwen3"}` tras cambiar `AI_MODEL_CHAT`. UI: `app.js` define `epsilonModelGuard` y llama a `epsilonModal` desde `submit`; no usa `window.confirm`.
- [ ] **Step 2: Correr** `uv run pytest tests/ai tests/channels/web/test_chat_ui.py -q` → FALLA.
- [ ] **Step 3: Implementar** `last_model`, `model_change` y los dos usos del modal.
- [ ] **Step 4: Correr** `uv run pytest -q` → PASA.
- [ ] **Step 5: Verificar a mano** (lo prueba la persona): en un chat de IA con mensajes, cambiar el modelo en la paleta abre el aviso; confirmar cambia el botón; cambiar `AI_MODEL_CHAT` en Configuración y enviar en ese chat abre el aviso antes de enviar.
- [ ] **Step 6: Commit** `feat: warn before changing model in the middle of a conversation`.

### Task 9: Documentación y servicio

**Files:**
- Modify: `CLAUDE.md` (estructura: `ai/messages.py`, `ai/providers/`; `llm.py` como fachada; reglas de modelos y de claves; quitar «Varios modelos…» de Pendiente), `docs/plan-modelos.md` (reescrito: qué quedó hecho y qué falta: Anthropic/OpenAI, archivo de configuración), `README.md` (cómo usar Ollama: `ollama serve`, `ollama pull`, elegir en el botón del modelo), `docs/plan-ui.md` si describe el encabezado

- [ ] **Step 1:** Editar los archivos, en el estilo de las entradas existentes.
- [ ] **Step 2:** `uv run pytest -q` completo → PASA.
- [ ] **Step 3: Commit** `docs: multiple AI providers`.
- [ ] **Step 4:** `systemctl --user restart epsilon` y confirmar con `systemctl --user status epsilon` que quedó activo.

---

## Auto-revisión

- **Cobertura del spec:** mensajes neutros y `raw` (T1, T2); interfaz de proveedor con `list_models`/`check`/`capabilities` (T1, T4); fachada por tarea (T2); ajustes por tarea, predeterminado y compatibilidad con `GEMINI_MODEL` (T3); Ollama por HTTP (T4); precio gratis y registros viejos (T2); límites de cada modelo (T5); `/api/models` y proveedor caído (T6); botón y paleta con «Usar» y línea al pie (T7); `model_change`, modal al elegir y antes de enviar (T7, T8); prueba de conexión de ambos (T4); sección de Configuración (T3, T4); docs y servicio (T9).
- **Consistencia de nombres:** `messages.Message/File/Call/Result/Step/ToolSpec`, `Provider.generate/chat/check/list_models/capabilities`, `providers.get/names`, `llm.task/parse_model/model_for/default_model/current_model/capabilities`, `usage.price/stored_name/last_model`, `epsilonModal`, `epsilonModelGuard`, `model_change` — iguales en todas las tareas.
- **Decisión mía:** `llm.generate`/`chat_tools` conservan sus firmas y la tarea viaja por un `contextvar` (`llm.task`), para que los dobles de los tests y todos los llamadores no cambien de firma; el costo es que la tarea es implícita (como ya lo es `usage.action`).
