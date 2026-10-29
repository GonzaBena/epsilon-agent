# Streaming de texto (Ollama + web) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la web muestre la respuesta de un modelo Ollama a medida que se escribe, en vez de todo junto al final.

**Architecture:** El proveedor Ollama pide `"stream": true` y avisa cada trozo con un callback `on_text(trozo)`; sigue devolviendo el mismo `ChatResult` al final. El callback viaja por `llm.chat_tools` → `chat.step` → `core._loop`, que lo convierte en el evento `{"type":"text","text": <texto acumulado y redactado>}` por el `on_event` que ya existe. `/api/message/stream` ya reenvía eventos; `app.js` los dibuja en el globo «Pensando» y la respuesta final (`reply`) lo reemplaza, como hoy.

**Tech Stack:** Python 3, httpx (`httpx.stream`), FastAPI `StreamingResponse` (NDJSON, ya existe), JS plano sin build, pytest.

**Spec:** no hay documento aparte; el diseño salió de la conversación del 2026-10-29. Contexto en `CLAUDE.md` («Progreso en vivo», «ai/llm.py») y `docs/plan-modelos.md`.

## Global Constraints

- Los tests nunca llaman a un modelo: todo punto de red nuevo se bloquea en `tests/conftest.py` (como `ollama._request`).
- `ai/providers/ollama.py` es el ÚNICO lugar que habla con Ollama; `ai/llm.py` el único que usa el resto del código para hablar con un modelo.
- Lo que la web muestra va siempre con `textContent`, nunca HTML. El markdown se dibuja solo con la respuesta final.
- Las claves nunca salen del servidor: todo texto que sale hacia la web pasa por `config.redact`.
- Los errores de proveedor son `ModelError` en español, sin ruta ni cuerpo crudo (solo host y puerto).
- Nombres de código en inglés; mensajes al usuario y comentarios en español, con el estilo del código vecino.
- Telegram, Gemini y Anthropic no cambian de comportamiento: aceptan el parámetro y lo ignoran.
- Streaming opt-in: solo si quien llama a `core.handle` lo pide (`stream_text=True`). Los fakes de tests que no lo conocen no se rompen.
- El costo es el mismo (mismos tokens). Verificar con `uv run pytest` antes de cada commit; commits sin firma de Claude (preferencia del usuario).

## Review Focus

- Se corta la conexión a mitad del stream (`httpx.ReadError`/`RemoteProtocolError`): `ModelError` en español sin host/ruta, y nada parcial se guarda en la conversación.
- Un trozo sin `message`, con `content` vacío, o línea en blanco entre trozos: no rompe ni duplica texto.
- Un paso donde el modelo escribe texto y además pide herramientas: el texto parcial se borra del globo al empezar la herramienta y la respuesta final es solo el texto de la última vuelta.
- Una clave partida entre dos trozos (`sk-ab` + `cdef…`) nunca llega al navegador: se redacta el texto acumulado, no cada trozo.
- Modelo con razonamiento (`message.thinking`): ese campo no se mezcla con la respuesta.

---

## File Structure

- Modify `src/epsilon/ai/providers/ollama.py`: `_request_stream` (segundo punto de red) y `chat(..., on_text)`.
- Modify `src/epsilon/ai/providers/base.py`, `gemini.py`, `anthropic.py`: la firma de `chat` acepta `on_text=None` y lo ignora.
- Modify `src/epsilon/ai/llm.py`, `src/epsilon/ai/chat.py`: pasar `on_text` solo si viene.
- Modify `src/epsilon/core.py`: `handle(..., stream_text=False)`, evento `text`.
- Modify `src/epsilon/channels/web.py`: `message_stream` pide `stream_text=True`.
- Modify `src/epsilon/channels/web_static/js/app.js` (+ `css/style.css` si hace falta): dibujar el evento `text`.
- Modify `tests/conftest.py`, `tests/ai/providers/test_ollama.py`, `tests/ai/test_llm.py`, `tests/test_core_flows.py`, `tests/channels/web/test_stream.py`.
- Modify `CLAUDE.md` (sección «Progreso en vivo») al final.

