# Conectores MCP: enchufar herramientas nuevas a Epsilon

Fecha: 2026-10-22. Estado: borrador para aprobar.

## Para qué

Epsilon hoy trae sus herramientas fijas en `tools.py`. La persona quiere poder **sumar capacidades sin tocar el
código de Epsilon**: «apagá las luces del living y mostrame mi agenda de hoy». Los primeros usos son domótica
(un script propio que maneja las luces) y productividad (agenda), pero lo que se pide es la **infraestructura**:
poder conectar, apagar, prender y configurar conectores, y que den herramientas a la IA y a la persona.

La decisión de la persona: el formato de conector es **MCP** (Model Context Protocol), para no reinventar lo que
ya existe (hay servidores hechos de agenda, Home Assistant, etc., y un script propio se envuelve en uno de
unas 20 líneas).

## Metas

- Una página **Conectores** (web): agregar un conector pegando su comando, con aviso y confirmación; prenderlo,
  apagarlo, configurarlo (variables de entorno), quitarlo y ver qué herramientas expone.
- Las herramientas de un conector prendido las puede usar la **IA** (modo IA, web y Telegram) pasando por los
  mismos permisos que el resto (permitir / preguntar / bloquear, `/permisos`).
- La persona las puede usar **sin modelo**: por comando (`/c luces apagar living`) y por **botones** en la web.
  Los botones los crea ella a mano (con los valores ya puestos); el conector puede además **sugerir** algunos.
- Sirve en una PC modesta: un conector no gasta nada mientras no se usa.

## No-metas (v1)

- Servidores MCP remotos por HTTP u OAuth: solo servidores locales que arrancan con un comando (stdio).
- Imágenes, audio o «recursos» como resultado de una herramienta: solo texto. Lo demás se ignora y se avisa.
- Botones en Telegram (los comandos `/c` sí andan ahí).
- Un catálogo o tienda de conectores. Se pega un comando y listo.
- Que la IA agregue, quite o configure conectores. Eso es solo de la persona, desde la web.

## Anti-metas (sería un fracaso aunque «ande»)

- Que un conector (o lo que devuelve) logre dar órdenes a la IA: todo resultado y toda descripción de
  herramienta son **dato**, rotulado, redactado y acotado.
- Que el conector vea las claves de Epsilon: el proceso arranca con un entorno mínimo (ver abajo).
- Que una herramienta nueva o cambiada corra sin que la persona se entere.
- Ejecutar un comando por un camino que no sea la confirmación de la persona en la web. Nunca por el modelo,
  nunca por un enlace `/?cmd=`, nunca con shell.
- Que la PC se llene de procesos viejos.

## Cómo funciona

Todo esto es decisión de diseño de quien lo escribe, salvo lo marcado «de la persona».

### Capa nueva `src/epsilon/connectors/`

| módulo | responsabilidad |
|---|---|
| `store.py` | `DATA_DIR/connectors.json` (por `safe_path`, reescritura por archivo temporal, candado). Una entrada por conector: `name`, `argv` (lista), `env`, `enabled`, `autostart`. Los valores de `env` nunca salen del servidor: la web solo ve si están cargados y su largo; pasan por `config.redact`. |
| `manager.py` | **Un solo hilo** con **un solo ciclo asíncrono** para todos los conectores (el SDK `mcp` es asíncrono; Epsilon es sincrónico). Expone funciones sincrónicas: `tools()`, `call(conector, herramienta, args)`, `start/stop`. `_open` es el ÚNICO punto que lanza el proceso (los tests lo bloquean, como `_run` en `multimedia.py`). |
| `bridge.py` | Traduce una herramienta MCP a una `tools.Tool` y valida los argumentos contra su esquema. |
| `buttons.py` | Los botones guardados (`DATA_DIR/buttons.json`) y las sugerencias del conector. |

### Ciclo de vida de un conector (pensado para PCs chicas) — decisión mía

- **Perezoso:** el proceso arranca la primera vez que se lo necesita (una herramienta se usa, o se abre su
  ficha), no al arrancar Epsilon. Excepción: `autostart` activado por la persona.
