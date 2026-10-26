# Catálogo de conectores — diseño

Fecha: 2026-10-26. Estado: borrador para aprobar (no hay código todavía). Sale de la charla del 2026-10-26 (paso 3 del rumbo para otros usuarios).

## Para qué

Hoy agregar un conector MCP exige escribir el comando a mano. Para quien solo quiere usar Epsilon eso es una pared. La
persona va a la página Conectores, ve los que ya tiene y los que puede instalar, toca «Instalar» y queda andando, sin
escribir ningún comando. Quien quiere trastear sigue pudiendo agregar a mano y puede sumar sus propias entradas.

## Objetivos

- Pestaña **Catálogo** en `/conectores` (junto a «Tuyos» y «Botones»): una fila por conector instalable, con «Instalar» o «Instalado».
- Un conector de **ejemplo** que funciona sin cuentas ni claves (el de las luces de mentira), para ver cómo es.
- Instalar = lo mismo que agregar a mano: el catálogo solo rellena nombre, comando y claves. Sigue el aviso «este programa va a
  correr en tu PC» y la confirmación explícita (el servidor la exige).
- Si el conector pide claves (un token), el diálogo las pide en el momento; nunca salen del servidor (como hoy).
- Sin red propia: la lista viene incluida en Epsilon y, opcionalmente, de un archivo de la persona.

## No-objetivos

- Que la IA instale o prenda conectores: sigue siendo solo la persona.
- Una lista bajada de internet o el registro público de MCP (no revisado): queda para más adelante, con firma o hash.
- Conectores que necesitan iniciar sesión con OAuth (Gmail, Calendar, Drive): no entran en esta etapa.
- Descargar o copiar código dentro de Epsilon: el conector baja su paquete solo al primer uso (`uvx`/`npx`), como hoy.
- La guía en la web (página aparte; el catálogo solo puede enlazarla).

## Anti-objetivos

- Que «Instalar» corra algo sin que la persona haya visto el comando exacto.
- Que un archivo de catálogo mal escrito rompa la página o instale algo distinto de lo que se ve.

## Cómo funciona

**Fuentes** (se juntan en una sola lista; la segunda gana si repite `id`):
1. `src/epsilon/connectors/catalog.json`: incluida en Epsilon, la mantiene el proyecto.
2. `DATA_DIR/catalog.json` (opcional): la persona suma o pisa entradas. Mismo formato.

**Una entrada** (todo texto de una línea, con topes de largo):
`id` (letras/números/guion bajo), `title`, `description`, `name` (nombre sugerido del conector; cumple las reglas de
`connectors/store.py`), `command` (lista de palabras; nunca un shell), `env` (lista de `{key, label, secret, required}`),
`needs` (programas que tienen que existir, p. ej. `uvx`; se buscan con `shutil.which`), `homepage` (https, opcional).
En `command` solo se aceptan dos marcadores, que Epsilon reemplaza al instalar: `{python}` (el Python de Epsilon) y
`{examples}` (la carpeta `connectors/examples/` del paquete). Una entrada inválida se descarta sin romper las demás.

**Instalar**: `POST /api/catalog/{id}/install` con `{name?, env, confirm: true}`. Sin `confirm` es 422. Valida que lleguen las
claves `required`, que existan los `needs` y arma el comando; después hace exactamente lo que `POST /api/connectors`
(`store.add` + arrancar). Si el nombre ya existe, 422 con el motivo; la fila muestra «Instalado» cuando hay un conector con ese `name`.

**El diálogo** muestra título, descripción, el comando exacto (ya con los marcadores resueltos), los campos de claves y el aviso,
y recién con «Confirmar e instalar» se llama a la API.

**Entradas iniciales** (decisión mía, a revisar): el ejemplo `luces` (se mueve a `src/epsilon/connectors/examples/` para que
viaje con el paquete) y los dos que la página ya ofrece como ejemplos del formulario: `fetch` (`uvx mcp-server-fetch`) y
`sqlite` (`uvx mcp-server-sqlite --db datos.db`). Sumar más es trabajo de contenido, uno por uno y verificado.

## Seguridad

- El catálogo no ejecuta nada al listar; solo instalar lo hace, con confirmación del servidor.
- `command` es una lista (sin shell); los marcadores son los únicos reemplazos; los `env` pasan por `store._check_env`.
- Las entradas de la persona son suyas: se marcan «tuyo» en la fila. Lo incluido se marca «incluido».
- Todo texto del catálogo llega a la web con `textContent`; las claves nunca vuelven del servidor.

## Lo que queda al implementador

Nombres internos, el estilo exacto de las filas (reutilizar las `claude-*` de Conectores), y los textos de los errores.
