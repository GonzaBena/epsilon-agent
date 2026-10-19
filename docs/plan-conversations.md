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
- **Los botones** (Guardar, Resumir...) registran en el archivo cuándo se tocan; al
  reabrir, los ya usados quedan apagados.
- **El estado pendiente** (CSV esperando confirmar) es por conversación:
  `session_id = web:<id>`.
- **Solo la web.** Telegram no se toca: ya guarda su chat del lado de Telegram y
  no tiene «Recientes». Si algún día se quiere, es una línea en su adaptador.
- **Sin migrar** el historial viejo del navegador (solo eran mensajes en pantalla).
- No gasta tokens: hoy el historial no se manda al modelo.

## Pasos (cada uno con tests y commit)

1. `conversations.py`, sin web: crear id, agregar mensajes y acciones, leer
   (resolviendo qué botones se usaron), listar, y validar ids.
2. API de la web: listar, abrir, crear, y guardar cada mensaje y su respuesta.
3. Interfaz: «Recientes» real, abrir una conversación, historial leído del servidor
   en lugar del navegador.

## Después

- Memoria para quien quiera conversar con un modelo (se apoyará en estos archivos).
- Conversaciones compartidas con Telegram, si hace falta.
