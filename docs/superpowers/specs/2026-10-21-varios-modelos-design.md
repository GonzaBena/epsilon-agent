# Varios modelos y proveedores (Gemini + Ollama) — diseño

Reemplaza el borrador `docs/plan-modelos.md` (se actualiza al terminar).

## Para qué

Hoy `ai/llm.py` solo habla con Gemini. Se quiere **libertad de elección**: usar Gemini ahora, Ollama (local) cuando
se pueda, y **elegir qué modelo atiende cada tarea** (p. ej. uno fuerte para el chat con herramientas y uno barato
para resúmenes). Sumar proveedores después (Anthropic, OpenAI…) tiene que ser agregar un archivo, no rehacer nada.

## Objetivos

- Un modelo por tarea, más uno predeterminado. Tareas: **chat** (modo IA con herramientas), **ask** (`/ia`),
  **summary** (resúmenes), **csv** (imagen→CSV). La persona elige; Epsilon no adivina si algo es pesado o liviano.
- Proveedores iniciales: Gemini (como hoy) y Ollama (local, sin clave).
- Un aviso **muy visible** (un `<dialog>` modal) cuando un cambio de modelo hace que una conversación reenvíe todo su
  historial al modelo nuevo.
- Lo que ya funciona no cambia: permisos de herramientas, límite de costo por conversación, redacción de claves,
  archivos con `@`, página Uso.

## No objetivos

- Anthropic, OpenAI u otros proveedores (después, uno por vez, sobre esta base).
- Cambiar de modelo solo ante un fallo, o elegir según el tamaño o la dificultad de la tarea.
- Elegir el modelo dentro de cada chat (se elige en Configuración; ver «Decisiones mías»).
- Streaming, Whisper (sigue aparte), MCP.
- Leer PDFs con Ollama (ver «Límites de cada modelo»).

## Anti-objetivos

- Que un cambio de modelo gaste sin que la persona lo sepa (por eso no hay cambio automático, y el cambio en medio de
  un chat avisa).
- Que una clave salga del servidor o aparezca en un error (`config.redact` sigue en todo).
- Que un modelo local cuente como «sin precio» y bloquee el chat.

## Diseño

### Mensajes neutros
Hoy el historial son objetos `Content` de Gemini y `Step.turn` es opaco. Pasa a un formato propio (módulo nuevo
`ai/messages.py`): un `Message` con `role` (`user`/`model`), partes de **texto**, **archivo** (`bytes` + tipo),
**llamadas** pedidas (`nombre`, `args`) y **resultados** de herramientas. `Message.raw` guarda, opcional, el turno tal
cual lo devolvió un proveedor: Gemini lo necesita para reenviar sus firmas de razonamiento en las llamadas a
herramientas, así que cada proveedor reutiliza `raw` solo si el turno es suyo y, si no, traduce desde lo neutro.
`summarize.build_part` y `extract` dejan de usar tipos de Gemini y arman partes neutras.

### Proveedores (`ai/providers/`)
Un archivo por proveedor (`gemini.py`, `ollama.py`) que implementa la misma interfaz (`base.py`):
- `generate(model, parts, prompt, max_tokens, json_mode) -> Result(text, input_tokens, output_tokens)`
- `chat(model, history, system, tools, max_tokens, allow_calls) -> Step` (con los tokens)
- `check(model) -> str`: prueba de conexión **sin gasto** (Gemini: `models.get`; Ollama: `GET /api/tags` y que el
  modelo esté en la lista). Lanza `ValueError` con mensaje claro y sin claves.
- `capabilities(model) -> Capabilities(tools: bool, images: bool, pdf: bool)`.
Un registro (`providers.get(name)`) devuelve el proveedor por nombre.

### `llm.py` como fachada
Se mantiene como el **único lugar** que el resto del código usa para hablar con un modelo, ahora con la tarea:
`generate(parts, prompt, ..., task=...)` y `chat_tools(..., task="chat")`. Resuelve el modelo de esa tarea, llama al
proveedor, anota el gasto en `usage` y valida `capabilities` antes de llamar (herramientas, imágenes, PDF) con
mensajes en español. `current_model(task)` devuelve el id completo.