---

### Task 1: Ollama transmite la respuesta por trozos

**Files:**
- Modify: `src/epsilon/ai/providers/ollama.py` (junto a `_request`, y `OllamaProvider.chat`)
- Modify: `src/epsilon/ai/providers/base.py` (`Provider.chat`)
- Modify: `tests/conftest.py:~118` (fixture `block_real_ollama`)
- Test: `tests/ai/providers/test_ollama.py`

**Interfaces:**
- Consumes: `ollama._request` (mapa de errores HTTP), `_messages`, `_tool`, `_tokens`, `parse_call`.
- Produces:
  - `ollama._request_stream(method: str, path: str, body: dict | None = None, timeout: float = 120.0) -> Iterator[dict]`: un dict por línea JSON; mismos `ModelError` que `_request`.
  - `Provider.chat(self, model, history, system, tools, max_tokens, allow_calls, on_text: Callable[[str], None] | None = None) -> ChatResult`.
  - `OllamaProvider.chat` con `on_text`: llama `on_text(trozo)` por cada `message.content` no vacío, en orden.

- [ ] **Step 1: Write the failing tests** en `tests/ai/providers/test_ollama.py`, con un helper `stream_server` (fixture que reemplaza `ollama._request_stream` por un generador sobre una lista de chunks y anota el body):
  - `test_chat_with_on_text_streams_and_returns_the_joined_text`: chunks `[{"message":{"content":"Ho"}}, {"message":{"content":"la"}}, {"message":{"content":""},"done":True,"prompt_eval_count":10,"eval_count":4}]` → `on_text` recibe `["Ho","la"]`; `result.step.text == "Hola"`; `(result.input_tokens, result.output_tokens) == (10, 4)`; el body enviado tiene `"stream": True`.
  - `test_chat_with_on_text_collects_tool_calls_from_any_chunk`: un chunk con `tool_calls=[{"function":{"name":"ver","arguments":{"args":"casa"}}}]` → `result.step.calls` tiene esa llamada.
  - `test_chat_without_on_text_still_uses_the_plain_request`: sin `on_text`, usa `_request` con `"stream": False` (nada cambia para los demás).
  - `test_stream_ignores_thinking_and_chunks_without_message`: chunks `{"message":{"thinking":"hmm"}}` y `{"done":True}` → texto vacío, `on_text` nunca llamado, sin excepción.
  - `test_request_stream_reports_a_dropped_connection_with_only_host_and_port`: importar `_request_stream as real_request_stream` antes de conftest (como `real_request`); `httpx.stream` falso que levanta `httpx.ReadError("…http://127.0.0.1:11434/secreto?clave=1")` → `ModelError` cuyo mensaje contiene `127.0.0.1:11434` y no contiene `secreto` ni `clave`.
  - `test_request_stream_maps_http_errors_like_request`: respuesta 404 con `body["model"]` → mensaje «no está en Ollama».

- [ ] **Step 2: Run** `uv run pytest tests/ai/providers/test_ollama.py -q`. Expected: FAIL (`_request_stream` no existe, `chat` no acepta `on_text`).

- [ ] **Step 3: Implement** `_request_stream` con `httpx.stream(method, url, json=body, timeout=timeout)`; extraer de `_request` el mapeo de `status_code` a `ModelError` a una función `_check(response)` y usarla en las dos (en el stream, `response.read()` antes de mirar el JSON del error); `httpx.HTTPError` durante la conexión **o la lectura** → el mismo `ModelError` «No pude conectar con Ollama en host:puerto». Líneas vacías se saltan; una línea que no es JSON → `ModelError("Ollama respondió algo que no entiendo.")`. En `OllamaProvider.chat`: si `on_text`, `body["stream"] = True`, recorrer `_request_stream`, acumular `message.content` (llamando `on_text` solo con trozos no vacíos), juntar `tool_calls` de todos los chunks y tomar los tokens del chunk con `done`. Armar el mismo `Turn`/`Step`/`ChatResult` que el camino sin stream (compartir ese tramo, no duplicarlo). Agregar `on_text=None` a `Provider.chat` en `base.py`.

