# Música con criterio: que la IA elija qué poner

Estado: propuesta (2026-10-28). Fases 0 (web apps en `open_url`), 1 (`musica_poner`), 2 (`musica_buscar`) y 3 (índice de playlists, `musica_playlists`) y 4 (`yt_historial` y `poner video <id>`) hechas, todas probadas en vivo y 5 (hecha SIN skill: ver CLAUDE.md; la skill sumaría una línea al prompt de cada turno y los `ai_tips` alcanzan, probado con qwen3.5:4b y Gemini); variar sin preguntar: se anota cuándo se puso cada playlist y el índice va ordenado por «hace más que no suena» (el plan solo decía «alternar»); desvíos de la fase 3: la huella es título+cantidad (no las primeras canciones, así no hay que abrir cada página para saber si cambió), el etiquetado usa la tarea `summary` y los 24 h son una constante; `PLAY_JS` probado contra YouTube Music real (búsqueda y playlist, con y sin aleatorio); `Page.navigate` acepta el «¿Salir del sitio?».

## Qué es

Que pedidos vagos funcionen sin darle a la IA una lista de nombres:

- «pon algo de música» → mira la hora y tus gustos y elige (tranquilo a la mañana, más movido a la tarde).
- «pon una playlist para el gimnasio» → si alguna de TUS playlists sirve para eso la pone, **aunque se llame
  «pepito 123»**; si no, busca una pública que se parezca a lo que escuchás.
- «pon los videos de café que tanto me gustan» → mira qué viste en YouTube y elige uno.

Siempre dice qué eligió y por qué («Puse *pepito 123*: Rammstein y Metallica, buena para entrenar»). Funciona en la
web y en Telegram, por texto o voz, porque son herramientas del núcleo como cualquier otra.

**La regla del diseño:** el modelo decide QUÉ (qué playlist, qué buscar); el código hace el CÓMO (abrir, encontrar el
botón, tocarlo) con direcciones armadas por el código y scripts fijos. Así es una llamada en vez de 10 vueltas de
`leer`/`clic`, sirve con modelos chicos y respeta «el navegador nunca corre JavaScript del modelo».

## Primera versión: solo YouTube Music y YouTube

Es lo que ya manejamos (`navegador.py`, `MUSIC_JS`, sesión de Google en tu Chromium) y ya está en
`BROWSER_TRUSTED_SITES` por defecto. Spotify queda afuera (ver «Más adelante»).

Sin la API de YouTube: da tus playlists pero no las recomendaciones, y pide OAuth con Google, mucha fricción para una
app local. Todo sale de tu navegador con tu sesión.

## Piezas

### 0. Abrir en la web app, no en una pestaña (`navegador.py`, sirve para todo `abrir`)

La persona tiene web apps de Omarchy (`~/.local/share/applications/*.desktop` con
`Exec=omarchy-launch-webapp <url>`, p. ej. «Youtube Music» → `https://music.youtube.com/`). Son `chromium --app=<url>`:
el MISMO Chromium y perfil (misma sesión de Google) en su propia ventana, y por CDP se ven como una página más
(comprobado: la ventana de WhatsApp aparece en `/json` como `page https://web.whatsapp.com/`). Así que manejarlas no
cambia nada; lo que cambia es cómo se abren.

- **Descubrir (sin procesos).** `_webapps()`, un punto único nuevo (bloqueado en los tests como `_history`), lee los
  `.desktop` de `~/.local/share/applications/` y saca nombre y dirección de `omarchy-launch-webapp <url>` o
  `--app=<url>`. Solo http/https; la dirección pasa por las mismas reglas que cualquier otra (bloqueados, red local).
  `reglas.pick_webapp(host, apps)` (pura) dice si un sitio tiene web app.
- **Abrir.** `open_url` (y por lo tanto `nav_abrir` y `musica_poner`) sigue este orden:
  1. Si ya hay una página de ese sitio (ventana de web app o pestaña), se usa esa: `Page.navigate` a la dirección
     pedida. No se abre otra ventana (cada `--app` abre una nueva).
  2. Si el sitio tiene web app, se lanza con `_run(["omarchy-launch-webapp", url])` (la dirección completa, p. ej. la
     de la playlist, así abre directo ahí), sin shell y con argumentos fijos. Si Chromium estaba cerrado, lo arranca
     con `chromium-flags.conf`, que ya tiene el puerto. Se espera la página nueva comparando los ids de `/json` antes y
     después (mismo sitio, `LOAD_TRIES`) y se anota como abierta por Epsilon, como hoy.
  3. Si no, la pestaña de siempre (`/json/new`).
- **Ajuste** `BROWSER_USE_WEBAPPS` (por defecto `si`). Con `no`, todo como hoy.
- **No cambia:** `nav_buscar` sigue abriendo Google en una pestaña de fondo (no es una web app y no debe robar el
  foco). Abrir una web app sí toma el foco (como abrir una pestaña con `/json/new`).
- **Tests:** `pick_webapp` con `.desktop` de ejemplo (Exec con comillas, con `%U`, sin URL, `file:`), reusar una página
  existente antes que lanzar, lanzar con argumentos fijos, y `_webapps` bloqueado.