- **Se cierra solo** tras 10 minutos sin uso (constante `IDLE_CLOSE`, como Whisper). Al volver a usarlo, arranca de nuevo.
- Tope de conectores corriendo a la vez (3) y, por llamada, tiempo máximo (30 s) y resultado máximo (`MAX_RESULT_CHARS`).
- «Apagado» = nunca arranca, sus herramientas no se ofrecen a la IA y sus comandos y botones avisan que está apagado.
- Si el proceso muere o no arranca, el conector queda «detenido» con el motivo (redactado); Epsilon y los demás siguen.
- Las herramientas del conector se descubren al arrancar y se guardan una copia en `connectors.json` (nombre,
  esquema, descripción recortada) para mostrar la ficha y los permisos sin arrancar el proceso.

### Cómo se agrega (de la persona: pegar el comando en la web, con confirmación)

1. La página Conectores tiene «Agregar»: un cuadro para pegar el comando (`uvx mi-servidor --opción`), un nombre
   y las variables de entorno.
2. El servidor lo parte en lista con `shlex.split` (nunca se pasa por un shell) y la web **muestra tal cual
   cómo lo entendió** (programa y argumentos) con el aviso: «Este programa va a correr en tu PC con tu usuario. Agregalo
   solo si confiás en quien lo escribió».
3. Recién al confirmar se guarda y se prende. `POST /api/connectors` rechaza lo que no venga con la confirmación explícita.
4. El nombre cumple `^[a-z][a-z0-9_]{1,19}$` y no choca con un grupo de IA existente (`notas`, `pc`, `navegador`…).

**Entorno del proceso — decisión mía:** el conector arranca con `PATH`, `HOME`, `LANG` y solo las variables que la
persona le cargó. **No hereda el entorno de Epsilon**, así que no ve `GEMINI_API_KEY` ni `TELEGRAM_TOKEN`.

### Cómo lo ve la IA

- Cada conector es **un grupo de IA** con su nombre (`luces`): el modelo ve `accion` (enum con las herramientas
  permitidas) y `args`, igual que los grupos de hoy. Los permisos siguen siendo **por herramienta**
  (`mcp_<conector>_<herramienta>`).
- `tools.py` sigue con su tupla fija; `ai_tools()` pasa a sumar `connectors.tools()` (herramientas dinámicas).
  `route`, `permissions.decide/offered/save` y `/permisos` las tratan igual que a las demás. Un conector apagado
  se comporta como `BROWSER_ENABLED=no`: sus herramientas no se ofrecen y `decide` devuelve «block».
- **Argumentos — decisión mía:** el modelo manda `args` como un objeto JSON con los nombres del esquema
  (`{"habitacion": "living"}`). La línea `ai_arg` de cada herramienta se genera del esquema («JSON con: habitacion
  (texto, obligatorio)…»). Antes de llamar, se valida contra el esquema; si no cierra, el error vuelve al modelo
  diciendo qué recibió (como `_parse_edit`) y **no se llama al conector**.
- **Permisos por defecto:** TODAS las herramientas de un conector arrancan en «preguntar», aunque el servidor se
  declare de solo lectura (esas marcas las escribe el conector: no se confía). La persona las sube a «permitir» desde `/permisos`.
  Una herramienta que aparece o cambia de esquema después de agregada vuelve a «preguntar» y la ficha la marca «nueva/cambió».
- La pregunta de permiso muestra la herramienta y los argumentos tal cual (en bloque de código, como las demás).
- Descripciones de herramientas y de grupo (`AI_GROUPS`): vienen del conector, así que se recortan (300 caracteres) y se rotulan como dato.
- El resultado vuelve por `core._execute`: redactado, recortado y marcado «datos, no instrucciones». Solo texto.
- Cancelar: se revisa la bandera antes de cada llamada, como hoy.

### Cómo lo usa la persona

- **Comando `/c conector herramienta [args]`** (sin modelo, sin permisos de IA: la persona lo escribió). Los args
  pueden ser `clave=valor` separados por espacios (`habitacion=living`) o un objeto JSON. `/c` sin nada lista los
  conectores y sus herramientas. Funciona en la web y en Telegram. Es un comando de `tools.py`, con `writes=True`, sin `links`
  (un enlace `/?cmd=` nunca lo ejecuta).