- [ ] **Step 4: Bloquear el punto nuevo** en `block_real_ollama` de `tests/conftest.py`: `monkeypatch.setattr(ollama, "_request_stream", forbidden)`.

- [ ] **Step 5: Run** `uv run pytest tests/ai/providers/test_ollama.py -q`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/epsilon/ai/providers/ollama.py src/epsilon/ai/providers/base.py tests/conftest.py tests/ai/providers/test_ollama.py
git commit -m "feat: Ollama streams chat text in chunks through an on_text callback"
```

---

### Task 2: El callback atraviesa la fachada y los otros proveedores

**Files:**
- Modify: `src/epsilon/ai/providers/gemini.py`, `src/epsilon/ai/providers/anthropic.py` (firma de `chat`)
- Modify: `src/epsilon/ai/llm.py:109` (`chat_tools`)
- Modify: `src/epsilon/ai/chat.py:269` (`step`)
- Test: `tests/ai/test_llm.py`, `tests/ai/test_chat.py`

**Interfaces:**
- Consumes: `Provider.chat(..., on_text=None)` de la Task 1.
- Produces:
  - `llm.chat_tools(history, system, tools, max_tokens=1500, allow_calls=True, on_text: Callable[[str], None] | None = None) -> Step`.
  - `chat.step(turn: Turn, on_text: Callable[[str], None] | None = None) -> llm.Step`.
  - Regla: `on_text` se pasa hacia abajo **solo si no es None** (los fakes de tests con firma vieja siguen funcionando).

- [ ] **Step 1: Write the failing tests**:
  - `tests/ai/test_llm.py::test_chat_tools_forwards_on_text_to_the_provider`: proveedor falso cuyo `chat` guarda sus kwargs; con `on_text=f` recibe `on_text=f`.
  - `test_chat_tools_does_not_pass_on_text_when_absent`: proveedor falso SIN el parámetro en su firma; `chat_tools(...)` sin `on_text` no falla.
  - `tests/ai/test_chat.py::test_step_forwards_on_text`: `monkeypatch` de `llm.chat_tools` que anota kwargs; `chat.step(turn, on_text=f)` lo recibe, `chat.step(turn)` no lo manda.
  - `test_gemini_and_anthropic_chat_accept_and_ignore_on_text`: en sus archivos de test existentes, llamar `chat(..., on_text=lambda t: 1/0)` con el cliente falso que ya usan y comprobar que devuelve el mismo `Step` y que el callback nunca se llamó.

- [ ] **Step 2: Run** `uv run pytest tests/ai -q`. Expected: FAIL (parámetro desconocido).

- [ ] **Step 3: Implement** las firmas arriba y, en `llm.chat_tools` y `chat.step`, armar `extra = {"on_text": on_text} if on_text else {}` y pasarlo con `**extra`. Gemini y Anthropic: solo agregan `on_text=None` a la firma, con un comentario de una línea («todavía sin streaming: el texto llega entero»).

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/epsilon/ai tests/ai
git commit -m "feat: on_text callback travels from chat.step to the providers (only Ollama uses it)"
```

---

### Task 3: El núcleo emite el texto acumulado y redactado

**Files:**
- Modify: `src/epsilon/core.py:1157` (`_loop`) y `:1874` (`handle`); contexto junto a `_on_event` (~`:1855`)
- Test: `tests/test_core_flows.py`

**Interfaces:**
- Consumes: `chat.step(turn, on_text=...)` de la Task 2; `_emit`, `config.redact`.
- Produces:
  - `core.handle(msg: Message, on_event=None, stream_text: bool = False) -> Reply`.
  - Evento `{"type": "text", "text": str}`: el texto de la vuelta en curso **hasta ahora**, ya redactado (cada evento reemplaza al anterior; no es un delta).
  - Solo se emite si `stream_text` es verdadero y hay `on_event`.

