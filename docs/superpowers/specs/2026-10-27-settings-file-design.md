# Archivo de ajustes propio (`settings.json`, al estilo VS Code)

Fecha: 2026-10-27. Estado: diseño, para revisar.

## Por qué

Hoy todos los ajustes viven en el `.env` (unos 40, los de `settings/config.py → SETTINGS`), y otros ~140 números
están fijos en el código (topes de pasos, de caracteres, tiempos máximos). Al autor el `.env` no le molesta, pero
Epsilon apunta a que otra persona lo instale y lo use: a la mayoría le resulta raro editar un `.env`, y los números
fijos solo se pueden cambiar tocando el código.

La idea es la de VS Code: la pantalla de Configuración sigue para lo de todos los días, y además hay un botón (y un
comando) que abre en el editor de texto de la PC un JSON con **todos** los ajustes que tiene sentido cambiar, con
un archivo aparte de valores de fábrica para poder volver atrás.

## Lo que decidió la persona

- Dos archivos: **`settings_default.json`** (todos los ajustes con su valor de fábrica) y **`settings.json`** (lo que
  la persona cambió). Lo que no está en `settings.json` usa el valor de fábrica: volver atrás es borrar la línea.
- El JSON incluye los ajustes de la pantalla **y los números fijos del código** que tiene sentido afinar.
- **Las defensas no entran** (volumen nunca más de 100 %, y las de esa clase): siguen fijas en el código.
- Se abre en el **editor de texto de la PC**, no en un editor dentro de la página.
- Al guardar el archivo, **Epsilon lo nota solo y aplica al instante** lo que se puede; lo que pide reinicio queda
  avisado en la pantalla (como hoy).
- Formato **JSONC, como VS Code**: comentarios `//` arriba de cada ajuste en `settings_default.json`, y
  `settings.json` acepta comentarios de la persona.

## Objetivos

1. Un registro único en el código con cada ajuste: clave, tipo, valor de fábrica, rango o valores permitidos,
   explicación corta, si se aplica al instante o pide reinicio (y por qué), y en qué sección de la pantalla va (si va).
2. `settings_default.json` generado desde ese registro, con un comentario por ajuste que diga para qué sirve, el
   rango permitido y si pide reinicio. Una persona tiene que poder entenderlo sin leer el código.
3. `settings.json` como fuente de los ajustes que no son claves. La pantalla de Configuración lee y escribe ahí.
4. Abrir cualquiera de los dos archivos desde la web (botón en Configuración) y con un comando.
5. Cambios aplicados al instante al guardar el archivo (lo que se pueda), con aviso de lo que pide reinicio.
6. Un error en el archivo nunca impide que Epsilon arranque ni que siga andando.

## No objetivos

- Editor de JSON dentro de la web.
- Editar las claves (Gemini, Telegram, Anthropic, las de plugins) desde el JSON.
- Ajustes por carpeta o por proyecto, sincronización entre máquinas, perfiles.
- Abrir los archivos desde Telegram (no hay pantalla de por medio).
- Volver configurables las defensas.

## Anti-objetivos (aunque funcione, sería un fracaso si…)

- Una clave termina escrita en `settings.json` o en `settings_default.json`, o aparece en un aviso o en un log.
- Un JSON roto (o un valor fuera de rango) deja a Epsilon sin arrancar, o sin web.
- Guardar desde la pantalla de Configuración borra los comentarios o reordena lo que la persona escribió a mano en
  `settings.json`.
- La IA puede leer o cambiar los ajustes (por ejemplo, subirse sus propios topes de pasos o de gasto).
- Epsilon pisa el `settings.json` de la persona (por ejemplo, al migrar o al encontrarlo roto).
- Algo pasa a ser configurable y deja de serlo una defensa (bloqueos del navegador, patrón del id de conversación,
  `safe_path`, topes de seguridad de importación de skills, etc.).

## Cómo funciona

### Dónde viven

- Los dos archivos van en `DATA_DIR` (`DATA_DIR/settings.json` y `DATA_DIR/settings_default.json`), por `safe_path`,
  como el resto de los datos. *(Decisión del diseño, no de la persona.)*
