# Canales como plugins: diseño

Fecha: 2026-10-20. Estado: borrador para aprobar.

## Por qué

Hoy los canales (web, Telegram) están escritos a mano en `main.CHANNELS`, en `settings/config.py`
(`TELEGRAM_ENABLED` y las claves), en `canales.js` y `shell.js` (nombres, descripciones, íconos) y en
`conversations.CHANNELS`. Sumar un canal (Discord, WhatsApp…) obliga a tocar el núcleo en cuatro
lugares. Si alguien quiere escribir un canal, o instalar el de otra persona, no debería hacer falta
tocar nada de Epsilon: copiar una carpeta, verlo en la página Canales y prenderlo.

## Alcance

**Entra:** canales de terceros como plugins (este documento).

**No entra (proyecto aparte, para después del modo chat con IA):** integraciones. Serán servidores
MCP: Epsilon como cliente MCP y los comandos de `tools.py` expuestos como herramientas de la IA. Ver
memoria `plugins-mcp-future`. Nada de este diseño debe estorbar a eso.

**Memoria y voz no son plugins**: son funciones del núcleo.

## Objetivos

- Un plugin de canal es una carpeta. Instalarlo = ponerla en `DATA_DIR/plugins/` (a mano o con un
  botón futuro). Sin tocar el repo ni editar archivos de Epsilon.
- Aparece en la página Canales **apagado por defecto**. Se prende desde ahí, como Telegram.
- Sus ajustes (token, ID…) se declaran en su manifiesto y se editan con la misma página y la misma
  validación, redacción de claves y escritura del `.env` de siempre.
- Los canales incluidos (web, Telegram) siguen funcionando igual; Telegram puede quedar como está o
  pasar al mismo contrato, pero no es obligatorio en esta etapa.

## No objetivos

- Tienda, descarga automática, versiones, actualizaciones de plugins.
- Aislar el plugin del proceso (un canal corre dentro de Epsilon y ve el entorno; se avisa, no se
  impide).
- Plugins que agreguen páginas web, comandos o herramientas (eso es del proyecto MCP).
- Cambiar el modelo de seguridad: la web sigue solo en 127.0.0.1, las claves nunca salen del servidor.

## Anti-objetivos (fracaso aunque «funcione»)

- Que un plugin roto o malicioso a nivel de errores tumbe la web o a otros canales.
- Que listar plugins ejecute su código: ver un plugin en la página no debe correr nada suyo.
- Que una clave de un plugin llegue al navegador, a logs o a mensajes de error.
- Que prender un plugin sea más fácil de hacer sin querer que prender Telegram.

## Cómo es un plugin

```
DATA_DIR/plugins/discord/
  plugin.toml     # manifiesto: solo datos, se lee sin ejecutar nada
  channel.py      # el código; mismo contrato que channels/telegram.py
```

`plugin.toml`:

```toml
name = "discord"            # igual al nombre de la carpeta; ^[a-z][a-z0-9_]{1,19}$
label = "Discord"           # cómo se muestra
about = "El bot de Discord. Responde solo a tu usuario."
entry = "channel.py"        # opcional; por defecto channel.py

[[settings]]                # cada ajuste que necesita; la clave del .env debe empezar con NAME_
key = "DISCORD_TOKEN"
label = "Token de Discord"
secret = true
pattern = "[\\w.\\-]{20,100}"          # validación: regex de coincidencia completa
message = "El token no tiene un formato válido."
```

`channel.py` expone lo mismo que Telegram hoy:

- `is_configured(env) -> bool` (obligatorio)
- `run()` (obligatorio; bloquea hasta que el canal termina)
- `stop()` (obligatorio para plugins: sin él no se podría apagar desde la web)
- `reset()` y `explain(error) -> str | None` (opcionales, como ahora)

Para hablar con Epsilon usa `core.handle(Message) -> Reply`, igual que Telegram. No conoce la web.

## Cómo lo usa Epsilon

1. **Descubrir.** Al arrancar y cada vez que la página Canales pide `GET /api/channels`, se lee cada
   `DATA_DIR/plugins/*/plugin.toml` (solo el TOML, no se importa nada). Un manifiesto inválido no
   rompe nada: el plugin aparece como «Plugin inválido» con el motivo, y no se puede prender.