### 1. Gustos en la memoria (sin código)

Las preferencias van en `memory.md`, no en `settings.json` (la IA no tiene acceso a los ajustes, y así sigue):

```
- Música: uso YouTube Music
- Mañana: tranquilo (lo-fi, jazz suave). Tarde: más movido. Para el gym no quiero electrónica
- «Videos de café» = baristas / latte art en YouTube
```

La IA las lee en cada mensaje y las actualiza con `recordar`/`editar` (con permiso) cuando le decís «esa no» o
«desde ahora…». Vos las ves y corregís con `/memoria`.

### 2. Índice de tus playlists (`data/music_index.py`, `DATA_DIR/music_index.json`)

Lo que permite elegir por contenido y no por nombre.

**Leer (sin modelo).** `navegador.music_playlists()` abre en una pestaña de fondo
`music.youtube.com/library/playlists` y, por cada playlist, su página (`/playlist?list=<id>`), con un script fijo
nuevo, `PLAYLIST_JS`, que solo lee: id, título, cantidad de canciones y las primeras `INDEX_TRACKS` (15) canciones con
su artista. Cierra la pestaña. Tope `MAX_PLAYLISTS` (50). Títulos recortados; todo es dato.

**Etiquetar (con modelo, solo lo que cambió).** Cada playlist tiene una «huella»: hash de cantidad + primeras
canciones. Si la huella es la misma que la guardada, no se toca. Las nuevas o cambiadas van, todas juntas, en UNA
llamada a `llm.generate` (tarea nueva `music`, prompt fijo y corto, `max_output_tokens` acotado, las canciones rotuladas
como datos, no instrucciones) que devuelve por playlist:

- `estilo`: texto corto (≤ 80 caracteres), p. ej. «rock/metal rápido, mucha energía».
- `usos`: de una lista FIJA, validada en código: `gym`, `correr`, `concentrarse`, `relajarse`, `dormir`, `fiesta`,
  `manejar`, `cocinar`, `mañana`, `noche`. Lo que no esté en la lista se descarta.
- `energia`: 1 a 5.

Lo que el modelo devuelve mal (JSON roto, id que no existe) se ignora: esa playlist queda sin etiquetar y se reintenta
la próxima vez. Gasto anotado como «Música» en `/uso`; dentro de un chat de modo IA, suma a ese chat. Después de la
primera vez el costo es casi cero (solo lo que cambió).

```json
{"version": 1, "updated": "2026-10-28T10:00",
 "playlists": [{"id": "PLxxxx", "title": "pepito 123", "count": 48, "print": "a1b2…",
                "artists": ["Rammstein", "Metallica", "…"], "style": "rock/metal rápido, mucha energía",
                "uses": ["gym", "correr"], "energy": 5}]}
```

Escribe solo `music_index.py`, por `safe_path` y archivo temporal (es un índice derivado, se puede regenerar: no es
una nota de la persona, así que reescribirlo no rompe «nunca pisar»).

**Cuándo se actualiza:** al pedirlo la IA (acción `playlists`) si el índice tiene más de `MUSIC_INDEX_HOURS` (24 h) o
no existe; o a mano con `/musica playlists`. Sin hilos propios.

### 3. Herramientas nuevas (grupo `musica`, separado de `pc` para no inflar ese grupo)

| acción | herramienta | qué hace | permiso inicial |
|---|---|---|---|
| `playlists` | `musica_playlists` | devuelve el índice (título, estilo, usos, energía, artistas principales); lo actualiza si hace falta | `allow` (lee lo tuyo, gasta poco) |
| `buscar` | `musica_buscar` | busca en `music.youtube.com/search?q=…` y devuelve playlists públicas (id, título, autor, cantidad) con un script fijo (`MUSIC_SEARCH_JS`); no reproduce | `allow` |
| `poner` | `musica_poner` | reproduce una playlist por id, o una búsqueda de canciones; opcional `aleatorio` | `allow` (como `musica`: acotada) |
| `historial` | `yt_historial` | los videos de YouTube que más viste en las últimas semanas (título y veces), filtrables por palabra | `ask` (es privado) |

Detalles:

- **`poner`** acepta `playlist <id>` o `canciones <texto>`. El id se valida con `^[A-Za-z0-9_-]{10,64}$` y la
  dirección la arma el código: `music.youtube.com/playlist?list=<id>` (o `watch?v=` para un video de YouTube). Busca el
  botón «Reproducir» / «Aleatorio» con un script fijo que solo devuelve su posición y lo toca con un clic real
  (`Input.dispatchMouseEvent`, como `_music_click`). Reusa la pestaña de YouTube Music si hay una; si no, abre una.
  Confirma que arrancó mirando el título del reproductor (`MUSIC_JS`). Abre vía `open_url`, así que usa la web app de
  YouTube Music si existe (pieza 0).
- **`buscar`** ordena los resultados de modo que el modelo pueda elegir; arma la búsqueda el modelo («workout rock
  playlist»), pero la dirección y la lectura las hace el código. Tope de búsquedas por mensaje, como `nav_buscar`.
