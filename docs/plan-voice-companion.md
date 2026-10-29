# Voice Companion: hablarle a Epsilon con un atajo

Apretás un atajo, hablás, y Epsilon te contesta **en voz alta** mientras escribe la respuesta. Puede además hacer cosas por vos
(cambiar de espacio de trabajo, abrir una app, anotar algo, ver tu pantalla). Todo corre en tu PC: la voz se transforma en texto con
Whisper y se habla con Kokoro, los dos locales. Solo el texto va al modelo de IA que elegiste (Gemini, Anthropic u Ollama).

Pensado para Hyprland (Arch / Omarchy), aunque casi todo funciona en cualquier escritorio con PipeWire.
Diseño y decisiones: `docs/superpowers/specs/2026-10-29-voice-companion-design.md`.

## Cómo se usa

1. **Prendelo**: página *Canales* → interruptor *Voice Companion* (viene apagado).
2. **Apretá el atajo** (por defecto `ALT + X`): empieza a grabar y aparece «Escuchando…».
3. **Hablá** y **apretá el atajo otra vez**: corta, transcribe («Dijiste: …»), la IA piensa y te contesta hablando.
4. **Interrumpir**: apretar el atajo mientras habla corta el audio y el turno, y empieza a grabar de nuevo.

Todo el progreso se ve en **la ventanita** (ver abajo): una sola tarjeta que se va actualizando, en vez de notificaciones que se acumulan.
La charla queda guardada en la conversación
*Voice Companion* (la ves en la web, de solo lectura) y su gasto aparece en *Uso* como «Voz».

## Escribirle en vez de hablarle (Alt+C)

Si no tenés micrófono a mano, o tenés que anotar algo sin interrumpirte, apretá **`ALT + C`** (ajuste `VOICE_TEXT_HOTKEY`; vacío lo desactiva):
la ventanita se abre con un campo de texto. Escribís, **Enter**, y ves «Pensando…», las herramientas y la respuesta mientras se escribe, **sin audio**.
`Esc` cierra el campo (`ALT + C` con el campo abierto no hace nada); la respuesta se queda en pantalla hasta que apretás `ALT + C` (la cierra); cerrar la tarjeta no corta el turno (la respuesta queda en la conversación).

- **Sabe dónde estás:** Epsilon lee qué ventana tiene el foco (título, app y espacio) justo antes de abrir el campo y se lo pasa al modelo
  como dato. Así podés decir «recordame agregar esta feature a este proyecto» sin explicar de qué proyecto hablás.
- **Texto primero:** no manda ninguna imagen de entrada. Si el título no alcanza, el modelo puede pedir la captura de pantalla
  (`pc.captura`), que acá corre sola con un modelo de la nube siempre que `IMAGES_TO_CLOUD` esté prendido (con un modelo local, siempre).
- **Mismos permisos que la voz:** lee y escribe sin pisar nada; nunca borra, pisa ni cierra ventanas (modo «YOLO seguro»).
- **Necesita la ventanita** (`VOICE_OVERLAY`): las notificaciones no tienen campo de texto.
- La charla es la misma de la voz; su gasto aparece en *Uso* como «Companion».
- Para probar solo el campo (abre una ventana de verdad; Enter o Esc la cierran):
  `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 -I src/epsilon/voice/overlay_window.py --demo-input`
- Si al cerrar el campo el foco no vuelve a tu ventana de trabajo, cambiá `input_keyboard_mode` a `ON_DEMAND` en `overlay_window.py`.

## La ventanita

Una tarjeta chica anclada a una esquina de la pantalla, **encima de todo y sin robar el foco ni los clics**, que muestra en un solo
lugar lo que va pasando: «Escuchando…», lo que dijiste, «Pensando…», «Usando Abrir una app…», **el texto de la respuesta mientras se
escribe**, los avisos de problemas. Se esconde sola unos segundos después de que termina de hablar.

- **Dónde aparece:** ajuste `VOICE_OVERLAY_POSITION` (Configuración › Voz): arriba-izquierda, arriba-centro, arriba-derecha, centro,
  abajo-izquierda, abajo-centro o abajo-derecha. Vale para la próxima tarjeta, sin reiniciar.
- **Cómo se ve:** `voice.overlay_width`, `voice.overlay_margin`, `voice.overlay_font` y `voice.overlay_hide_seconds` en `settings.json`.
- **Mientras esté viva, las notificaciones callan.** Si no se puede abrir o se cae, vuelven y te dicen por qué (una sola vez).
- Se apaga con `VOICE_OVERLAY=no`.
- Necesita `gtk4-layer-shell` y `python-gobject` (en Arch: `sudo pacman -S gtk4-layer-shell python-gobject`). Corre como un proceso aparte
  con el Python **del sistema**: si se cuelga, Epsilon sigue.
- Para **ver cómo queda sin Epsilon** (abre una ventana unos segundos con una charla de ejemplo; cambiá la esquina al final):
  `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 -I src/epsilon/voice/overlay_window.py --demo abajo-derecha`

