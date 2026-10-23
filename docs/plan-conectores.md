# Conectores (MCP)

Un **conector** es un servidor MCP (por stdio) que Epsilon lanza como proceso y del que usa sus herramientas.
Sirve para enchufar cosas (luces, una agenda, tu propio script) sin tocar el código de Epsilon. No es un plugin de
canal (ver `plan-plugins.md`): no es una forma de hablar con Epsilon sino algo que Epsilon puede usar.

Código en `src/epsilon/connectors/`: `store.py` (lista guardada), `manager.py` (procesos y llamadas), `bridge.py`
(herramientas MCP → herramientas de Epsilon), `registry.py`, `buttons.py` (botones y sugerencias).

## Agregar uno

1. Página **Conectores** → agregar. Nombre en minúsculas (`luces`, de 2 a 20 caracteres, sin «__») y el comando
   completo, p. ej. `uv run python docs/ejemplos/luces_mcp.py`.
2. Epsilon pide **confirmar**: el comando corre en tu máquina con tus permisos. Confirmá solo lo que entendés.
3. Al prenderlo, Epsilon pregunta qué herramientas tiene. Cada una queda en **preguntar** en `/permisos`; vos
   decidís cuáles pasan a permitir. Si un conector cambia una herramienta (nombre, descripción o esquema), vuelve a
   preguntar. Apagar un conector olvida los permisos que le diste.
4. La IA las ve como un grupo propio con acciones `mcp_<conector>__<acción>`.

Variables de entorno (claves del conector): se cargan en la página, se guardan en `DATA_DIR/connectors.json` y no
vuelven a mostrarse (solo si están y su largo).

## `/c`: usarlo sin modelo

`/c` lista los conectores. `/c conector herramienta clave=valor ...` (o un JSON) ejecuta una herramienta sin IA y sin
permisos de la IA: lo escribiste vos. Los valores se convierten según el esquema (`nivel=3` → entero).

## Botones

En la página, un botón es una herramienta con argumentos ya puestos que tocás para ejecutarla (sin modelo; se guardan
en `DATA_DIR/buttons.json`, máx. 40). Se validan contra el esquema al crearlos y otra vez al tocarlos.

Un conector puede **sugerir** botones con el recurso `epsilon://buttons`: un JSON con una lista de
`{"label": "...", "tool": "...", "args": {...}}`. Epsilon solo muestra las que cuadran con una herramienta real y sus
argumentos (máx. 10, rótulo de hasta 40 caracteres); nunca crea nada solo. Ver el ejemplo.

## Seguridad

- **La IA no puede agregar, cambiar ni prender conectores**: solo la persona, desde la página, con confirmación.
- **Entorno mínimo**: el proceso recibe `PATH`, `HOME` y `LANG` más lo que cargaste para ese conector. Las claves de
  Epsilon (Gemini, Telegram…) nunca llegan. (El SDK suma `LOGNAME`, `SHELL`, `TERM` y `USER`.)
- **Lo que devuelve un conector es dato**, no instrucciones; va redactado, sin tus claves, y recortado.
- Permisos por herramienta como el resto (`permitir` / `preguntar` / `bloquear`); el navegador y los demás topes
  siguen valiendo. Los argumentos se validan contra el esquema antes de llamar.
- Un conector corre con tus permisos de usuario: solo instalá los que confiás.

## Ciclo de vida

Perezoso: arranca la primera vez que se usa y se cierra solo tras `IDLE_CLOSE` (600 s) sin uso. Hay a lo sumo
`MAX_RUNNING` (3) abiertos a la vez; con más, se pide probar en un rato. Cada llamada tiene un tiempo máximo. Si el
proceso se cae, la próxima llamada lo vuelve a abrir. Con «autostart» arranca al iniciar Epsilon. Al cerrar Epsilon se
cierran todos. Un máximo de 10 conectores guardados.

## Envolver tu propio script

Con el SDK (`mcp`, ya instalado en el proyecto):

```python
from mcp.server.mcpserver import MCPServer

server = MCPServer("miscript")

@server.tool()
def hacer(algo: str) -> str:
    """Qué hace (lo lee el modelo)."""
    return "listo"      # imprimí logs en stderr: stdout es del protocolo

if __name__ == "__main__":
    server.run()
```

Tipá bien los argumentos (`Literal["on", "off"]`, `int`): de ahí sale el esquema que valida Epsilon. Ejemplo completo,
con el recurso de botones, en `docs/ejemplos/luces_mcp.py` (no toca nada real: solo escribe en stderr).
