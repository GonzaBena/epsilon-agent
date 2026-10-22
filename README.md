<p align="center">
  <img src="docs/img/icon-192.png" alt="Logo de Epsilon" width="96" height="96">
</p>

<h1 align="center">Epsilon</h1>

Agente personal para uso propio. Se usa desde una web local (y, si querés, desde un bot de
Telegram) y hace cinco cosas:

- guarda **notas** y **archivos** en una carpeta tuya;
- pasa **imágenes a CSV** (tickets, planillas, facturas);
- **resume** notas, PDFs e imágenes con IA (Gemini o modelo seleccionado);
- responde **preguntas** a la IA (`/ia pregunta`, o un chat en modo IA desde la web, que además puede usar tus notas y tu memoria);
- entiende **notas de voz** (opcional, se transcriben en tu PC con Whisper).

Está pensado para ser simple y barato: lo que no necesita IA no la usa, los prompts son cortos
y la respuesta del modelo siempre tiene un tope de tokens. Los datos quedan en tu máquina.

## Instalación

Necesitás [uv](https://docs.astral.sh/uv/) y Python 3.12 o más nuevo.

```bash
git clone https://github.com/GonzaBena/epsilon-agent.git
cd epsilon-agent
uv sync                # instala las dependencias
uv sync --extra voz    # opcional: suma Whisper para las notas de voz
```

## Configuración

1. Copiá `src/epsilon/.env.example` a `src/epsilon/.env`.
2. Cargá `GEMINI_API_KEY` (se saca en [Google AI Studio](https://aistudio.google.com/)). Es opcional: sin ella las notas, los archivos, la búsqueda y la memoria funcionan igual; solo no andan los resúmenes, la imagen a CSV ni la IA.
3. Arrancá Epsilon y, si preferís, seguí la configuración desde la web, en
   <http://127.0.0.1:8765/config>: ahí se editan las claves, el modelo y el puerto, y hay un
   botón «Probar conexión» que no gasta tokens.

| Variable                                                     | Para qué sirve                                                                                                          |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `GEMINI_API_KEY`                                             | Clave de Gemini. Sin ella no se pueden leer imágenes ni resumir.                                                        |
| `GEMINI_MODEL`                                               | Modelo de Gemini a usar si no elegiste otro (por defecto, `gemini-3.1-flash-lite`).                                                                   |
| `DATA_DIR`                                                   | Carpeta donde se guardan notas, archivos y conversaciones (por defecto, `~/epsilon-data`). Queda fuera del repositorio. |
| `WEB_PORT`                                                   | Puerto de la web local (por defecto, 8765).                                                                             |
| `TELEGRAM_TOKEN`, `ALLOWED_USER_ID`                          | Opcionales. Con las dos, el bot responde solo a ese usuario de Telegram.                                                |
| `TELEGRAM_ENABLED`                                           | Prende (`si`) o apaga (`no`) el bot. Se cambia desde la página Canales, sin reiniciar.                                  |
| `USD_ARS`                                                    | Opcional: pesos por dólar, cargado a mano. Habilita el botón ARS en la página Uso.                                      |
| `CHAT_LIMIT_USD`                                             | Opcional: límite de costo (US$) con el que nace cada chat con IA; se cambia en cada conversación. Por defecto 0.50.     |
| `WEATHER_CITY`                                               | Opcional: la ciudad que usa `/clima` cuando no escribís otra.                                                           |
| `CHAT_MAX_FILES`                                             | Opcional: archivos que se pueden mencionar con `@` en un mensaje del chat con IA. Por defecto 5.                        |
| `CHAT_MAX_TURNS`                                             | Opcional: cuántos pares (tu mensaje y la respuesta) de la charla se reenvían a la IA en cada mensaje; lo anterior sigue guardado pero no viaja. Por defecto 10. |
| `CHAT_MAX_TURNS_TELEGRAM`                                    | Opcional: lo mismo que `CHAT_MAX_TURNS` pero para la charla con IA de Telegram. Por defecto 5.                          |
| `CHAT_MAX_MB`                                                | Opcional: tamaño total (MB) de esos archivos por mensaje. Por defecto 15.                                               |
| `AI_MODEL`                                                  | Opcional: modelo de IA predeterminado, como `proveedor:modelo` (`gemini:…` u `ollama:…`). Si falta, rige `GEMINI_MODEL`. |
| `AI_MODEL_CHAT`                                             | Opcional: modelo del chat con IA. Si falta, el predeterminado. |
| `AI_MODEL_ASK`                                              | Opcional: modelo de `/ia`. Si falta, el predeterminado. |
| `AI_MODEL_SUMMARY`                                          | Opcional: modelo de los resúmenes. Si falta, el predeterminado. |
| `AI_MODEL_CSV`                                              | Opcional: modelo de imagen a CSV. Si falta, el predeterminado. |
| `OLLAMA_URL`                                                | Opcional: dirección de Ollama. Por defecto `http://127.0.0.1:11434`. |
| `BROWSER_ENABLED`                                             | Navegador: deja que la IA use tu Chromium (por defecto, `si`; `no` la deja sin esa herramienta).                         |
| `BROWSER_GUARD`                                               | Guardia del navegador: con `no` la IA no pregunta por sitios que no son de confianza (siguen los bloqueos y los topes; por defecto, `si`). |
| `ROUTINES_ENABLED`                                            | Rutinas: con `no` Epsilon no cumple ninguna de las programadas con `/rutina` (siguen guardadas; por defecto, `si`). |
| `BROWSER_TRUSTED_SITES`                                       | Sitios donde la IA actúa sin preguntar, separados por coma (por defecto, `music.youtube.com,youtube.com`).              |
| `BROWSER_BLOCKED_SITES`                                       | Sitios que la IA nunca ve ni toca (por defecto, `accounts.google.com,myaccount.google.com,mail.google.com`).           |
| `VOICE_ENABLED`                                              | Voz: micrófono de la web y notas de voz de Telegram (por defecto, `si`; `no` la apaga).                                 |
| `VOICE_CONFIRM`                                              | Con `si`, Telegram pregunta «Ejecutar / Cancelar» antes de ejecutar una nota de voz (por defecto, `no`).                |
| `ASSISTANT_NAME`                                              | Nombre para hablarle por voz: «Hola Meli, ¿qué tengo hoy?» equivale a `/ia ¿qué tengo hoy?`. Varios, separados por coma (se guardan en minúsculas y sin tilde; por defecto, `ia, ai`). |
| `WHISPER_LANGUAGE`, `WHISPER_IDLE_MINUTES`, `WHISPER_DEVICE` | Opcionales, para la voz (idioma, minutos sin uso antes de soltar el modelo de la memoria, `cpu` o `cuda`).              |

Los cambios hechos desde `/config` se guardan en el `.env`, pero **el reinicio es manual**: la
página avisa cuándo hace falta. El modelo de voz (unos 1,6 GB) se baja solo la primera vez que
se usa.

Para probar sin tocar tu configuración real: `EPSILON_ENV_FILE=/ruta/otro.env uv run epsilon`.

## Uso

```bash
uv run epsilon     # o: uv run python -m epsilon.main
```

Abre la web en <http://127.0.0.1:8765> y, si Telegram está configurado, el bot. En el chat
podés escribir un texto, adjuntar una imagen o PDF, o dictar con el micrófono.

| Comando                | Qué hace                                                                  |
| ---------------------- | ------------------------------------------------------------------------- |
| `+ texto`              | Guarda una nota en inbox.                                                 |
| `/ver [nota]`          | Lee una nota (por defecto, inbox).                                        |
| `/buscar palabra`      | Busca en las notas.                                                       |
| `/archivos [texto]`    | Lista lo guardado (lo más nuevo primero).                                 |
| `/resumir nombre`      | Resume una nota o archivo guardado. _Usa IA._                             |
| `/ia pregunta`         | Una pregunta suelta a la IA, con tu memoria y sus herramientas. _Usa IA._ |
| `/ticket`              | Guarda la próxima imagen o PDF en `tickets/`.                             |
| `/cancelar`            | Cancela el `/ticket` que quedó en espera.                                 |
| `/contexto`            | Lista lo que guardaste desde que arrancó el bot.                          |
| `/recordar texto`      | Anota un dato o preferencia tuya en la memoria.                           |
| `/memoria`             | Muestra lo que anotaste en la memoria.                                    |
| `/olvidar número`      | Borra una línea de la memoria (pide confirmar).                           |
| `/editar número texto` | Cambia una línea de la memoria.                                           |
| `/fecha`               | Fecha y hora de ahora (sin IA).                                           |
| `/dolar`               | Cotización del dólar hoy: oficial, blue, MEP… (sin IA).                   |
| `/clima [ciudad]`      | Clima ahora y próximos días; sin ciudad, usa `WEATHER_CITY` (sin IA).     |
| `/tareas [todas]`      | Tus tareas pendientes, numeradas (con «todas», también las hechas).       |
| `/todo texto`          | Agrega una tarea pendiente.                                               |
| `/hecho número`        | Marca como hecha la tarea pendiente con ese número.                       |
| `/enviar nombre`       | Te manda por Telegram una nota o archivo guardado (en la web ya está en la biblioteca). |
| `/csv nombre [filtros]` | Consulta un CSV guardado: filtros y, tras una barra vertical, suma, promedio, agrupar… (sin IA). |
| `/sistema [qué]`       | Estado de la máquina: disco, ram, uptime, servicio (sin IA).              |
| `/volumen [valor]` | Volumen de la PC: sin valor dice cómo está; con un número (`40`), `+5`/`-5`, `silenciar` o `activar` lo cambia. Nunca pasa de 100 % (sin IA, usa `wpctl`). |
| `/sonando`             | Qué se está reproduciendo en la PC: título, artista y app (sin IA, por MPRIS). |
| `/musica [acción]` | Controla la música o el video de la PC: `pausa`, `play`, `siguiente`, `anterior`; sin acción alterna play/pausa (sin IA). |
| `/ventanas [activa]` | Ventanas abiertas de la PC con su espacio y la que tiene el foco; con `activa`, solo esa (sin IA, usa `hyprctl`). |
| `/rutina [regla]` | Programa algo que Epsilon hace solo, sin IA: `/rutina 23:00 \| volumen 20`, `lun-vie 09:00 \| avisar Standup`, `en 30m \| avisar la pizza`, `disco>90 \| avisar poco espacio` (también `ram>85`). También `borrar N`, `pausar N`, `seguir N`. Avisa por escritorio y por Telegram. |
| `/rutinas` | Tus rutinas programadas, numeradas, con su próxima vez. |
| `/uso`                 | Tokens y precio gastados, hoy y en total.                                 |
| `/menu`                | Ajustes del bot con botones: la voz y el modelo de IA (entre tus favoritos). |
| `/nuevo`               | Telegram: empieza una conversación nueva.                                 |
| `/chat`                | Telegram: abre una charla con la IA (lo que escribís sin `/` va a la IA; los comandos siguen sin usarla). `/nuevo` la cierra. |
| `/ayuda`               | Muestra la ayuda.                                                         |

**Páginas de la web:** arriba están **Chat**, **Canales**, **Uso** y **Permisos**; en el menú de la izquierda, **Biblioteca** y, abajo de todo,
**Configuración**. En **Biblioteca** ves tus notas, archivos y tickets en una cuadrícula, con buscador y filtros por
tipo; al tocar uno se abre ahí mismo y un botón abre su carpeta en tu gestor de archivos. Es solo para ver, no cambia nada. En **Canales** ves el estado de cada canal y podés **apagar y prender el bot de
Telegram sin reiniciar nada** (queda así aunque reinicies el servicio; la web no se puede apagar
desde sí misma). En **Uso** ves cuántos tokens y cuánto dinero gastó la IA: hoy, esta semana, este
mes y en total, más la lista de las peticiones de hoy (con hora, acción y canal). Para ver el gasto
también en pesos, cargá la cotización en Configuración (`USD_ARS`).

**Telegram y la web:** lo que hablás por Telegram queda guardado y la web lo muestra como una
conversación aparte (con el ícono y el color de Telegram), que se actualiza sola y es **solo de
lectura**: para responder, usá Telegram. Telegram no avisa cuando borrás un chat, así que para
empezar una conversación de cero escribí `/nuevo` (la anterior queda guardada en disco).

**Modo IA:** al abrir un chat nuevo en la web podés elegir el modo IA: una conversación libre con la IA, que
recibe lo que escribís, sus respuestas y tu memoria, y puede usar las herramientas de Epsilon (buscar en tus notas,
guardar una nota, anotar en la memoria…). **Vos decidís qué puede hacer sola** en la página **Permisos**: cada
herramienta se permite, pregunta antes o se bloquea (por defecto, leer corre sola, escribir pregunta y borrar está
bloqueado). Cada chat tiene un límite de costo en dólares (`CHAT_LIMIT_USD`) y, al llegar, se frena lo que usa IA.

**Hablarle por voz:** si empezás un audio con «Hola ia, …» (o con el nombre que pongas en `ASSISTANT_NAME`), lo
que sigue se manda como un `/ia`: «Hola ia, ¿tengo algo anotado de filamento?».

**Canales de terceros:** otros canales pueden sumarse como plugins, una carpeta en `DATA_DIR/plugins/<nombre>/`
con su `plugin.toml`. Aparecen apagados en **Canales** y prenderlos pide confirmación, porque corren dentro de
Epsilon y ven tus claves (ver `docs/plan-plugins.md`).

**Imagen a CSV:** adjuntá una imagen con un texto como `gastos: fecha, proveedor, total`.
Epsilon muestra lo que leyó y **espera tu confirmación** antes de guardar (la IA puede
equivocarse al leer imágenes, así que conviene revisar los datos).

Todo lo que se guarda es **crear o agregar**: Epsilon nunca pisa un archivo existente.
La única excepción es **eliminar una conversación** (el tacho en «Recientes», con confirmación): borra su
archivo de verdad y no se puede deshacer. Lo que esa conversación guardó en tus notas y archivos queda donde está.

### Correrlo como servicio

`deploy/epsilon.service` es un servicio de usuario de systemd. Cambiá las dos rutas del archivo
por las de tu máquina y:

```bash
systemctl --user enable --now /ruta/a/deploy/epsilon.service
systemctl --user status epsilon
journalctl --user -u epsilon -f      # logs
systemctl --user restart epsilon     # después de cambiar el código o la configuración
```

### Ahorrar espacio si no usás voz

La voz es opcional y el modelo (~1,6 GB) solo se baja con el primer audio. Si no la querés:

- **Nunca la instales:** con `uv sync` a secas no se baja nada, y un audio responde cómo activarla.
- **Si ya la instalaste y no la usás:** apagala (`VOICE_ENABLED=no`, en `/config` o con `/menu` desde
  Telegram). Apagada, no se descarga ni se usa nada.
- **Para recuperar el disco:** con la voz apagada, `/config` ofrece «Borrar el modelo de voz». Solo
  borra el modelo que Epsilon bajó (nunca uno de otro programa). Para sacar también el paquete,
  corré `uv sync` sin `--extra voz`.

## Seguridad

- La web escucha **solo en 127.0.0.1** y no tiene login: es para uso personal en tu máquina
  (desde el celular se usa Telegram). Cada pedido valida `Host` y `Origin`. No la expongas a la
  red sin agregarle contraseña y HTTPS.
- El bot de Telegram solo responde al `ALLOWED_USER_ID`, también en los botones.
- Las claves **nunca salen del servidor**: la web solo sabe si están cargadas y su largo, y los
  logs y mensajes de error las ocultan.
- El contenido de imágenes, PDFs y notas se trata como **dato**, nunca como instrucciones.
- Todo acceso a archivos pasa por una ruta segura dentro de `DATA_DIR`.

## Desarrollo

```bash
uv run pytest      # los tests (carpeta tests/) nunca llaman a un modelo ni cargan Whisper
```

Dónde está cada cosa:

- `core.py`: la lógica (comandos, confirmaciones, estado de la sesión). No conoce ningún canal.
- `channels/web.py` y `channels/telegram.py`: traducen entre cada canal y el núcleo.
- `tools.py`: el **registro de herramientas**, la fuente única de los comandos.
- `ai/` (los modelos de IA y el gasto), `data/` (notas y conversaciones), `voice/` (Whisper) y `settings/`
  (el `.env` y las pruebas de conexión): una carpeta por responsabilidad. `ai/llm.py` es el único
  lugar que usa el resto del código para hablar con un modelo (`ai/providers/` tiene uno por proveedor); `voice/transcribe.py`, el único que carga Whisper.
- `tests/`: los tests, con las mismas carpetas que el código.
- `docs/`: los planes de diseño de cada parte (`plan-*.md`).

### Agregar una herramienta

1. Declarala en `tools.TOOLS` (`src/epsilon/tools.py`): nombre, línea de ayuda, texto del menú
   de Telegram y, si querés que aparezca en el panel de la web, `panel=`.
2. Escribí su función y sumala a `core.COMMANDS`.

De la entrada salen solos la ayuda, el menú de Telegram, el panel de la web (`GET /api/tools`)
y, si es de solo lectura y se declara en `links`, el enlace `/?cmd=...`. Los tests avisan si
`tools.py` y `core.COMMANDS` dejan de coincidir.

## Licencia

[MIT](LICENSE). Podés usar, copiar y modificar Epsilon libremente; solo hay que conservar el aviso de copyright. El archivo
`js/vendor/markdown-it.min.js` es de terceros y conserva su propia licencia (`markdown-it.LICENSE`, también MIT).

## Usar Ollama (modelos locales)

Epsilon puede usar modelos que corren en tu máquina con [Ollama](https://ollama.com), sin clave y sin costo:

1. Tenelo andando (`ollama serve`) y bajá un modelo (`ollama pull llama3.1:8b`).
2. En Epsilon, tocá el botón del modelo (arriba a la derecha) y elegí `ollama:llama3.1:8b`, o escribilo en
   Configuración → «Modelos de IA». Cada tarea (chat, `/ia`, resúmenes, imagen a CSV) puede usar un modelo distinto.
3. Si Ollama está en otra dirección, cargala en `OLLAMA_URL`. «Probar conexión con Ollama» no gasta nada.

Ollama no lee PDFs, y no todos sus modelos usan herramientas o leen imágenes: Epsilon lo avisa en vez de fallar.
