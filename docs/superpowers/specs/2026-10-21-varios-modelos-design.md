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
- Cambiar el modelo predeterminado desde cualquier página tocando el botón con el modelo actual (arriba a la derecha):
  abre una **paleta de comandos** (modal con un cuadro de búsqueda y la lista de modelos `proveedor:modelo`, que se
  filtra al escribir).
- Un aviso **muy visible** (un `<dialog>` modal) cuando un cambio de modelo hace que una conversación reenvíe todo su
  historial al modelo nuevo.
- Lo que ya funciona no cambia: permisos de herramientas, límite de costo por conversación, redacción de claves,
  archivos con `@`, página Uso.

## No objetivos

- Anthropic, OpenAI u otros proveedores (después, uno por vez, sobre esta base).
- Cambiar de modelo solo ante un fallo, o elegir según el tamaño o la dificultad de la tarea.
- Elegir el modelo dentro de cada chat (se elige en Configuración; ver «Decisiones mías»).
- Un archivo de configuración propio (más adelante; hoy los ajustes siguen en el `.env`).
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
- `list_models() -> list[str]`: los modelos disponibles para elegir (Gemini: `models.list`, los que sirven para
  generar texto; Ollama: `GET /api/tags`). No gasta tokens.
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

### Elegir el modelo: el botón de arriba a la derecha
- El botón con el modelo actual (hoy un enlace a Configuración, repetido en todas las páginas) pasa a ser un botón que
  muestra el predeterminado como `proveedor:modelo` y abre la **paleta de modelos**, igual en todas las páginas
  (código en `shell.js`; el test que vigila el encabezado repetido sigue valiendo).
- La paleta es un `<dialog>` modal con un cuadro de búsqueda y una lista. Al escribir se filtra por coincidencia de
  texto, sin distinguir mayúsculas, sobre `proveedor:modelo`. Flechas para moverse, Enter para elegir, Escape o clic
  afuera para cerrar. Si lo escrito tiene la forma `proveedor:modelo` de un proveedor conocido y no está en la lista,
  aparece una fila «Usar `…`» (un modelo que el proveedor no lista), **solo si ese proveedor respondió**. Todo se pinta
  con `textContent`.
- La lista sale de `GET /api/models` → `{models: [{id, provider}], errors: {proveedor: mensaje}}`, que consulta
  `list_models()` de cada proveedor al abrir la paleta. Un proveedor que falla (Ollama apagado, sin clave) no rompe
  nada: **ninguno de sus modelos aparece** (ni se ofrece «Usar» para él) y, como única señal, la paleta muestra al pie
  una línea tenue con el motivo («Ollama no responde»), sin claves.
- Elegir guarda `AI_MODEL` (el predeterminado) con el `PUT /api/config` de siempre y actualiza el botón. Las tareas
  que tengan modelo propio (`AI_MODEL_*`) no cambian; si hay alguna, la paleta lo dice al pie.

### Cambiar de modelo en medio de una conversación
- El registro de uso ya anota el modelo de cada llamada de cada conversación. `chat.status` suma
  `model_change: {from, to} | null` cuando el modelo de la tarea `chat` difiere del de la última llamada de esa
  conversación (si no tuvo llamadas, `null`).
- **Al elegir en la paleta** estando en una conversación de modo IA ya empezada: antes de guardar se abre un segundo
  `<dialog>` modal que explica que el modelo nuevo no tiene la charla en caché y recibe todo el historial de nuevo
  (puede costar más) y que los archivos mencionados antes no se reenvían. Botones **Cambiar igual** / **Cancelar**.
- **Antes de enviar** un mensaje en una conversación de modo IA, el navegador pide el estado actual; si hay
  `model_change` (el cambio se hizo en Configuración o en otra pestaña) abre el mismo modal con **Enviar igual** /
  **Cancelar**.
- Es un aviso, no una prohibición: el servidor no bloquea. Un solo componente de modal, sin `window.confirm`.

### Configuración
Suma una sección «Modelos de IA»: predeterminado, uno por tarea, la URL de Ollama y «Probar conexión» para cada
proveedor (`settings/checks.py`: `gemini` y `ollama`). Los textos de la interfaz dicen «IA», no el nombre del modelo,
salvo en Configuración y en el botón del modelo.

## Orden de trabajo (cada paso deja todo andando)
1. Mensajes neutros + proveedor Gemini detrás de la interfaz + fachada con tareas; sin cambios visibles.
2. Ajustes de modelo por tarea y su sección en Configuración.
3. Proveedor Ollama, prueba de conexión, precio gratis y capacidades.
4. `list_models`, `GET /api/models` y la paleta de modelos en el botón del encabezado.
5. Aviso de cambio de modelo (modal al elegir en la paleta y antes de enviar).
6. Documentación y servicio.

## Decisiones mías (revisalas)
- El modelo se elige solo en Configuración y las conversaciones lo siguen (no hay selector dentro del chat).
- Ollama se habla por HTTP directo (`httpx`, ya presente), sin sumar una dependencia nueva.
- Los registros de uso de Gemini siguen guardando el nombre sin proveedor; los de Ollama guardan `ollama:<modelo>`.
- No hay cambio automático de modelo ante un fallo: el error es claro y la persona decide.
- La paleta cambia el predeterminado (no el modelo de un chat en particular) y no hay modal al guardar en
  Configuración: el aviso de antes de enviar cubre ese caso.
- La paleta deja escribir un `proveedor:modelo` que no esté en la lista, pero solo de un proveedor que respondió.
- Un proveedor caído deja una línea tenue al pie de la paleta con el motivo (se puede quitar).

## Pruebas
Sin llamar a ningún modelo: `conftest.py` bloquea el registro de proveedores y las llamadas HTTP a Ollama. Cubrir:
traducción de mensajes neutros a cada proveedor (incluido `raw` de Gemini), resolución del modelo por tarea y las
compatibilidades (`GEMINI_MODEL`, nombre sin proveedor), validadores, `list_models` de cada proveedor y `/api/models`
con un proveedor caído, que el botón sea un botón con el modelo en todas las páginas, precio gratis de Ollama y que el chat no se
bloquee, `capabilities` (sin herramientas, sin PDF) con mensajes en español, `model_change` en `chat.status`, la prueba
de conexión de ambos proveedores sin gasto (HTTP simulado) y que ninguna clave aparezca en errores.
