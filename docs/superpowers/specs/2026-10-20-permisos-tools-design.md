# Permisos de herramientas para la IA — diseño

Nombre provisorio: «permisos de tools».

## Intención y por qué

El modo IA ya conversa, pero no puede hacer nada con tus datos. La meta es que pueda, por ejemplo:
«buscame el ticket de mi compra de la Nintendo Switch 2» y que la IA busque y lea sola. Antes de
darle herramientas hace falta un sistema de permisos: que la persona decida de antemano qué puede
usar la IA sola, qué le tiene que consultar y qué nunca puede usar, como en los agentes de terminal.

Esta versión entrega las dos cosas juntas: que la IA pueda llamar herramientas **y** los permisos que
las controlan. Sin permisos no se le dan herramientas.

## Qué es

Cada herramienta que la IA puede usar tiene un **estado**:

- **Permitida:** la IA la usa sola.
- **Preguntar:** antes de ejecutarla, la IA te pide permiso en el chat.
- **Bloqueada:** la IA no puede usarla, ni siquiera pidiéndola.

Los permisos valen **solo para la IA, en el modo IA de la web**. Los comandos que escribís vos
(`/recordar`, `/olvidar`…) funcionan igual que hoy, sin preguntas: ahí ya decidiste vos.

### Herramientas de la primera versión

| Herramienta | Qué hace | Tipo | Estado inicial |
|---|---|---|---|
| `/ver` | leer una nota | lectura | permitida |
| `/buscar` | buscar en las notas | lectura | permitida |
| `/archivos` | listar lo guardado (incluye tickets) | lectura | permitida |
| `/memoria` | ver la memoria | lectura | permitida |
| `+ texto` | guardar una nota | escritura | preguntar |
| `/recordar` | anotar en la memoria | escritura | preguntar |
| `/editar` | cambiar una línea de la memoria | escritura | preguntar |
| `/resumir` | resumir una nota o archivo (gasta IA) | escritura | bloqueada |
| `/olvidar` | borrar una línea de la memoria | borrado | bloqueada |

Quedan afuera porque dependen de un adjunto, de un canal o del estado de la sesión: `/ticket`,
`/cancelar`, «Imagen a CSV», `/menu`, `/nuevo`, `/ayuda`. Mi llamada: `/uso` y `/contexto` también
quedan afuera, porque la IA no los necesita para buscar tus cosas.

Hoy no existe ninguna herramienta que borre notas o archivos; la única que borra es `/olvidar`.

### Cuando la IA pide permiso

La pregunta aparece **en el chat** como un mensaje con tres botones. Dice qué herramienta quiere usar y
con qué datos (por ejemplo, «La IA quiere editar la línea 3 de la memoria: <texto nuevo>»), así se ve
exactamente qué va a hacer. Las respuestas:

- **Sí:** se ejecuta esta vez; la próxima vuelve a preguntar.
- **Sí, por el resto de la conversación:** se ejecuta y no vuelve a preguntar por esa herramienta
  **en esa conversación**. En una conversación nueva vuelve a preguntar. No cambia el ajuste general
  (mi llamada).
- **No:** no se ejecuta, **se corta el turno**: la IA deja de hacer lo que estaba haciendo y el chat
  queda quieto hasta que la persona escriba de nuevo. Queda un aviso en la conversación («Dijiste que no
  a: <herramienta>»), para que se vea por qué se cortó (mi llamada).

La conversación espera mientras la pregunta no tenga respuesta.

### La pantalla de permisos

