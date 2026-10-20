# Epsilon

Agente personal para uso propio. Se usa desde una web local (y, si querés, desde un bot de
Telegram) y hace cuatro cosas:

- guarda **notas** y **archivos** en una carpeta tuya;
- pasa **imágenes a CSV** (tickets, planillas, facturas);
- **resume** notas, PDFs e imágenes con IA (Gemini o modelo seleccionado);
- entiende **notas de voz** (opcional, se transcriben en tu PC con Whisper).

Está pensado para ser simple y barato: lo que no necesita IA no la usa, los prompts son cortos
y la respuesta del modelo siempre tiene un tope de tokens. Los datos quedan en tu máquina.

## Instalación

Necesitás [uv](https://docs.astral.sh/uv/) y Python 3.12 o más nuevo.

```bash
uv sync                # instala las dependencias
uv sync --extra voz    # opcional: suma Whisper para las notas de voz
```

## Configuración

1. Copiá `src/epsilon/.env.example` a `src/epsilon/.env`.
2. Cargá al menos `GEMINI_API_KEY` (se saca en [Google AI Studio](https://aistudio.google.com/)).
3. Arrancá Epsilon y, si preferís, seguí la configuración desde la web, en
   <http://127.0.0.1:8765/config>: ahí se editan las claves, el modelo y el puerto, y hay un
   botón «Probar conexión» que no gasta tokens.

| Variable                                                     | Para qué sirve                                                                                                          |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `GEMINI_API_KEY`                                             | Clave de Gemini. Sin ella no se pueden leer imágenes ni resumir.                                                        |
| `GEMINI_MODEL`                                               | Modelo a usar (por defecto, `gemini-3.1-flash-lite`).                                                                   |
| `DATA_DIR`                                                   | Carpeta donde se guardan notas, archivos y conversaciones (por defecto, `~/epsilon-data`). Queda fuera del repositorio. |
| `WEB_PORT`                                                   | Puerto de la web local (por defecto, 8765).                                                                             |
| `TELEGRAM_TOKEN`, `ALLOWED_USER_ID`                          | Opcionales. Con las dos, el bot responde solo a ese usuario de Telegram.                                                |
| `VOICE_ENABLED`                                              | Voz: micrófono de la web y notas de voz de Telegram (por defecto, `si`; `no` la apaga).                                 |
| `VOICE_CONFIRM`                                              | Con `si`, Telegram pregunta «Ejecutar / Cancelar» antes de ejecutar una nota de voz (por defecto, `no`).                |
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

| Comando             | Qué hace                                         |
| ------------------- | ------------------------------------------------ |
| `+ texto`           | Guarda una nota en inbox.                        |
| `/ver [nota]`       | Lee una nota (por defecto, inbox).               |
| `/buscar palabra`   | Busca en las notas.                              |
| `/archivos [texto]` | Lista lo guardado (lo más nuevo primero).        |
| `/resumir nombre`   | Resume una nota o archivo guardado. _Usa IA._    |
| `/ticket`           | Guarda la próxima imagen o PDF en `tickets/`.    |
| `/cancelar`         | Cancela el `/ticket` que quedó en espera.        |
| `/contexto`         | Lista lo que guardaste desde que arrancó el bot. |
| `/uso`              | Tokens y precio gastados, hoy y en total.        |
| `/menu`             | Ajustes del bot con botones (hoy, la voz).       |
| `/nuevo`            | Telegram: empieza una conversación nueva.        |
| `/ayuda`            | Muestra la ayuda.                                |

**Telegram y la web:** lo que hablás por Telegram queda guardado y la web lo muestra como una
conversación aparte (con el ícono y el color de Telegram), que se actualiza sola y es **solo de
lectura**: para responder, usá Telegram. Telegram no avisa cuando borrás un chat, así que para
empezar una conversación de cero escribí `/nuevo` (la anterior queda guardada en disco).

**Imagen a CSV:** adjuntá una imagen con un texto como `gastos: fecha, proveedor, total`.
Epsilon muestra lo que leyó y **espera tu confirmación** antes de guardar (la IA puede
equivocarse al leer imágenes, así que conviene revisar los datos).

Todo lo que se guarda es **crear o agregar**: Epsilon nunca pisa un archivo existente.

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
uv run pytest      # los tests nunca llaman a Gemini ni cargan Whisper
```

Dónde está cada cosa:

- `core.py`: la lógica (comandos, confirmaciones, estado de la sesión). No conoce ningún canal.
- `channels/web.py` y `channels/telegram.py`: traducen entre cada canal y el núcleo.
- `tools.py`: el **registro de herramientas**, la fuente única de los comandos.
- `llm.py` es el único lugar que llama a Gemini; `transcribe.py`, el único que carga Whisper.
- `docs/`: los planes de diseño de cada parte (`plan-*.md`).

### Agregar una herramienta

1. Declarala en `tools.TOOLS` (`src/epsilon/tools.py`): nombre, línea de ayuda, texto del menú
   de Telegram y, si querés que aparezca en el panel de la web, `panel=`.
2. Escribí su función y sumala a `core.COMMANDS`.

De la entrada salen solos la ayuda, el menú de Telegram, el panel de la web (`GET /api/tools`)
y, si es de solo lectura y se declara en `links`, el enlace `/?cmd=...`. Los tests avisan si
`tools.py` y `core.COMMANDS` dejan de coincidir.