- [ ] **Step 1: Write the failing tests** en `tests/test_core_flows.py` (modo IA con el `chat_tools` falso que ya usan, ahora con un fake que llama `on_text` antes de devolver):
  - `test_streamed_text_events_are_cumulative`: fake llama `on_text("Ho")`, `on_text("la")` y devuelve «Hola» → los eventos `text` recibidos son `["Ho", "Hola"]` (en `text`), y el `Reply.text == "Hola"`.
  - `test_streamed_text_is_redacted_even_when_a_secret_is_split_across_chunks`: con una clave conocida cargada en config (usar el helper de los tests de `redact`), `on_text("la clave es AB")`, `on_text("CDEF")` → ningún evento contiene la clave completa ni su inicio sensible, y el último `text` tiene el marcador de redacción.
  - `test_no_text_events_without_stream_text`: mismo fake, `handle(msg, on_event=…)` sin `stream_text` → el fake recibió `on_text=None` (o ni lo recibió) y no hay eventos `text`.
  - `test_streamed_text_of_a_tool_step_is_followed_by_the_tool_event`: primera vuelta con texto + una llamada, segunda con texto final → orden de eventos `model, text, tool(running), tool(done), model, text`.

- [ ] **Step 2: Run** `uv run pytest tests/test_core_flows.py -q -k streamed`. Expected: FAIL.