## Lo que podés pedirle

- «¿Qué hora es?», «¿cómo está el dólar?», «¿qué tengo anotado de mañana?» (lecturas).
- «Anotá que tengo que comprar pan» (escribe sin pisar nada).
- «Pasame al espacio 3», «mandá esta ventana al 2», «volvé al anterior», «pasame a Firefox».
- «Abrí Spotify», «abrí el navegador» (ver *Apps* abajo).
- «¿Qué dice este error?» (mira tu pantalla; ver *Privacidad*).
- «Poné música para trabajar», «bajá el volumen a 20».

## Instalación

Hace falta la voz de Epsilon (`uv sync --extra voz`: Whisper y Kokoro) y estos programas, que en Arch ya vienen o se instalan con
`pacman`: `pw-record` (PipeWire), `aplay` (alsa-utils), `notify-send` y, para ver la pantalla, `grim`; en Hyprland, `hyprctl`.

### El atajo

Epsilon aplica el atajo en caliente apenas prendés el canal, pero para que **sobreviva a recargar Hyprland** tenés que incluir
**una sola vez** el archivo que escribe en tu carpeta de datos (`DATA_DIR/voice/`):

- Hyprland con config en **Lua** (Hyprland 0.55+, el Omarchy nuevo): en `~/.config/hypr/bindings.lua` agregá
  `dofile(os.getenv("HOME") .. "/epsilon-data/voice/hyprland-voice.lua")` (ajustá la ruta si tu `DATA_DIR` es otro).
- Hyprland **clásico**: en `hyprland.conf`, `source = ~/epsilon-data/voice/hyprland-voice.conf`.
- Otro escritorio: enlazá la tecla que quieras al script `DATA_DIR/voice/epsilon-voice-toggle`.

Para cambiar la tecla: ajuste `VOICE_HOTKEY` (Configuración › Voz), por ejemplo `SUPER, Z`. Epsilon reescribe los archivos y mueve el
atajo sin recargar. Epsilon **nunca** toca el resto de tu config de Hyprland.

### El micrófono

Epsilon graba con el micrófono **predeterminado** de PipeWire; si cambiás de micrófono en el sistema, lo sigue. Si no hay ninguno, el
primer toque te lo dice (y lo dice en voz alta) con cómo arreglarlo: `wpctl status` para ver las entradas y
`wpctl set-default <id>` para fijar una.

## Qué puede y qué no hacer por voz

Por voz nadie puede contestar «¿lo permitís?», así que la conversación corre en modo **«YOLO seguro»**:

| | Por voz |
|---|---|
| Leer (notas, fecha, clima, ventanas, historial…) | corre sola |
| Escribir **sin pisar nada** (anotar, recordar, crear un archivo nuevo, volumen, música, abrir apps, moverse entre espacios) | corre sola |
| Lo que **borra o pisa** (olvidar, borrar una rutina, editar una línea de una nota o de la memoria) | **nunca** |
| **Cerrar una ventana** | **nunca** |
| **Crear rutinas** (después corren solas con permisos «confirmados al crear») | **nunca** |
| Herramientas de **conectores** (pueden facturar o reembolsar) | solo las que vos pusiste en «permitir» |
| Lo que bloqueaste en `/permisos` | sigue bloqueado |

El modelo ni siquiera ve las herramientas que no puede usar. El tope de costo de la conversación sigue valiendo.
En el chat de la web y en Telegram los permisos son los de siempre.

## Apps

La IA abre una app diciendo su **nombre**, nunca un comando. Epsilon lo busca en dos listas cerradas:

1. **Tus alias** (ajuste `PC_APPS`, `nombre=comando` separados por coma), por ejemplo
   `navegador=omarchy-launch-browser, notas=obsidian --new-window`. Ganan sobre todo lo demás.
2. **Las apps instaladas** (los `.desktop`, incluidas las web apps de Omarchy), sin importar acentos ni mayúsculas.

Si dos apps calzan igual de bien («navegador» → Firefox y Chromium) no adivina: te pregunta cuál. Si no la conoce, te dice algunas que
sí puede abrir. Un alias solo puede llevar palabras y rutas (sin `|`, `&`, `;`, `$`, comillas dobles ni comas).

## Privacidad

- El **audio** se graba en una carpeta privada de la memoria (no en disco), se transcribe acá y **se borra apenas se transcribe**.
  Nunca se guarda ni se sube.
- Al modelo le llega **solo el texto** de lo que dijiste (y la memoria y las herramientas de siempre).
- **Ver la pantalla** saca una captura del monitor con el foco y la manda al modelo. Con un modelo de la nube (Gemini, Anthropic) esa
  captura **sale de tu PC**, así que en un chat normal te pregunta primero y **por voz solo corre sola con un modelo local (Ollama)**.
  Si querés que lo haga con uno de la nube, ponela en «permitir» a propósito en `/permisos`. No se ofrece si el modelo no ve imágenes.
