# Plan: panel de herramientas y contexto

Sigue a `docs/plan-conversations.md`. Objetivo: completar el panel derecho del
mockup: «Herramientas» con la etiqueta de las que usan IA, y «Contexto» con los
archivos y notas de la conversación.

## Decisiones

- **La etiqueta dice «usa IA»**, no el nombre del modelo ni del proveedor: el
  modelo puede cambiar. Va en las filas que gastan tokens: `/resumir` e «Imagen a
  CSV». Es un texto escrito a mano en el HTML; un test cuida que solo esas filas la
  tengan.
- **«Imagen a CSV» aparece como herramienta** (hoy solo existía como píldora). Tocarla
  hace lo mismo que la píldora: abre el selector y deja el ejemplo del pedido.
- **Contexto = lo que la conversación creó o guardó**: imágenes y PDFs recibidos,
  tickets, CSV, notas y resúmenes. Al tocar un ítem se **escribe** el comando útil en
  el cuadro de texto (`/ver nombre` para una nota, `/resumir nombre` para un archivo)
  y **no se ejecuta**. No se muestra el contenido del archivo (exigiría un endpoint
  que lea archivos: más superficie de seguridad).
- **Cómo viaja**: donde el núcleo ya guarda algo, la respuesta lleva `artifacts`
  (`kind` y `path`). La web los anota en la línea del mensaje de Epsilon (solo se
  agrega, como los botones) y la API los devuelve al abrir la conversación.
  Telegram los ignora. Las rutas las genera el servidor y se muestran como texto.
- **Sin registro de herramientas, por ahora** (ver abajo).

## Pasos (cada uno con tests y commit)

1. Panel de Herramientas: fila «Imagen a CSV» y etiqueta «usa IA». **Hecho.**
   La fila y la píldora comparten el atajo (`data-csv`); la etiqueta lleva un tooltip
   («Usa el modelo de IA: gasta tokens») y un test cuida que solo `/resumir` e «Imagen
   a CSV» la tengan, y que el texto no nombre al modelo.
2. Los archivos guardados viajan en la respuesta del núcleo, se anotan en la
   conversación y salen en la API.
3. Sección «Contexto» del panel.

## Límites conocidos

- Solo cuenta lo que se haga de ahora en adelante: las conversaciones ya guardadas
  no tienen estos datos.
- Si un archivo se borra a mano, el panel sigue mostrando su nombre y el comando
  da «no encontré».

## Para más adelante: registro de herramientas

Hoy cada comando está escrito en varios lugares: el texto de ayuda y los
manejadores (`core.py`), el menú de Telegram (`channels/telegram.py`), las filas del
panel (`index.html`) y la lista de ejecución automática (`ALLOWED_COMMANDS` en
`app.js`). Los tests los mantienen alineados.

Un **registro** (`tools.py`) declararía cada herramienta una sola vez (nombre,
descripción, si solo lee, si usa IA, si se ejecuta sola desde un enlace) y de ahí
saldrían la ayuda, el menú de Telegram, `GET /api/tools` (el panel se dibujaría
desde la API) y la lista de ejecución automática (que pasaría al servidor, donde un
test garantiza que sean solo lecturas).

Se decidió **no hacerlo todavía**: no hace falta para la etiqueta ni para el
contexto, y toca el núcleo y Telegram, que funcionan. **Se retoma al agregar la
próxima herramienta real** (voz con Whisper, MCP) o un canal nuevo: ahí agregar un
comando pasaría de tocar 5 archivos a escribir una entrada.