- [ ] **Step 3: Implement**: ContextVar `_stream_text` (bool, default False) junto a `_on_event`; `handle` la fija y la restaura en el mismo `finally` que ya restaura `_on_event`. En `_loop`, antes de `chat.step`, si `_stream_text.get() and _on_event.get()`: `seen = ""` y un `on_text(piece)` que hace `seen += piece` y `_emit(type="text", text=config.redact(seen))`; pasar `on_text` solo en ese caso, y reiniciar `seen` en cada vuelta (cada vuelta crea su propio `on_text`). Un fallo del oyente ya lo traga `_emit`.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/epsilon/core.py tests/test_core_flows.py
git commit -m "feat: core emits cumulative redacted text events when the caller asks to stream"
```

---

### Task 4: `/api/message/stream` pide el texto en vivo

**Files:**
- Modify: `src/epsilon/channels/web.py:650` (`message_stream`)
- Test: `tests/channels/web/test_stream.py`

**Interfaces:**
- Consumes: `core.handle(msg, on_event=..., stream_text=True)` de la Task 3.
- Produces: el NDJSON de `/api/message/stream` puede traer líneas `{"type":"text","text":...}` entre el progreso y el `reply` final. `/api/message` (sin stream) no cambia.

- [ ] **Step 1: Update + write tests**: los `fake_handle`/`boom` existentes pasan a `(msg, on_event=None, stream_text=False)`. Test nuevo `test_stream_asks_core_for_live_text_and_forwards_text_events`: el fake comprueba `stream_text is True`, emite `{"type":"text","text":"Ho"}` y `{"type":"text","text":"Hola"}` → los tipos recibidos son `["text","text","reply"]` y lo guardado en el hilo es solo la respuesta final (`saved_lines(cid)[-1]["text"] == "Hola"`, sin líneas de `text`).
- [ ] **Step 2: Run** `uv run pytest tests/channels/web/test_stream.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**: en `work()`, `core.handle(msg, on_event=events.put, stream_text=True)`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src/epsilon/channels/web.py tests/channels/web/test_stream.py
git commit -m "feat: the web stream endpoint asks the core for live text"
```

---

### Task 5: El chat dibuja el texto a medida que llega

**Files:**
- Modify: `src/epsilon/channels/web_static/js/app.js:635` (`trackProgress`; el globo lo crea `showThinking` ~`:610`)
- Modify: `src/epsilon/channels/web_static/css/style.css` (solo si hace falta ocultar los puntitos)
- Test: no hay tests de JS en el repo; correr los de estructura (`uv run pytest tests/channels -q`) y verificar a mano.

**Interfaces:**
- Consumes: evento `{"type":"text","text": str}` (cada uno reemplaza al anterior).
- Produces: comportamiento del globo: un `<p class="stream">` dentro de `.body`, con `textContent` (nunca HTML) y los puntitos de «Pensando» ocultos mientras haya texto; se borra al llegar un evento `tool` o un `model` con `step > 1`; la respuesta final (`addMessage`) reemplaza el globo como hoy.

- [ ] **Step 1: Implement** el caso `event.type === "text"` en `trackProgress`: crear el `p.stream` si no existe (insertado antes de las líneas de pasos), `textContent = event.text`, marcar el globo para ocultar los puntitos, `scrollToEnd()`. En los casos `tool` y `model` (step > 1) quitar el `p.stream` y mostrar los puntitos otra vez.
- [ ] **Step 2: Run** `uv run pytest -q`. Expected: PASS (los tests de estructura de la web no deben romperse).
- [ ] **Step 3: Verificar en vivo** (skill `run`): reiniciar con `uv run epsilon`, elegir un modelo Ollama (p. ej. `ollama:qwen3.5:4b`), chat nuevo en modo IA, pedir «contame un cuento corto de 5 líneas». Esperado: el texto crece en el globo; al terminar se reemplaza por la respuesta con markdown. Repetir con un pedido que use una herramienta («¿qué hora es?»): el texto parcial desaparece al aparecer «Usando…» y la respuesta final llega completa. Repetir con Gemini: se comporta como antes (todo junto). Tocar el cuadradito a mitad: cancela y se guarda «Cancelé lo que estaba haciendo.».
- [ ] **Step 4: Commit**

```bash
git add src/epsilon/channels/web_static
git commit -m "feat: the chat shows the model's text as it streams in"
```

---

### Task 6: Documentación

**Files:**
- Modify: `CLAUDE.md` (viñeta «Progreso en vivo» y la de `ai/providers/`), `docs/plan-modelos.md` (nota breve)

- [ ] **Step 1:** En «Progreso en vivo» agregar el evento `{"type":"text","text"}` (acumulado y redactado, solo Ollama y solo web con `stream_text=True`; Telegram lo ignora). En `ai/providers/` anotar el segundo punto de red `_request_stream` y que Gemini/Anthropic aceptan `on_text` y lo ignoran. En «Pendiente», agregar «Streaming de texto para Gemini y Anthropic, y para Telegram».
- [ ] **Step 2: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/plan-modelos.md
git commit -m "docs: document live text streaming (Ollama + web)"
```

---

## Fuera de alcance (a propósito)

- Gemini y Anthropic (`generate_content_stream`, `messages.stream`), y Telegram editando el mensaje con el texto.
- Cortar de verdad la conexión al cancelar: hoy `core.cancel` sigue siendo cooperativo; con el callback sería posible, pero es un cambio aparte.
- Dibujar markdown mientras llega.
- Streaming de `llm.generate` (resúmenes, CSV): no tiene sentido en una tarea de un disparo sin chat.

## Self-review

- **Cobertura:** proveedor (T1), fachada (T2), núcleo (T3), endpoint (T4), UI (T5), docs (T6). Las cinco líneas de Review Focus tienen test: corte de conexión y trozos vacíos/`thinking` en T1, clave partida y paso con herramientas en T3, borrado del texto parcial en la verificación en vivo de T5.
- **Consistencia de nombres:** `on_text` (callback de trozos, T1–T3), `stream_text` (bandera de `handle`, T3–T4), evento `text` con campo `text` acumulado (T3–T5), `_request_stream` (T1).
- **Riesgo conocido:** Ollama muy viejo (< 0.8) no devuelve `tool_calls` en modo stream; se detecta en la verificación de T5 con un pedido que use herramientas.