- **`historial`** usa la copia de solo lectura que ya hace `_history`, filtrando `youtube.com/watch` y agrupando por
  video. Devuelve como mucho 20, con título recortado. No incluye las direcciones de otros sitios.
- Ninguna toca `browser_guard` de más: son sitios de confianza y las direcciones las arma el código. Los bloqueos duros
  de `navegador.py` (y `BROWSER_ENABLED`) valen igual. Todo queda en `browser.log` y en Actividad (`Tool.done`).

### 4. Skill `musica` (solo texto, se suma a `factory.py`)

El procedimiento, para que también lo siga un modelo chico:

1. Mirá la hora (`fecha`) y las preferencias de música de la memoria.
2. Si pide algo que ya vio («los de café»), usá `historial` y elegí uno.
3. Si no, leé tus `playlists` y elegí la que mejor encaje por `usos`, `estilo` y `energia`; si hay varias parecidas,
   alterná (no siempre la misma).
4. Si ninguna encaja, `buscar` una pública usando los artistas que más aparecen en el índice.
5. `poner` y contá en UNA línea qué pusiste y por qué. Si era una pública, ofrecé guardarla (no la guardes sola).
6. Si te dicen «esa no» o «para X prefiero Y», proponé anotarlo en la memoria.

Que se use sola con «pon música…»: `chat.preload` ya carga la skill si el mensaje dice su nombre o palabras clave; se
agregan «música», «playlist», «poné», «gym».

## Cómo se ve un pedido

«pon una playlist para el gimnasio» (16:30):
1. `herramientas` → carga `musica` (o la carga `preload` por «playlist»).
2. `musica.playlists` → 12 playlists; «pepito 123» tiene `usos: gym, correr`, energía 5.
3. `musica.poner playlist PLxxxx aleatorio`.
4. «Puse *pepito 123* en aleatorio: Rammstein, Metallica, rock rápido. ¿Va bien?»

Tres vueltas del modelo, contra ~10 haciéndolo con `leer`/`clic`.

## Fases (una por vez, cada una con sus tests y su commit)

0. **Web apps en `open_url`** (pieza 0). Chica, y mejora también el `abrir` de hoy.
1. **`musica_poner`** (playlist por id, canciones por texto, aleatorio). Ya sirve sola para «poné lo-fi».
2. **`musica_buscar`** (playlists públicas).
3. **Índice** (`music_playlists` + `music_index.py` + etiquetado) y `musica_playlists`. La parte más grande.
4. **`yt_historial`** y videos de YouTube en `poner` (`watch?v=`).
5. **Skill `musica`**, palabras de `preload` y probar con un modelo chico (qwen3.5:4b) y con Gemini.

## Tests (sin red, sin Chromium, sin modelo)

- Los scripts fijos no se ejecutan: se prueba el Python que interpreta lo que devuelven (fixtures con la forma de
  `PLAYLIST_JS`/`MUSIC_SEARCH_JS`), como `_search_results`.
- Huella: igual → no etiqueta; cambia la cantidad o las primeras canciones → etiqueta solo esa.
- Etiquetado con un `llm.generate` falso: usos fuera de la lista descartados, JSON roto ignorado, id inventado ignorado.
- `poner`: ids inválidos rechazados, dirección armada por el código, nunca un `file:` ni otro host.
- `historial`: solo `youtube.com/watch`, agrupado, tope de 20, sin otros sitios.
- Las 4 herramientas tienen `Tool.done` y están en el orden previsto de `TOOLS`.

## Riesgos y límites

- **YouTube cambia su HTML:** los scripts fijos se rompen. Por eso cada uno está aislado, con un error claro («no
  encuentro el botón de reproducir: puede que YouTube haya cambiado») y la IA puede caer al camino lento
  (`leer`/`clic`).
- **Leer 50 playlists tarda** (una página cada una): solo pasa la primera vez o cuando el índice venció, y se avisa
  con el progreso en vivo («Leyendo tus playlists…»).
- **El modelo etiqueta por lo que sabe de los artistas:** un artista poco conocido puede quedar mal clasificado. La
  persona corrige diciendo «esa no es para el gym» (va a la memoria, que gana sobre el índice).
- **Privacidad:** el historial y las playlists no salen de tu compu salvo hacia el modelo que elijas. Con Ollama no
  salen nunca.

## Más adelante (fuera de esta versión)

- **Spotify:** MPRIS `OpenUri` reproduce una `spotify:playlist:…`, pero para listar tus playlists y su contenido hace
  falta su API (OAuth). Sería un conector MCP aparte, y la skill elegiría la app según la memoria.
- **Rutinas con IA:** «a las 8, algo tranquilo» elegido por la IA cada día. Depende de las rutinas con IA (hoy las
  rutinas no usan modelo); mientras tanto, una rutina puede `poner` una playlist fija.
- **Recomendaciones de la portada** de YouTube («algo que me recomiende YouTube»): un `leer` de `youtube.com` ya
  alcanza; si se usa mucho, un script fijo propio.
