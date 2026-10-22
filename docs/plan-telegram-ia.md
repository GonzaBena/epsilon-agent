# Plan: modo IA en Telegram

## Qué se quiere

Hoy en Telegram la IA solo se usa con `/ia pregunta`: cada pregunta es suelta, sin la charla de antes. No se puede
seguir un hilo ni corregir una petición. Se quiere una **charla con IA en Telegram**, parecida a la del modo IA de la web:

- `/chat` abre una conversación de modo IA. Mientras esté abierta, el texto sin `/` va a la IA con el historial.
- Los comandos (`/nota`, `/sistema`, `/dolar`…) siguen corriendo **sin modelo**, como siempre.
- `/nuevo` cierra la charla y se vuelve al modo normal (ya existe). `/ia pregunta` sigue igual.
- El modelo se elige desde `/menu`, solo entre los **favoritos** (los de la web).
- Mismos límites de costo y mismos permisos de herramientas que en la web. El contexto es más corto: 5 pares por
  defecto (ajustable).

No es objetivo: elegir entre todos los modelos desde Telegram, progreso en vivo (Telegram no lo usa), ni tocar la web.

## Cómo encaja (lo que ya existe)

El núcleo ya hace casi todo: `core._is_ai_conversation(msg)` mira `msg.conversation_id`, y en `_dispatch` los
comandos se resuelven **antes** de llegar a `_chat`, así que sin tocar la lógica los comandos no usan modelo. Falta que
Telegram (1) tenga una conversación en modo IA y (2) se la pase al núcleo.

## Pasos

Cada paso termina con los tests en verde y su commit.

1. **`data/conversations.py`: una conversación de canal puede ser de modo IA.**
   - `mode()` hoy solo reconoce `channel == "web"`. Pasa a aceptar también `telegram` (un plugin sigue siendo «normal»).
   - Función nueva `start_channel(name, mode, limit=None)`: cierra la abierta del canal, crea otra con la línea
     `meta` (`{"channel": "telegram", "mode": "ai"}`) y, si hay límite, su línea `limit`.
   - Tests: modo de una de Telegram, un plugin no puede ser `ai`, abrir cierra la anterior.

2. **`ai/chat.py` + `settings/config.py`: contexto propio de Telegram.**
   - Ajuste nuevo `CHAT_MAX_TURNS_TELEGRAM` (por defecto 5, rango 1–100), editable desde `/config` y en `.env.example`.
   - `max_turns(conversation_id)` elige el ajuste según el canal de la conversación; lo usan `turns()` y `status()["trimmed"]`.
   - Tests: Telegram corta a 5, la web sigue en `CHAT_MAX_TURNS`.

3. **`channels/telegram.py`: pasar la conversación al núcleo.**
   - Ayudante `ai_conversation()`: devuelve `conversations.current("telegram")` si esa conversación es de modo IA, o `None`.
   - Se usa en **todos** los lugares donde se arma un `core.Message`: texto, archivos, voz y **botones**. En los botones
     es obligatorio: `_answer_permission` compara el `conversation_id` y, sin él, las preguntas de permiso de la IA
     quedan «vencidas».
   - `log_turn` guarda `ai=reply.ai` en la respuesta. Sin eso `chat._pairs` no reconoce las respuestas de la IA
     y el historial llega vacío.
   - `/chat` (handler nuevo `on_chat`, como `on_new`, porque abrir una conversación es cosa del canal): abre la charla
     y contesta con el modelo actual, el límite y el tope de contexto. Si ya hay una abierta, lo dice y no abre otra.
     Entrada en `tools.py` con `channel="telegram"` y `writes=True`.
   - Tests: con charla IA abierta el mensaje lleva `conversation_id`; cerrada (`/nuevo`), no; la respuesta se anota con
     `ai`; un comando dentro de la charla no llama al modelo (`conftest.py` bloquea el modelo, así que el test lo prueba).

4. **`menu.py`: pantalla «🤖 Modelo».**
   - Nueva entrada en la pantalla principal. Lista los favoritos (`ai/favorites.load()`), con ✅ en el actual.
   - Los modelos tienen nombres largos y el id de un botón admite 64 bytes: el botón lleva un hash corto del nombre
     (`menu:m:<hash8>`) y al tocarlo se busca entre los favoritos de **ese momento**; si ya no está, avisa y no cambia nada.
   - Guarda `AI_MODEL` con `config.update`, igual que el selector de la web.
   - Sin favoritos: «Marcá favoritos desde el selector de modelo de la web».
   - Pie de pantalla: si hay una charla abierta, el próximo mensaje reenvía su contexto al modelo nuevo.
   - Tests: render, tocar uno, favorito que desapareció, id mal formado.

5. **Documentación y cierre.**
   - `CLAUDE.md` (estructura, y sacar «Telegram en modo IA quedó afuera» de Pendiente), `docs/plan-chat-ia.md`,
     ayuda del bot (`HELP` sale de `tools.py`).
   - `uv run pytest` completo y reiniciar el servicio: `systemctl --user restart epsilon` y confirmar que quedó activo.

## Decisiones mías (decime si alguna no te va)

- El modelo del menú cambia `AI_MODEL`, el predeterminado de todo (igual que el selector de la web), no solo el del
  chat. Alternativa: guardar `AI_MODEL_CHAT`, que afecta solo a la charla.
- La charla de Telegram se ve en la web como cualquier conversación de canal, de solo lectura.
- Una charla IA de Telegram nace con el límite `CHAT_LIMIT_USD`, como las de la web.
- No hay botón para volver al modo normal: se usa `/nuevo`.

## Riesgos a vigilar

- Que la web, al ver una conversación de Telegram con `mode: ai`, la deje escribir. Hoy solo escribe en las de `web`;
  hay un test que lo cubra.
- Pegar una ruta que empieza con `/` dentro de la charla muestra la ayuda (limitación ya conocida del modo IA).
- Fotos en la charla: siguen yendo por el flujo de extracción a CSV, no se mandan a la IA como en `@archivo`.
