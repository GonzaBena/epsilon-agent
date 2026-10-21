# Plan: varios modelos y proveedores

**Hecho** (diseño en `docs/superpowers/specs/2026-10-21-varios-modelos-design.md`, plan en
`docs/superpowers/plans/2026-10-21-varios-modelos.md`): Gemini y Ollama, con un modelo por tarea.

- `ai/messages.py` define el formato propio de los mensajes; cada proveedor de `ai/providers/` lo traduce a su API.
- `ai/llm.py` es la fachada: elige proveedor y modelo según la tarea (`chat`, `ask`, `summary`, `csv`), anota el gasto y
  revisa lo que el modelo puede hacer (herramientas, imágenes, PDFs) antes de llamar.
- Los modelos se escriben `proveedor:modelo`. Ajustes: `AI_MODEL` (predeterminado; si falta rige `GEMINI_MODEL`),
  `AI_MODEL_CHAT|ASK|SUMMARY|CSV` y `OLLAMA_URL`.
- El botón del modelo abre una paleta para cambiar el predeterminado; un proveedor caído no aporta modelos.
- Cambiar de modelo en medio de una conversación avisa con un modal (se reenvía todo el historial).
- Los modelos locales cuestan cero y cuentan como precio conocido. No hay cambio automático de modelo ante un fallo.

## Pendiente

- Más proveedores (Anthropic, OpenAI y compatibles): uno por vez, un archivo nuevo en `ai/providers/` + una línea en el
  registro + su prueba de conexión y sus ajustes.
- Un archivo de configuración propio (hoy los ajustes viven en el `.env`).
- La barra del chat no avisa cuando el modelo elegido no usa herramientas (contesta sin ellas).
- Algunos modelos de Ollama dicen soportar herramientas (`capabilities: tools`) y no las usan: `phi4-mini` contesta con
  texto en vez de pedirlas, y `qwen3:1.7b` las pide con el argumento vacío. Con `qwen3.5:4b` y `gemma4:e4b` andan.
  Epsilon se fía de lo que Ollama declara, así que no puede avisar de estos casos.
- Quizá, más adelante, una regla simple por tamaño (p. ej. «PDF de más de N páginas usa el modelo fuerte»).

## Reglas que siguen valiendo

- Las claves nunca salen del servidor (ni a logs ni a errores: `config.redact`); los errores de los proveedores no
  incluyen cuerpos de respuesta crudos ni direcciones completas.
- `max_output_tokens` siempre limitado; el contenido de notas/imágenes es DATO; los permisos de las herramientas
  se aplican en código (`core._run_calls`), no en el prompt.