- En el `.env` quedan solo **las claves** (`secret=True`) y **lo de arranque** que hace falta antes de poder leer
  `DATA_DIR`: `DATA_DIR` mismo. `EPSILON_ENV_FILE` sigue funcionando igual; como el `DATA_DIR` sale de ese `.env`, una
  configuración de prueba apunta a su propia carpeta y a sus propios `settings*.json`.
- `ALLOWED_USER_ID` (la única lista blanca de Telegram) **se queda en el `.env`** con las claves y sigue pidiendo
  reinicio, aunque no sea secreto. *(Decisión del diseño.)*
- Una variable de entorno puesta por el servicio (systemd) o por la terminal **gana** sobre `settings.json`.
  *(Decisión del diseño.)* Para distinguirla de lo que viene del `.env`, se toma una foto de `os.environ` **antes** de
  `load_dotenv`, y `load_dotenv` pasa a cargar solo las claves, `ALLOWED_USER_ID` y `DATA_DIR`. Los valores de
  `settings.json` no se vuelcan en `os.environ`: todos los lectores pasan por una función de acceso del registro. La
  pantalla muestra «viene del entorno» solo para lo que estaba en la foto.

### `settings_default.json`

- Lo escribe Epsilon en cada arranque desde el registro (y lo reescribe si cambió). Arriba lleva un comentario: «Lo
  genera Epsilon: no lo edites, tus cambios van en settings.json; se vuelve a escribir al arrancar».
- Agrupado por sección con un comentario de título por grupo (General, Modelos, Chat con IA, Voz, Telegram,
  Navegador, Rutinas, Conectores, Skills, Archivos…). Cada ajuste lleva arriba su comentario (qué hace, rango o
  valores permitidos, «pide reinicio: <motivo>» si corresponde).
- Los ajustes de plugins también aparecen, en su propio grupo, con lo que declara su `plugin.toml`. Sus claves llevan
  el prefijo `plugin.<nombre>.` (así nunca chocan con las de Epsilon); los secretos de un plugin siguen en el `.env`.
  Si un plugin aparece después de la migración y su ajuste no secreto está en el `.env`, se copia a `settings.json`
  esa vez, con la misma regla de no pisar. *(Decisión del diseño.)*

### `settings.json`

- Si no existe, Epsilon lo crea con un `{}` y un comentario arriba que explica que acá van los cambios y que los
  valores posibles están en `settings_default.json`.
- Se acepta JSONC: comentarios `//` y `/* */`, y comas al final, como VS Code.
- **Cuando la pantalla de Configuración guarda**, cambia solo el texto de las claves tocadas (agrega, reemplaza el
  valor o borra la línea) y deja intacto todo lo demás: comentarios, orden, espacios. Nunca reescribe el archivo
  entero desde un diccionario. La escritura es por archivo temporal, como las demás reescrituras del proyecto.
- Si el archivo está roto (no se puede leer como JSONC), la pantalla no lo pisa: avisa y no deja guardar hasta que se
  arregle a mano. *(Decisión del diseño.)*

### Qué ajustes entran