- Lo que ve en pantalla o lee de ella es un **dato**, nunca instrucciones.
- Nadie más puede darle órdenes por el atajo: el socket es solo de tu usuario (`0600`) y entiende cuatro palabras (`toggle`, `cancel`, `status`, `ask`).

## Ajustes

| Ajuste | Para qué | Por defecto |
|---|---|---|
| `COMPANION_ENABLED` | prender el canal (página Canales) | `no` |
| `VOICE_HOTKEY` | el atajo | `ALT, X` |
| `VOICE_TEXT_HOTKEY` | el atajo para escribirle (vacío lo desactiva) | `ALT, C` |
| `VOICE_OVERLAY` | la ventanita con el texto en vivo, en vez de notificaciones | `si` |
| `VOICE_OVERLAY_POSITION` | dónde aparece (7 posiciones) | `abajo-derecha` |
| `voice.overlay_width` / `voice.overlay_margin` / `voice.overlay_font` | ancho, distancia al borde y letra de la ventanita (px) | `460` / `24` / `15` |
| `voice.overlay_hide_seconds` | cuánto queda a la vista al terminar | `6` |
| `VOICE_SPEAK` | que conteste en voz alta (con `no`, solo avisa por escrito) | `si` |
| `VOICE_TTS_VOICE` | la voz de Kokoro (`ef_dora`, `em_alex`: español) | `ef_dora` |
| `voice.tts_speed` | velocidad de la voz | `1.0` |
| `voice.max_record_seconds` | cuánto puede grabar antes de cortar solo (hasta 3600) | `180` |
| `voice.max_audio_minutes` / `voice.max_audio_mb` | lo que Whisper acepta: **acotan** el anterior | `10` / `25` |
| `voice.max_turns` | cuántos intercambios anteriores ve la IA | `5` |
| `images.max_side_screen` | lado de las capturas de pantalla (px) | `1600` |
| `PC_APPS` | tus alias para abrir apps | vacío |

Para una charla larga subí `voice.max_record_seconds` **y** `voice.max_audio_minutes` (y quizás `voice.max_audio_mb`): se corta en lo menor de los
tres y el aviso dice cuál subir.

## Si algo no anda

| Síntoma | Qué mirar |
|---|---|
| «No pude grabar: … micrófono predeterminado» | `wpctl status`; fijá una entrada con `wpctl set-default <id>` (y que esté conectada). |
| El atajo no hace nada | ¿Prendiste el canal? ¿Incluiste el archivo en tu config (arriba)? `journalctl --user -u epsilon -f` muestra por qué un atajo no se aplicó. |
| «Epsilon no está corriendo» | El servicio está apagado: `systemctl --user restart epsilon`. |
| Tarda mucho la primera vez | Whisper se carga (~10 s) y, si nunca lo bajaste, se descarga 1,6 GB una sola vez. Empieza a cargarse en cuanto apretás el atajo. |
| No habla | `VOICE_SPEAK`, que `aplay` esté instalado y la voz (`uv sync --extra voz`). Lo avisa una vez. |
| Habla pero la primera oración tarda | Es el modelo: uno local en CPU puede tardar decenas de segundos en escribirla; uno en la nube, un par. |
| No aparece la ventanita y salen notificaciones | Dice por qué la primera vez: casi siempre falta `gtk4-layer-shell` o `python-gobject`, o no hay sesión de Wayland. Probá la demo de arriba para ver el error. |
| «Hay varias apps que coinciden» | Decile cuál, o definí un alias en `PC_APPS`. |
| No ve la pantalla | El modelo tiene que leer imágenes; por voz, ser local; y tiene que estar instalado `grim`. |

## Cómo está hecho (para quien toque el código)

Es un canal más (`channels/voice.py`, canal `companion`): un hilo con un socket unix, una máquina de estados (`Companion`) que graba
(`voice/capture.py`), un `Listener` que transcribe (`voice/transcribe.py`), un `Assistant` que habla con `core.handle` en una
conversación de modo IA, y un `Speaker` (`voice/speaker.py`) que corta el texto en oraciones, las sintetiza con Kokoro y las reproduce
en un solo `aplay`. Todo lo que pasa sale como eventos por `voice/events.Bus`: la ventanita, el aviso de escritorio y la voz son solo suscriptores. La ventanita es un
proceso aparte (`voice/overlay_window.py`, GTK4 + gtk4-layer-shell, con el Python del sistema): `voice/overlay.py` tiene el `Presenter` (eventos →
tarjetas) y el `Window` (lanza el proceso, le manda una orden JSON por línea y, si falla, devuelve el trabajo a las notificaciones).
Cada contacto con el sistema (`pw-record`, `aplay`, `hyprctl`, `grim`) tiene **un solo punto** de procesos que los tests bloquean.

## Pendiente

- Un pitido opcional al empezar y terminar de grabar.
- Fish Speech como segundo motor de voz (la interfaz está lista; hace falta GPU o un servidor aparte).
- Verificar en vivo contra las APIs de Gemini y Anthropic el turno de resultados con una captura (las traducciones están probadas contra el formato).