2. **Validar.** Reglas del manifiesto: nombre con el patrón y distinto de web/telegram y de los demás
   plugins; claves de ajustes con prefijo `NAME_` y sin chocar con las de Epsilon; `entry` dentro de
   la carpeta (por `safe_path`); regex compilable. Nada de esto es texto del usuario armando rutas a
   mano: todo pasa por `safe_path`.
3. **Registrar ajustes.** `config.py` suma los ajustes del manifiesto a `SETTINGS`/`BY_KEY` (con
   `page="canales"`), más un `NAME_ENABLED` con valor por defecto **«no»** (el de Telegram es «si»).
   Las claves `secret` de plugins entran en `config.redact` y en el filtro de logs como las demás.
4. **Mostrar.** `channels_state()` incluye también los plugins con su `label`, `about` y el origen
   («plugin»). La página Canales deja de tener nombres, textos e íconos escritos en JS para estos:
   los toma de la API. Los plugins usan un ícono genérico.
5. **Prender.** `PUT /api/channels/<nombre>` funciona igual. Al importar el código por primera vez
   (`importlib.util.spec_from_file_location`, porque la carpeta no está en `sys.path`) se hace dentro
   de `start_channel`, y cualquier excepción al importar se convierte en `ChannelError` (estado
   «detenido» con el motivo, la web sigue). Antes de prender un plugin por primera vez, la web pide
   confirmación con el aviso: «Este canal corre dentro de Epsilon y puede ver tus claves. Prendelo
   solo si confiás en quien lo escribió.»
6. **Arrancar.** `main.main()` arranca los plugins prendidos igual que Telegram. El canal principal
   sigue siendo la web (primero en el orden); los plugins van siempre después, cada uno en su hilo y
   con `run_isolated`.

## Conversaciones

`conversations.CHANNELS` hoy es `("web", "telegram")` fijo. Un plugin necesita guardar su charla como
conversación de canal (línea `meta`, solo lectura en la web). Se amplía para aceptar el nombre de un
plugin válido, con título fijo = su `label` y el ícono/color genéricos en la web (el estilo
`from-telegram` queda para Telegram). Se mantiene la regla de que la web solo lee las conversaciones
de otros canales.

## Cambios previstos por archivo

- `main.py`: `CHANNELS` pasa a ser incluidos + plugins descubiertos; `load()` sabe importar por ruta.
- Nuevo `plugins/channels.py` (carpeta de capa nueva, `from epsilon.plugins import channels`):
  descubrir, validar el manifiesto, cargar el módulo. Sin Telegram ni modelo.
- `settings/config.py`: registrar/quitar ajustes de plugins; `redact` los incluye.
- `channels/web.py`: `channels_state()` y `set_channel` con plugins; confirmación de primera vez.
- `channels/web_static/js/canales.js` y `shell.js`: nombres, descripciones e íconos desde la API.
- `data/conversations.py`: aceptar canales de plugins.
- `docs/` y `CLAUDE.md`: documentar el contrato del plugin.
- Tests en `tests/plugins/` con un plugin de mentira (carpeta temporal); nada llama a Gemini.

## Decisiones tomadas por mí (a revisar)

- Carpeta `DATA_DIR/plugins/`, fuera del repo, para que actualizar Epsilon no los pise.
- Manifiesto en TOML (`tomllib` ya viene con Python, sin dependencias nuevas).
- Descubrimiento sin ejecutar código; la importación recién al prender.
- Nuevo plugin detectado = aparece al recargar la página Canales, sin reiniciar. Quitar la carpeta de
  un plugin prendido: sigue corriendo hasta apagarlo o reiniciar, y después desaparece.
- Primera vez: confirmación con aviso. No se guarda que ya lo confirmaste: se pide cada vez que un
  plugin pasa de apagado a prendido desde la web (al reiniciar el servicio, un plugin que dejaste
  prendido arranca sin preguntar, como Telegram).
- Telegram no se migra al formato de plugin en esta etapa.

## Para el que lo construya

Detalles abiertos que resuelve quien arme el plan sin cambiar lo que se construye: forma exacta de la
confirmación en la UI, mensajes en español, qué hace `config.update` con una clave de un plugin que
desapareció del disco, y cómo se ordena la lista (incluidos primero, luego plugins por nombre).
