# Plan: canales como plugins

Diseño: `docs/superpowers/specs/2026-10-20-channel-plugins-design.md`. Plan de implementación:
`docs/superpowers/plans/2026-10-20-channel-plugins.md`. Este archivo es la guía para quien escribe
o instala un plugin.

## Qué es (y qué no)

Un **plugin de canal** es una forma nueva de hablar con Epsilon (Discord, WhatsApp…). Es una carpeta;
Epsilon la encuentra sola, la muestra en la página **Canales** apagada y la prendés desde ahí.

- Las **integraciones** (Obsidian, Calendar, luces…) son **conectores MCP**, que no son plugins de canal:
  ver `plan-conectores.md`.
- Memoria y voz son funciones del núcleo, no plugins.

## Instalar uno

1. Copiá su carpeta a `DATA_DIR/plugins/` (por ejemplo `~/epsilon-data/plugins/discord/`).
2. Abrí **Canales**: aparece apagado (si el manifiesto está mal, aparece «Plugin inválido» con el motivo).
3. Cargá sus ajustes en **Configuración** (token, ID…).
4. Prendé el interruptor. La primera vez la página avisa que el canal corre **dentro de Epsilon y puede
   ver tus claves**: prendelo solo si confiás en quien lo escribió.

Listar un plugin solo lee su `plugin.toml`; el código recién se ejecuta cuando lo prendés (o cuando el
servicio arranca con el plugin ya prendido). Si el código falla, el canal queda «detenido» con el motivo
y la web y los demás canales siguen.

## La carpeta

```
DATA_DIR/plugins/discord/
  plugin.toml     # manifiesto: solo datos
  channel.py      # el código
```

### `plugin.toml`

| Campo | Obligatorio | Regla |
|---|---|---|
| `name` | sí | igual al nombre de la carpeta; `^[a-z][a-z0-9_]{1,19}$`; no puede ser `web` ni `telegram` |
| `label` | sí | cómo se muestra; hasta 40 caracteres |
| `about` | sí | una línea para la tarjeta; hasta 200 caracteres |
| `entry` | no | archivo del código; por defecto `channel.py`; tiene que quedar dentro de la carpeta |
| `[[settings]]` | no | hasta 10 ajustes (abajo) |

Cada `[[settings]]`: `key` (obligatoria, empieza con `NOMBRE_` en mayúsculas; no puede ser `NOMBRE_ENABLED`
ni una clave de Epsilon ni de otro plugin), `label` (obligatorio), `secret` (por defecto `false`: si es
`true` nunca se muestra, solo si está cargado y su largo, y se tacha de logs y errores), `pattern`
(expresión regular que el valor tiene que cumplir entero; por defecto cualquier texto de una línea de
hasta 200 caracteres) y `message` (qué se dice si no cumple).

Epsilon agrega solo `NOMBRE_ENABLED` (el interruptor, «no» por defecto).

### `channel.py`

Tiene que exponer:

- `is_configured(env) -> bool`: ¿tiene todo lo que necesita? (`env` es `os.environ`).
- `run()`: arranca el canal y **bloquea** hasta que termina. Corre en su propio hilo.
- `stop()`: pide que `run()` termine. Sin `stop` el canal no se podría apagar desde la web (el plugin
  se rechaza al cargarlo).

Y puede exponer `reset()` (se llama antes de cada arranque, para limpiar un pedido de apagado viejo) y
`explain(error) -> str | None` (traduce un error a un mensaje claro).

Para hablar con Epsilon:

- `core.handle(core.Message(text=..., session_id="<nombre>:<chat>")) -> core.Reply`. El núcleo no conoce
  ningún canal.
- `conversations.for_channel("<nombre>")` y `conversations.add_message(...)` para que lo que se habla
  quede en una conversación de canal que la web puede leer (ver `channels/telegram.py`, `log_turn`).
- `state.set("<nombre>", "running", "Conectado.")` cuando ya está conectado. Sin eso la página muestra
  «Conectando…» (Telegram hace lo mismo).

Ejemplo mínimo:

```toml
# plugin.toml
name = "eco"
label = "Eco"
about = "Canal de ejemplo: lee líneas de un archivo y las pasa al núcleo."

[[settings]]
key = "ECO_ARCHIVO"
label = "Archivo de entrada"
```

```python
# channel.py
import threading

from epsilon import core, state

_stop = threading.Event()


def is_configured(env):
    return bool(env.get("ECO_ARCHIVO"))


def run():
    state.set("eco", "running", "Conectado.")
    # …acá iría el bucle del canal: leer mensajes, core.handle(core.Message(...)), responder…
    _stop.wait()


def stop():
    _stop.set()


def reset():
    _stop.clear()
```

## Cómo funciona por dentro

- `plugins/channels.py`: lee y valida manifiestos (`read_manifest`, `discover`), importa el código
  (`load_module`) y arma sus ajustes (`settings_for`).
- `main.refresh_plugins()` mantiene `CHANNELS` al día (se llama al arrancar y cada vez que la página
  Canales pide el estado). Un plugin que desaparece del disco sigue hasta que lo apagues.
- `settings/config.py` suma los ajustes de los plugins (`set_plugin_settings`). El secreto de un plugin
  se sigue tachando aunque su carpeta se borre durante la vida del proceso.
- `data/conversations.py` acepta cualquier nombre con la forma de plugin como canal; el título fijo de
  su conversación es el nombre con mayúscula inicial.
- La web exige confirmación para prender un plugin (`428` si falta `confirmed`).

## Límites conocidos

- No hay aislamiento: el código corre en el proceso de Epsilon.
- No hay tienda, descarga automática ni versiones: se copia la carpeta.
- Un plugin nuevo aparece al recargar Canales (o en pocos segundos: la página consulta cada 4 s).