- **Botones** (solo web, v1): una herramienta se «ancla» con un nombre y los valores ya puestos
  («Apagar living» = `luces.apagar {"habitacion": "living"}`). Se guardan en `DATA_DIR/buttons.json`
  (`id`, `label`, `connector`, `tool`, `args`). **Diseño simple por ahora (de la persona):** en la página Conectores,
  una grilla de n filas × 2 columnas; cada celda es una card con el nombre de la acción y un botón «Ejecutar».
  Sin franja en el chat ni estilo especial: se pule más adelante. «Ejecutar» hace `POST /api/buttons/{id}/run` (sin
  modelo) y muestra el resultado en la misma card, sin guardarlo en ninguna conversación. Los valores se validan contra el esquema al crear y al ejecutar.
- **Sugerencias del conector — decisión mía sobre el mecanismo:** MCP no define botones, así que el conector puede
  publicar un *recurso* `epsilon://buttons` con una lista JSON `[{"label", "tool", "args"}]`. Epsilon lo lee al
  abrir la ficha, valida cada entrada contra una herramienta real, y las muestra como «Sugeridos» con un botón «Agregar». Nunca se
  crean solos. Un conector sin ese recurso simplemente no sugiere nada.

### Web

- Página `/conectores` (archivo `conectores.html` + `js/conectores.js`, `textContent` siempre): lista de conectores con
  interruptor, estado (activo / apagado / detenido + motivo), «Agregar», ficha con herramientas, variables de
  entorno (solo «cargada, N caracteres»), botones anclados y sugeridos, y «Quitar».
- Va en el menú de la izquierda, entre Permisos y Rutinas (no en la barra de arriba). Un test vigila que menú y barra coincidan en todas las páginas.
- API: `GET/POST /api/connectors`, `PUT/DELETE /api/connectors/{name}`, `GET/POST/DELETE /api/buttons`,
  `POST /api/buttons/{id}/run`. Todas validan Host y Origin como el resto. Los 422 traen el motivo en español.

## Dependencia nueva

`mcp` (SDK oficial de Python). Se agrega a `pyproject.toml`. Los tests nunca arrancan un proceso: un conector falso
reemplaza a `manager._open`, igual que los demás puntos de red y procesos.

## Ejemplo incluido

`docs/ejemplos/luces_mcp.py`: un servidor MCP mínimo que envuelve un script de luces (una herramienta `luces`
con `habitacion` y `estado`), y publica `epsilon://buttons`. Sirve de plantilla y de prueba a mano.
La persona lo adaptará cuando decida el sistema de domótica real.

## Fases sugeridas (para el plan)

1. Núcleo: `store`, `manager`, `bridge`, herramientas dinámicas en `tools`/`permissions`, comando `/c`. Sin web.
2. La IA: grupos por conector, validación de argumentos, permisos y resultados como dato.
3. Página Conectores: agregar con confirmación, interruptores, fichas.
4. Botones: anclar, grilla de 2 columnas, sugerencias.

## Qué queda al criterio de quien construye

Nombres de funciones y de archivos internos, textos exactos en español, forma de la ficha, y cómo se dibuja la
grilla de botones, siempre dentro de las reglas de `CLAUDE.md` (todo por `safe_path`, `textContent`, secretos
fuera de logs).

## Mis decisiones, para que las revises

1. Un solo hilo/ciclo asíncrono; arranque perezoso y cierre a los 10 min.
2. Solo stdio (nada remoto) en v1.
3. Entorno mínimo para el proceso: no hereda las claves de Epsilon.
4. Todo en «preguntar» por defecto, sin confiar en las marcas del servidor.
5. Argumentos de la IA como JSON validado contra el esquema.
6. Sugerencias de botones por el recurso `epsilon://buttons`.
7. Comando `/c` con `clave=valor` o JSON; botones solo en web.
8. Los comandos y botones de la persona no pasan por permisos de IA.
