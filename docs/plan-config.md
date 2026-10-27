# Plan: pantalla de configuración

Sigue a `docs/plan-web.md`. Objetivo: que quien instale Epsilon cargue sus
claves desde la web, sin editar el `.env` a mano.

## Qué se configura

| Ajuste | Editable | Cuándo se aplica |
|---|---|---|
| `GEMINI_API_KEY` | sí | al instante (`llm.py` lee el entorno en cada llamada) |
| `GEMINI_MODEL` | sí | al instante |
| `TELEGRAM_TOKEN` | sí | al reiniciar |
| `ALLOWED_USER_ID` | sí | al reiniciar |
| `WEB_PORT` | sí | al reiniciar |
| `WHISPER_LANGUAGE`, `WHISPER_IDLE_MINUTES` | sí | al instante (`transcribe.py` los lee al usar el modelo) |
| `WHISPER_DEVICE` | sí | al reiniciar (o al descargarse el modelo) |
| `DATA_DIR` | no, solo se muestra | cambiarlo en caliente dejaría las notas viejas atrás |

La pantalla también muestra el estado de cada canal (activo, sin configurar,
detenido con su error).

## Decisiones

- **El `.env` es la única fuente.** Se escribe con `dotenv.set_key` (conserva
  comentarios y claves ajenas), con permisos `0600`. Está en `.gitignore`.
- **Las claves nunca vuelven al navegador, ni parcialmente.** La API devuelve
  `{"set": true, "length": N}` y la página dibuja N puntos. Los puntos van como
  *placeholder* del campo, nunca como su valor: el campo real queda vacío, y
  vacío significa «no tocar». Para borrar una clave hay un botón aparte.
- **Validación estricta antes de escribir**, todo o nada. Un valor con salto de
  línea podría inyectar otras variables en el `.env`. Token de Telegram con su
  formato, ID solo dígitos, puerto 1–65535, modelo `[\w.-]`, sin saltos de línea.
- **Borrar = escribir el valor vacío** (`KEY=""`), no quitar la línea. Así el
  aviso de reinicio detecta la diferencia sin estado extra.
- **Botones «Probar» que no gastan tokens:** Telegram con `getMe` (gratis);
  Gemini con los metadatos del modelo (`models.get`), dentro de `llm.py`.
  Solo corren cuando el usuario los toca.
- **Reinicio manual, nunca desde la web.** Si el proceso se cae, un botón
  dentro de la misma web no sirve. La pantalla muestra un aviso persistente,
  por cada ajuste cambiado, con la razón concreta («Telegram lee el token una
  sola vez al arrancar») y el comando: `systemctl --user restart epsilon`, o
  cerrar y volver a ejecutar `uv run epsilon`. El aviso sale de comparar lo
  guardado en el `.env` con lo que el proceso tiene cargado: sobrevive a
  recargar la página y desaparece solo tras reiniciar.
- **Si algo falla al arrancar, se ve por qué** (consola y journal): una línea
  clara por canal con qué ajuste revisar (token rechazado, puerto ocupado...),
  en vez de un traceback suelto; y el mismo error queda visible en el estado
  de canales de la pantalla.
- **Primer arranque:** si falta la clave de Gemini, el chat muestra un aviso
  con enlace a la configuración.

## Seguridad

Es la pantalla más sensible: puede cambiar quién tiene permiso en Telegram.
Valen Host, Origin y JSON, y ninguna respuesta incluye secretos. Límite
conocido: cualquier programa local de la PC que hable con `localhost:8765`
también podría cambiarla, porque no hay login. Aceptado para uso personal.

## Pasos (cada uno con tests y commit)

1. **`config.py`:** lista de ajustes, validación, lectura de estado (sin
   valores secretos), escritura segura y cálculo de «pendiente de reinicio».
2. **API:** `GET /api/config` y `PUT /api/config` (cambios parciales). Los
   errores de validación no repiten lo recibido (FastAPI por defecto devuelve
   el cuerpo en el 422, y ahí podría volver una clave).