### Qué modelo atiende cada tarea
Ids con la forma `proveedor:modelo` (`gemini:gemini-3.1-flash-lite`, `ollama:qwen3`). Ajustes nuevos, todos `live`:
`AI_MODEL` (predeterminado), `AI_MODEL_CHAT`, `AI_MODEL_ASK`, `AI_MODEL_SUMMARY`, `AI_MODEL_CSV` (vacíos = el
predeterminado) y `OLLAMA_URL` (por defecto `http://127.0.0.1:11434`). Un nombre **sin** proveedor se toma como
Gemini, y si `AI_MODEL` está vacío rige `GEMINI_MODEL` como hasta ahora: quien no toca nada no nota cambios. El
validador exige un proveedor conocido, un nombre sin saltos de línea y una URL `http(s)` válida.

### Costo
`usage.price(model_id)`: Gemini por `PRICES` (como hoy); Ollama es **gratis y de precio conocido** (0, 0). Se anotan los
tokens que informa Ollama. `chat.status` usa el modelo de la tarea `chat`. Los registros viejos (nombre sin proveedor)
se siguen leyendo como Gemini.

### Límites de cada modelo
Antes de llamar, `llm` consulta `capabilities`: si el modelo del chat no soporta herramientas, el modo IA no se las
ofrece (y la barra lo dice); si un archivo de imagen o PDF no lo puede leer el modelo elegido, el pedido responde un
error claro en vez de fallar adentro (resumir un PDF con un Ollama sin visión, `@archivo.pdf` en el chat…).

### Cambiar de modelo en medio de una conversación
- El registro de uso ya anota el modelo de cada llamada de cada conversación. `chat.status` suma
  `model_change: {from, to} | null` cuando el modelo de la tarea `chat` difiere del de la última llamada de esa
  conversación (si no tuvo llamadas, `null`).
- **En el chat:** antes de enviar en una conversación de modo IA, el navegador pide el estado actual y, si hay
  `model_change`, abre un `<dialog>` modal (`showModal`, como el de Permisos) que explica: el modelo nuevo no tiene la
  charla en caché y recibe todo el historial de nuevo (puede costar más), y los archivos mencionados antes no se
  reenvían. Botones **Enviar igual** / **Cancelar**. Es un aviso, no una prohibición: el servidor no bloquea.
- **En Configuración:** al guardar un cambio que altere el modelo de la tarea `chat`, el mismo modal avisa que las
  conversaciones empezadas reenviarán su historial completo; **Guardar** / **Cancelar**.
- Un solo componente de modal para los dos usos. Sin `window.confirm`.

### Web
Configuración suma una sección «Modelos de IA»: predeterminado, uno por tarea, la URL de Ollama y «Probar
conexión» para cada proveedor (`settings/checks.py`: `gemini` y `ollama`). El nombre del modelo en el encabezado
(`shell.js`) muestra el predeterminado. Los textos de la interfaz dicen «IA», no el nombre del modelo, salvo en
Configuración.

## Orden de trabajo (cada paso deja todo andando)
1. Mensajes neutros + proveedor Gemini detrás de la interfaz + fachada con tareas; sin cambios visibles.
2. Ajustes de modelo por tarea y su sección en Configuración.
3. Proveedor Ollama, prueba de conexión, precio gratis y capacidades.
4. Aviso de cambio de modelo (modal en chat y en Configuración).
5. Documentación y servicio.

## Decisiones mías (revisalas)
- El modelo se elige solo en Configuración y las conversaciones lo siguen (no hay selector dentro del chat).
- Ollama se habla por HTTP directo (`httpx`, ya presente), sin sumar una dependencia nueva.
- Los registros de uso de Gemini siguen guardando el nombre sin proveedor; los de Ollama guardan `ollama:<modelo>`.
- No hay cambio automático de modelo ante un fallo: el error es claro y la persona decide.
- El modal aparece en dos lugares (chat y guardar en Configuración).

## Pruebas
Sin llamar a ningún modelo: `conftest.py` bloquea el registro de proveedores y las llamadas HTTP a Ollama. Cubrir:
traducción de mensajes neutros a cada proveedor (incluido `raw` de Gemini), resolución del modelo por tarea y las
compatibilidades (`GEMINI_MODEL`, nombre sin proveedor), validadores, precio gratis de Ollama y que el chat no se
bloquee, `capabilities` (sin herramientas, sin PDF) con mensajes en español, `model_change` en `chat.status`, la prueba
de conexión de ambos proveedores sin gasto (HTTP simulado) y que ninguna clave aparezca en errores.