- **Todos los de `SETTINGS` que no son claves**, más los que hoy se leen con `os.environ` sin estar en el registro
  (voz: `TTS_*`, `KOKORO_MODELS_DIR`; Whisper: `WHISPER_*`; Ollama: `OLLAMA_NUM_CTX`; etc.). Las variables del
  sistema que no son de Epsilon (`DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `HYPRLAND_INSTANCE_SIGNATURE`) no
  entran. `ASSISTANT_NAME` es de la voz, no el nombre del asistente (`AGENT_NAME`).
- **Los números fijos que son topes de gasto, de tamaño o de tiempo** que tiene sentido afinar. Ejemplos que entran:
  `chat.MAX_TOOL_STEPS`, `chat.MAX_BROWSER_STEPS`, `chat.MAX_TOKENS`, `core.MAX_RESULT_CHARS`, `ask.MAX_QUESTION`,
  `browser_guard.MAX_BROWSER_ACTIONS|MAX_NEW_TABS|MAX_SEARCHES`, `connectors/manager` (`IDLE_CLOSE`, `MAX_RUNNING`,
  `CALL_TIMEOUT`, `OPEN_TIMEOUT`), `images.MAX_SIDE|MAX_SIDE_CSV|JPEG_QUALITY`, `summarize.MAX_TEXT|MAX_FILE`,
  `transcribe.MAX_BYTES|MAX_SECONDS`, `routines/runner.RUN_BUDGET`, `routines/steps.MAX_WAIT`, `dolar`/`clima`
  `CACHE_SECONDS`, `telegram.PROGRESS_AFTER|TYPING_EVERY`, `tabular.MAX_ROWS`, `navegador.MAX_TEXT|MAX_ELEMENTS`.
- **No entran** (defensas e invariantes): `multimedia.MAX_VOLUME|MAX_STEP`, los topes de importación de skills
  (`importer.*`), `plugins/channels.MAX_MANIFEST_BYTES` y similares, los topes de lo que escribe un tercero y se
  recorta por ser dato (`MAX_DESCRIPTION`, `MAX_RESOURCE`, `MAX_TEXT` de `multimedia`/`ventanas`, `bridge.*`), los
  límites que pone otro servicio (`telegram_format.LIMIT`, `SEND_MAX_BYTES`, `EDIT_EVERY`), las constantes que no son
  ajustes (`images.ORIENTATION`, `navegador.PORT`, `MIN_REF`), y todo lo que un test vigila que coincida con otra cosa
  (si dos constantes deben ser iguales, como `connectors/manager.MAX_RESULT_CHARS` y `core.MAX_RESULT_CHARS`, son UN
  solo ajuste).
- Criterio para lo dudoso: si cambiar el número puede saltear una protección, dejar entrar datos de afuera sin
  recortar, o romper un acuerdo con otro servicio, no entra. Si solo cambia cuánto gasta, cuánto espera o cuánto
  muestra, entra.

### Validación

- Cada ajuste tiene tipo (entero, decimal, sí/no, texto, lista, modelo) y, si es número, mínimo y máximo. Los textos
  reusan los validadores que ya existen en `config.py` (`_person_name`, `_model_id`, `_port`…). Los rangos se eligen
  para que no se pueda poner un valor absurdo (por ejemplo, `MAX_TOOL_STEPS` entre 1 y 50).
- Un valor que no sirve se ignora (rige el de fábrica) y se avisa. Una clave que no existe se avisa («¿quisiste decir
  `chat.max_tool_steps`?» si hay uno parecido). Una clave secreta escrita en `settings.json` se ignora y se avisa sin
  repetir el valor.
- Los avisos salen en la pantalla de Configuración (y en `GET /api/config`), con la clave y el motivo, y en el log.
  Nunca llevan el valor si el ajuste es secreto.

### Aplicar al guardar

- Un hilo mira la fecha de modificación de `settings.json` cada pocos segundos (sin dependencias nuevas). Al cambiar,
  lo vuelve a leer, valida y aplica los ajustes que se aplican al instante. Los que piden reinicio quedan pendientes y
  aparecen en el aviso de reinicio que ya existe, con su motivo. *(Decisión del diseño: sondeo y no `inotify`, para
  no sumar dependencias.)*
- **Los ajustes que aflojan una protección siempre piden reinicio**, aunque técnicamente se pudieran aplicar al
  instante (decisión de la persona). Motivo: cualquier programa local (un conector MCP, un plugin, un paso de rutina)
  puede escribir `settings.json`, y no tiene que poder subirle los topes a la IA en segundos. El registro lleva un
  atributo para marcarlos (por ejemplo `loosens=True`), y el aviso de reinicio los lista con el valor de ahora y el
  nuevo, así la persona ve qué va a cambiar antes de reiniciar. Entran ahí, como mínimo: `BROWSER_GUARD`,
  `BROWSER_ENABLED`, `BROWSER_TRUSTED_SITES`, `BROWSER_BLOCKED_SITES`, `CHAT_LIMIT_USD`, los topes de pasos y de
  salida de la IA (`MAX_TOOL_STEPS`, `MAX_BROWSER_STEPS`, `MAX_TOKENS`, `MAX_CALLS_PER_STEP`), los del navegador
  (`MAX_BROWSER_ACTIONS`, `MAX_NEW_TABS`, `MAX_SEARCHES`), los de tamaño de lo que entra al modelo
  (`MAX_RESULT_CHARS`, `MAX_QUESTION`, `CHAT_MAX_FILES`, `CHAT_MAX_MB`) y `ROUTINES_ENABLED`. Ante la duda, marcarlo.
  Lo mismo vale cuando el cambio lo guarda la pantalla de Configuración, para que haya una sola regla.
- La pasada se puede llamar sin el hilo (como `routines/engine.tick`), así se prueba sin esperar.
- Un ajuste que hoy es una constante de módulo y se aplica «al instante» tiene que leerse en el momento de usarlo, no
  al importar el módulo.

### Abrir los archivos

- Configuración tiene dos botones: «Abrir settings.json» y «Ver valores por defecto» (abre `settings_default.json`).
  Se abren con el programa que el sistema usa para **texto** (`xdg-mime query default text/plain` y se lanza esa
  aplicación), no con el asociado a JSON, que suele ser el navegador; si no hay ninguno, `xdg-open`. Solo con
  pantalla, y con el mismo mensaje claro que «abrir carpeta» de la Biblioteca si no la hay. Lanzarlo pasa por un único
  punto de procesos, bloqueado en los tests. *(Decisión del diseño.)*
- Un comando de la persona, solo para la web (`web=True`; en Telegram no sirve), con su entrada en `tools.py`. Nombre
  a elección del constructor, en español y que no choque con uno existente ni con una skill.
- **La IA no tiene ninguna herramienta que lea, abra o cambie estos archivos**, y el comando no lleva `links` (un
  enlace `/?cmd=` no lo ejecuta). Los archivos tampoco aparecen en la Biblioteca (van en `library.SYSTEM_NOTES` o el
  equivalente para archivos del sistema) ni se pueden mencionar con `@`.

### Migración

- La primera vez que arranca con esta versión, si `settings.json` no existe, Epsilon copia ahí los ajustes que no son
  claves y que hoy están en el `.env`. Desde entonces `settings.json` es la fuente; si esas líneas siguen en el `.env`,
  se ignoran y la pantalla avisa una vez que se pueden borrar de ahí. Epsilon no las borra del `.env`
  solo. *(Decisión del diseño: «crear o agregar, nunca pisar».)*
- Si `settings.json` ya existe, no se migra nada.
- `.env.example` queda solo con las claves y `DATA_DIR`, con un comentario que manda a `settings.json` para lo demás.

## Lo que queda para quien lo construya

- Los nombres de las claves en el JSON. Sugerencia: con puntos y por sección, como VS Code
  (`chat.max_tool_steps`, `browser.max_new_tabs`, `ai.model`), y el código traduce los nombres viejos del `.env` al
  migrar. Si se prefiere mantener los nombres del `.env` (`AI_MODEL`), también vale; lo que importa es que sea uno
  solo y consistente.
- Cómo se reemplazan las constantes de módulo por lecturas del registro sin romper los tests que hoy las cambian con
  `monkeypatch` (por ejemplo, una función de acceso y un ayudante de tests).
- Cómo conviven los lectores actuales que usan `os.environ.get(...)`: pasarlos a la función de acceso, o un paso
  intermedio.
- La lista exacta de constantes que entran, siguiendo el criterio de arriba. Ante la duda, dejarla afuera: siempre se
  puede sumar después.
- Los rangos de cada número.
- El lector de JSONC (sin dependencias si alcanza con sacar comentarios y comas finales) y la edición mínima de texto
  que conserva los comentarios.
- Tests: generación de `settings_default.json`, lectura con comentarios, valores rotos, clave secreta en el archivo,
  migración desde un `.env` de prueba (`EPSILON_ENV_FILE`), edición desde la pantalla que conserva comentarios, la
  pasada de vigilancia, que la IA no tenga herramienta para esto y que el comando no tenga `links`.
- Documentación: `docs/plan-config.md`, CLAUDE.md y `.env.example`.
