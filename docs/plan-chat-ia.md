# Plan: modo IA (chat con la IA)

Diseño completo: `docs/superpowers/specs/2026-10-20-modo-ia-design.md`. Plan de obra:
`docs/superpowers/plans/2026-10-20-modo-ia.md`. Este archivo es el contrato corto.

- **Modo fijo.** «Chat normal» o «Chat con IA» se elige al crear la conversación (la web manda `mode` con el primer
  mensaje) y no cambia. Una conversación sin línea `meta` (todas las anteriores), de Telegram o normal nunca usa IA libre.
- **Qué ve el modelo.** `ai/chat.py::turns`: pares (tu mensaje, respuesta con `ai: true`) + el mensaje nuevo, todo por
  `config.redact`; más la memoria en el prompt de sistema, marcada como dato. Nunca la salida de comandos ni adjuntos.
- **Costo.** `usage.conversation(id)` anota cada llamada con su conversación (también `/resumir` e imagen→CSV dentro
  de ella); `usage.for_conversation(id)` suma. Contexto = tokens de entrada de la última llamada «Chat».
- **Rendimiento.** `usage.for_conversation` se consulta en cada mensaje; no relee el registro entero: recuerda hasta
  dónde leyó y solo interpreta lo que se agregó (guarda tokens por modelo y aplica el precio al consultar). Se
  rearma de cero si cambia el archivo (otra carpeta de datos, reemplazado o más corto).
- **Límite.** Línea `limit` en el archivo (gana la última); `PUT /api/conversations/{id}/limit` la cambia. El chequeo es
  antes de cada llamada: una respuesta puede pasarse un poco. Chequeo y llamada van bajo un candado por
  conversación (`core._ai_lock`), así dos pedidos a la vez no pasan los dos el límite; las conversaciones no se esperan. Bloqueada = `core` no llama al modelo para texto libre,
  comandos con IA, imagen con pedido ni el botón Resumir; `/ver`, `/memoria`, notas siguen.
- **Sin precio.** Si `usage.PRICES` no tiene el modelo configurado, el límite no se puede medir: todo lo que usa IA en
  modo IA queda bloqueado con un aviso.
- **Fuera por ahora.** Tools (esperan un sistema de permisos), MCP, Telegram.
