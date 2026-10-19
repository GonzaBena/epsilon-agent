# Plan: conversaciones guardadas como archivos

Sigue a `docs/plan-ui.md`. Objetivo: que la web guarde sus conversaciones en el
servidor (en `DATA_DIR`), para llenar «Recientes» y hacer real el «Nuevo chat».

## Decisiones

- **Un archivo por conversación**: `DATA_DIR/conversations/<id>.jsonl`, una línea
  JSON por mensaje. Solo se agrega al final, nunca se pisa (regla del proyecto, y
  el mismo patrón que `usage.jsonl`). Se puede leer, respaldar o borrar a mano.
- **El id lo genera el servidor** (fecha, hora y 4 hex al azar) y nunca sale de
  texto del usuario. Todo acceso pasa por `safe_path`, y la API valida el id con
  un patrón estricto.
- **El archivo se crea con el primer mensaje**, no al abrir un chat nuevo: así
  «Nuevo chat» no deja archivos vacíos.
- **Título** = primera línea del primer mensaje del usuario (40 caracteres).
  «Recientes» se ordena por fecha de modificación, como `/archivos`.
- **Lo que se guarda pasa por `config.redact`** (una clave pegada por error en el
  chat no queda en disco) y se recorta si es enorme.
- **Cada mensaje lleva un id** (8 hex). Un botón tocado se registra como una línea
  que apunta a ese id, y al leer cada mensaje sale con `actions` (vacío si no tiene
  botones), `used` y `chosen` ya resueltos. Así no hay que adivinar en qué mensaje
  se tocó (con dos mensajes pendientes que comparten botón, se resuelve el exacto).
  La primera pulsación gana; una pulsación sobre un botón que ese mensaje no ofrecía,
  o sobre un mensaje que no existe, se ignora.
- **El título se puede cambiar.** Como el archivo es solo de agregar, renombrar
  agrega una línea `title` y gana la última; un título vacío vuelve al automático
  (la primera línea de tu primer mensaje). Es de una sola línea, hasta 80 caracteres,
  y pasa por `config.redact`. Renombrar NO cambia el orden de «Recientes» (se
  conserva la fecha de modificación).
- **El estado pendiente** (CSV esperando confirmar) es por conversación:
  `session_id = web:<id>`.
- **Solo la web.** Telegram no se toca: ya guarda su chat del lado de Telegram y
  no tiene «Recientes». Si algún día se quiere, es una línea en su adaptador.
- **Sin migrar** el historial viejo del navegador (solo eran mensajes en pantalla).
- No gasta tokens: hoy el historial no se manda al modelo.

## Pasos (cada uno con tests y commit)

1. `conversations.py`, sin web: crear id, agregar mensajes y acciones, leer
   (resolviendo qué botones se usaron), listar, y validar ids.
2. API de la web: listar, abrir, crear, renombrar, y guardar cada mensaje y su
   respuesta (el cliente manda el id del mensaje al tocar un botón). **Hecho.**
   - `GET/POST /api/conversations`, `GET /api/conversations/{id}`,
     `PUT /api/conversations/{id}/title`.
   - `/api/message` y `/api/upload` aceptan `conversation_id` (opcional: sin él no
     se guarda nada, como antes). La respuesta trae `message_id`, el id del
     mensaje de Epsilon; al tocar un botón el cliente manda ese `message_id`.
   - Un id inválido: 422 al escribir, 404 al leer o renombrar. Nunca se arma una
     ruta con lo que llega por la URL.
   - Cada conversación tiene su estado pendiente (`session_id = web:<id>`).
   - Verificado de punta a punta con un reinicio real del proceso (23 comprobaciones).
3. Interfaz: «Recientes» real, abrir una conversación, renombrarla, historial leído
   del servidor en lugar del navegador. **Hecho.**
   - El navegador solo recuerda CUÁL conversación tenías abierta (`epsilon.current`);
     el contenido viene del servidor. El historial viejo (`epsilon.history`) se borra.
   - «Recientes» es la lista real, con un lápiz en cada fila (al pasar el mouse, con
     el foco del teclado, y siempre en pantallas táctiles). Enter guarda, Escape y
     soltar el foco cancelan; vacío vuelve al título automático.
   - Las conversaciones son enlaces `/?c=<id>`: en el chat se abren sin recargar y
     desde la configuración llevan al chat con esa conversación abierta. Un id con
     forma inválida en la URL se ignora.
   - El patrón del id está escrito en `app.js` y en `conversations.py`; un test vigila
     que coincidan.
   - Si cambiás de conversación mientras esperás una respuesta, esa respuesta no se
     dibuja en la otra (el servidor igual la guarda en la suya).
   - Si no hay servidor para crear la conversación, el chat funciona igual, sin guardar.

## Sin verificar

- Cambiar de conversación a mitad de una respuesta (el cuidado está en el código,
  pero no se probó con una respuesta lenta).
- Ventana angosta y suavidad de la animación (igual que en `docs/plan-ui.md`).
- Con muchas conversaciones (la API devuelve las 50 más recientes; no hay «ver más»).

## Después

- Memoria para quien quiera conversar con un modelo (se apoyará en estos archivos).
- Conversaciones compartidas con Telegram, si hace falta.