Una página nueva «Permisos», basada en el mockup que hizo la persona
(<https://claude.ai/artifact/Ai97MRvvTyWRiuWNXbzgeS>, tablero «Permisos»):

- Una fila por herramienta, con un selector de tres botones: **Permitir · Preguntar · Bloquear**.
  Cada fila dice qué tipo es (lectura, escritura, borrado). Las filas van agrupadas (notas, memoria…).
- Un resumen arriba con tres contadores: permitidas, piden confirmación, bloqueadas.
- **Sin perfiles.** El mockup tiene los botones Estricto, Equilibrado y Libre; se descartaron. Hay un
  solo conjunto de valores iniciales: lectura permitida, escritura pregunta, borrado bloqueado, y
  `/resumir` bloqueada a pedido de la persona (es una escritura, pero la pidió bloqueada). Desde ahí se
  cambia herramienta por herramienta.
- Una barra «Tenés cambios sin guardar» con **Descartar** y **Guardar cambios**.
- Si hay cambios sin guardar y la persona intenta salir de la sección (otro enlace del menú o de la barra
  de arriba, atrás, cerrar la pestaña), la página avisa que hay cambios y deja elegir **Guardar** o
  **Descartar** (salir sin guardar), o quedarse en la página para seguir editando (mi llamada: sin una
  tercera salida sería fácil perder los cambios sin querer). Al guardar, se sale recién después de que el
  guardado salió bien; si falla, la persona se queda y ve el error.
- Se agrega «Permisos» a la barra de arriba y al menú de la izquierda, como en el mockup. Mi llamada:
  el mockup tiene otras entradas (Memoria, Docs, Resúmenes…) que no son parte de este trabajo y no se tocan.

## Objetivos

- La IA puede llamar a las herramientas de la tabla durante una conversación de modo IA.
- Cada llamada respeta el estado de la herramienta: se ejecuta, se pregunta o se rechaza.
- Los permisos se editan desde la web y quedan guardados; sobreviven al reinicio.
- Un permiso mal puesto nunca puede abrir más de lo que la persona eligió: ante la duda, se pregunta o
  se bloquea.

## No objetivos (esta versión)

- MCP, plugins y herramientas de terceros. Más adelante entrarán por este mismo sistema.
- Telegram: la IA libre sigue siendo solo de la web.
- La sección de «carpetas accesibles», Obsidian y Voz del mockup. Todo acceso a archivos ya pasa por
  `safe_path` y las herramientas solo tocan `DATA_DIR`.
- Permisos por conversación guardados (el «por el resto de la conversación» es solo mientras dura la
  conversación abierta). Mi llamada, para no inventar otra capa de configuración.
- Que la IA use los comandos con adjuntos.

## Anti-objetivos

- Que la IA pueda ejecutar algo bloqueado, o algo en «preguntar» sin que se haya contestado «sí».
- Que una orden escondida en una nota, un PDF o un ticket (que son DATO, nunca instrucciones) logre
  que la IA haga algo que la persona no autorizó. Los permisos se aplican **en el código**, no en el
  prompt: el modelo no decide si está permitido.
- Gastar de más: leer no gasta IA, y cada herramienta que gasta (`/resumir`) pasa por el límite de
  costo de la conversación como cualquier llamada al modelo.

## Restricciones del proyecto que se mantienen

- Un solo lugar llama al modelo: `ai/llm.py`.
- Los comandos de `tools.py` son la fuente única; `core.COMMANDS` hace el trabajo. Un test vigila que
  coincidan.
- Todo valor que va al `.env` o a un archivo pasa por validación; lo que se guarda pasa por
  `config.redact`.
- La web solo escucha en 127.0.0.1 y valida Host y Origin en cada request; los endpoints nuevos también.
- Lo que la web muestra va como texto (`textContent`). El pedido de permiso muestra los datos de la IA
  como texto, nunca como HTML.
- Los tests no llaman al modelo.

## Lo que decidió la persona

- Tres estados: permitida, preguntar, bloqueada.
- Respuestas al pedido: «sí», «sí por el resto de la conversación», «no».
- «No» corta el turno y espera el próximo mensaje.
- Estado inicial: lectura permitida, escritura pregunta, borrado bloqueado, y `/resumir` bloqueada.
- Sin perfiles: un solo conjunto de valores iniciales.
- Al salir de la pantalla con cambios sin guardar, avisa y permite guardar o descartar.
- Se ve como el mockup de la pantalla de permisos.

## Lo que queda a criterio de quien construye

- **Cómo la IA llama herramientas:** el mecanismo de llamada a funciones del modelo, que vive en
  `ai/llm.py`. Hoy `chat` solo manda y recibe texto; hay que extenderlo sin romper el límite de costo ni
  la anotación de uso por conversación.
- **Dónde se guardan los permisos:** un archivo propio en `DATA_DIR`, solo se escribe por un módulo
  (como `memory/store.py`), y la ausencia de archivo significa «valores iniciales». Mi llamada: no va al
  `.env`.
- **Cómo se representa «borrado»:** `tools.py` hoy solo tiene `writes`. Falta marcar las herramientas
  que borran para que los valores iniciales salgan de los datos de cada herramienta, no de una lista
  aparte.
- **La espera de la respuesta:** cómo se guarda el pedido pendiente y cómo se vuelve a retomar el turno
  después de contestar (los pedidos de confirmación que ya existen en `core` son el antecedente).
- **Las conversaciones como archivos:** los pedidos y las respuestas de permiso se anotan como líneas
  nuevas del archivo de la conversación (solo se agrega, pasa por `config.redact`). Qué ve el modelo de
  ellos lo decide quien construye, con la regla de siempre: nada de salida de comandos sin necesidad.
- **Texto de la interfaz:** decir «IA», sin nombrar el modelo.
- **Tests:** estado inicial, cada estado, el corte con «no», «por el resto de la conversación» y que lo
  bloqueado nunca corra; todo sin llamar al modelo.