3. **Estado de canales y errores claros al arrancar** en `main`; `llm.check()`,
   la prueba de Telegram y `POST /api/config/test/{gemini|telegram}`.
4. **Página** `config.html` / `config.js`: campos enmascarados, aviso de
   reinicio con sus razones, estado de canales y aviso de primer arranque.
   Se revisa en el navegador.

## Fuera de alcance

Login, formularios para otros canales, cambiar `DATA_DIR`, elegir el modelo de
una lista, mostrar el gasto en esta pantalla (`/uso` ya existe).

## Estado

Los 4 pasos están hechos. Verificado con arranques reales: token inválido de
Telegram (motivo claro, sin el token en el log, canal «detenido» en la API),
puerto ocupado (mensaje claro, código 1), `models.get` de Gemini contra la API
real (clave y modelo válidos; modelo inexistente explicado) y `getMe` de
Telegram con un token falso. La página (`/config`) se verificó en un navegador con una instancia aparte,
archivo de configuración de prueba y claves falsas: dibujo de puntos según el
largo (el campo real queda vacío), validación con el error junto al campo,
aviso de reinicio con su razón, pruebas de conexión contra Google y Telegram,
borrado en dos pasos y aviso de primer arranque en el chat. Escribir sobre el
`.env` real queda sin probar a propósito (lo maneja el dueño a mano).

## settings.json (ajustes fuera del `.env`)

**Para quien instala Epsilon.** El `.env` queda solo para lo secreto (claves, token), `ALLOWED_USER_ID` y `DATA_DIR`.
Todo lo demás vive en `DATA_DIR/settings.json`, un archivo de texto con comentarios (como el de VS Code). Junto a él hay
dos archivos que Epsilon regenera solo y no hay que tocar: `settings_default.json` (todos los ajustes con su valor por
defecto y su explicación) y `settings.schema.json` (para que el editor autocomplete y avise de errores). Para cambiar
algo: copiá la línea de `settings_default.json` a `settings.json` y editá el valor. Se abre con `/ajustes` o con el botón
«Abrir settings.json» de Configuración. La primera vez que arranca, Epsilon pasa solo a `settings.json` los ajustes que
tenías en el `.env`.

**Precedencia:** variable de entorno del proceso > `settings.json` > valor por defecto.

**Aplicar cambios.** Un hilo mira el archivo cada 2 s y aplica lo que cambió, sin reiniciar. Los ajustes que AFLOJAN un
límite (más pasos de herramientas, topes más altos, más sitios permitidos…) piden reiniciar el servicio, salvo que el
cambio lo ENDUREZCA (`store.tightens`): bajar un tope o bloquear un sitio se aplica al instante, porque nunca da más
poder. Los pendientes de reinicio aparecen en el aviso de Configuración. Si guardás el archivo roto (una coma de más) con
Epsilon andando, se siguen usando los últimos valores buenos y se avisa; al arrancar, un archivo roto se informa con su
línea. Un valor inválido o fuera de rango se ignora y se avisa, sin tirar el resto.

**La IA no tiene acceso** a `settings.json`: ninguna herramienta lo lee ni lo escribe.

**Notas para desarrollo.**
- Registro: `settings/config.py` (nombre del `.env`, nombre JSON `sección.clave`, tipo, rango, secreto). Se lee siempre con
  `config.value(...)` / `config.number(...)`, nunca con `os.environ` (un test de estructura lo vigila). `config.environ()`
  es lo que reciben los canales y plugins como `env`.
- `settings/store.py` es el único que escribe `settings.json` (`jsonc.py` lo lee y `editor.py` hace ediciones mínimas sin
  perder comentarios); `tunables.py` marca cuáles aflojan; `generate.py` arma el default y el schema. Los números sin valor
  por defecto ni valor habitual se omiten de `settings_default.json` pero están en el schema.
- Plugins: sus ajustes se llaman `plugin.<nombre del plugin>.<clave>` y se migran del `.env` cuando el plugin se registra.
- `EPSILON_ENV_FILE` sigue sirviendo para probar con otro `.env`.
