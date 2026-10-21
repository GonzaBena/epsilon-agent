# Plan: permisos de las herramientas de la IA

Diseño completo: `docs/superpowers/specs/2026-10-20-permisos-tools-design.md`. Plan de obra:
`docs/superpowers/plans/2026-10-20-permisos-tools.md`. Este archivo es el contrato corto.

- **Qué es.** En el modo IA (solo web) el modelo puede pedir usar herramientas de Epsilon. El modelo solo PIDE
  (`llm.chat_tools` no ejecuta nada); quien decide y ejecuta es el código (`core._run_calls`). Nada de esto
  existe fuera del modo IA: el chat normal y Telegram no cambian.
- **Qué herramientas.** Las de `tools.TOOLS` con el campo `ai` (`read`, `write` o `delete`). Su id para el
  modelo es `tools.ai_id` (`ai_name` o, si no, `name`; la nota es `nota`). Cada una se ejecuta con
  `core.AI_RUNNERS` (id → función). Lo que devuelven pasa por `config.redact`, se recorta y vuelve marcado como
  dato, nunca como instrucciones.
- **Estados** (`ai/permissions.py::STATES`): `allow` (corre sola), `ask` (pregunta antes) y `deny` (bloqueada).
- **Valores iniciales.** Por tipo: `read` → `allow`, `write` → `ask`, `delete` → `deny`; `ai_default` en
  `tools.py` los pisa (hoy `resumir` arranca en `deny`). Quedan: `ver`, `buscar`, `archivos` y `memoria` permitidas;
  `nota`, `recordar` y `editar` preguntan; `olvidar` y `resumir` bloqueadas.
- **Dónde viven.** `DATA_DIR/permissions.json` (id → estado, por `safe_path`; se escribe por archivo temporal).
  Archivo ausente, roto o con un valor inválido: vale el inicial de esa herramienta. `save` rechaza
  herramientas que no existen o que faltan y estados inválidos, y entonces no escribe nada.
- **Bloqueada = invisible.** `permissions.offered()` deja afuera las `deny`: el modelo ni las ve. Si igual
  pide una (o una que no existe), `decide` da `block`, no se ejecuta y se le contesta «Herramienta no
  disponible.». Los permisos se aplican en código; el prompt no es una defensa.
- **Las tres respuestas** (botones `perm:si`, `perm:siempre`, `perm:no`) cuando una herramienta en `ask` se
  pide: la pregunta muestra la herramienta y sus datos (redactados, hasta 300 caracteres y, si hay más,
  «… (y N caracteres más)»), dentro de un bloque de código que el texto de la IA no puede cerrar: la web
  pasa los mensajes por markdown y así un enlace o una definición no esconden nada. Si antes otra
  herramienta guardó algo en el mismo turno, la pregunta ya lo lleva.
  - **Sí**: corre esta vez.
  - **Sí, por el resto de la conversación**: `permissions.grant` la deja correr sin preguntar en esa
    conversación hasta que Epsilon se reinicie. No pisa un `deny` (se chequea de nuevo al responder).
  - **No**: se corta el turno; no se ejecuta ni se vuelve a llamar al modelo.
- **Tope de vueltas.** Hasta `chat.MAX_TOOL_STEPS` (6) llamadas al modelo por mensaje pueden pedir
  herramientas, más una última que ya no puede (las declaraciones van igual, con `allow_calls=False`): 7
  llamadas como mucho. Si igual pide alguna, no se ejecuta. Por respuesta del modelo se atienden hasta
  `core.MAX_CALLS_PER_STEP` (8) llamadas; las demás reciben «Demasiadas herramientas a la vez.». Cada vuelta
  chequea el límite de costo de la conversación (ver `docs/plan-chat-ia.md`), y también antes de correr una
  herramienta que gasta IA (`/resumir`).
- **La pregunta pendiente vive en memoria** (`Session.pending_tool`): se pierde al reiniciar, y la descarta un
  mensaje nuevo de texto libre en esa conversación (lo que vuelve a hablar con la IA; un comando o una nota no).
  Toda respuesta a un permiso (Sí o No) toma el candado de la conversación: dos a la vez no actúan las dos. Un botón viejo o de otra conversación contesta «Esa pregunta ya no está vigente».
- **Qué ve el modelo.** `chat.begin/step/add_results` arman el historial con sus pedidos y los resultados;
  la salida de las herramientas nunca se guarda como respuesta de la IA.
- **Web.** `/permisos` (`permisos.html`, `js/permisos.js`) lista cada herramienta con su estado (permitir,
  preguntar, bloquear) y el inicial; `GET/PUT /api/permissions` (el PUT guarda todo junto, con candado: un
  guardado a la vez). Cambiar un permiso rige desde el próximo pedido de la IA, sin reiniciar.
- **Fuera por ahora.** MCP, Telegram en modo IA, permisos por conversación guardados en disco.
