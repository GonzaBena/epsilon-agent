# Skills — diseño

Fecha: 2026-10-25. Estado: borrador para aprobar (no hay código todavía).

## Qué es

Una **skill** es una carpeta con un `SKILL.md` (formato estándar: frontmatter con `name` y `description`, y
debajo las instrucciones en texto). Le enseña a Epsilon a hacer algo «a la manera de la persona» (un informe con
cierto formato, un correo con cierto tono). Es solo texto: no da permisos ni ejecuta nada.

Costo: cero mientras no se use. A la IA solo le llega la lista `nombre: descripción corta`; el cuerpo se carga
cuando hace falta.

## Dónde viven

`DATA_DIR/skills/<nombre>/SKILL.md` (más archivos de texto/datos de apoyo si vinieron en un `.zip`).
El nombre de la carpeta es el nombre de la skill y el comando: minúsculas, dígitos, `-` y `_`, 1–32 caracteres.
Todo acceso por `safe_path`. Módulo nuevo: `src/epsilon/skills/` (`store.py`, `importer.py`, `runtime.py`).

## Cómo se usan

1. **A mano, `/<skill>`** (con o sin pedido: `/correo avisar a Marta que la reunión pasa al jueves`).
   Funciona en web y Telegram. Carga el cuerpo en ese mensaje y lo manda a la IA (como `/ia`, con la skill
   puesta). Usa IA: sale marcada así en la ayuda y se anota en Uso («Skill»).
2. **La IA la elige sola**: la lista (tope de cantidad y de largo) va en el prompt del sistema y la acción
   `skills.usar nombre` devuelve el cuerpo. Es un grupo de IA propio (`skills`), de solo lectura, permiso
   por defecto `allow`, consistente con `tools.AI_GROUPS`.
3. Un comando que ya existe **gana siempre**. Al crear o importar una skill cuyo nombre choca con un comando
   (o con uno reservado), se rechaza con un mensaje claro. Las skills salen en `/ayuda`, `/menu` de Telegram y
   el panel de comandos de la web, tomadas del registro de `tools.py` en tiempo de ejecución (no entradas fijas).
4. En un chat de modo IA, un `/algo` que sea una skill llega a la IA (arregla en parte la limitación conocida de
   «mensaje con `/` muestra la ayuda»).

## Cómo se agregan (solo la persona)

Página **Skills** (`skills.html`, `js/skills.js`), lista al estilo Conectores: nombre, descripción corta,
interruptor (prendida/apagada) y menú (Ver, Editar, Borrar). Botón «Agregar skill» con tres caminos:

- **Archivo**: un `SKILL.md` suelto o un `.zip`.
- **URL**: se baja el `SKILL.md` (http/https, tope de tamaño, sin redirigir a la red local).
- **Crear conversando**: abre un chat nuevo con IA con un prompt inicial; al terminar, la IA propone el
  `SKILL.md` completo y la persona lo revisa y toca «Guardar». La IA nunca escribe en disco.

Siempre se muestra el texto exacto y hay que confirmar antes de guardar (el servidor lo exige:
`confirmed: true`). Al confirmar se guarda un hash; si el archivo cambia en disco después, la skill queda
apagada hasta volver a confirmar (igual que los conectores).

## Seguridad

- Solo la persona agrega, cambia o prende skills; la IA no tiene ninguna herramienta que lo haga.
- El contenido es **dato rotulado**, no órdenes del sistema; igual que notas e imágenes.
- `.zip`: anti zip-slip (rutas normalizadas dentro de la carpeta), tope de tamaño total y de cantidad de
  archivos, solo extensiones de texto/datos; los scripts (`.py`, `.sh`, …) no se extraen ni se ejecutan y se
  avisa que había.
- Una skill no concede permisos: cualquier herramienta que la IA use por ella pasa por `permissions` y
  `core._run_calls` como siempre.
- Todo lo que viene del servidor se dibuja con `textContent`.
- Lo guardado pasa por `config.redact`.

## De fábrica (se copian la primera vez)

Al primer arranque se copian a `DATA_DIR/skills/` (marca `skills/.seeded` para no repetir; si las borrás no
vuelven): `informe` (resumen de informe semanal: hecho, pendiente y problemas, pocas líneas), `correo`
(redactar un borrador con tono y largo pedibles; no envía nada) y `prioridades` (organizador de tareas por
prioridad; solo propone el orden, no toca las notas). Las tres son solo texto, sin herramientas.

## API

`GET /api/skills`, `POST /api/skills` (archivo/URL/texto + `confirmed`), `PUT /api/skills/{nombre}`
(interruptor / edición), `DELETE /api/skills/{nombre}`. Un 422 trae el motivo en español.

## Orden de construcción

1. Almacén, validación, hash y `/<skill>`.
2. Grupo de IA `skills` y lista en el prompt.
3. Página Skills, agregar por archivo, `.zip` y URL.
4. Crear conversando.
5. Skills de fábrica.

Cada paso con sus tests (sin llamar a un modelo) y commit.

## Fuera de alcance

Ejecutar scripts de una skill, catálogo/tienda online de skills, que la IA modifique skills, compartir entre
personas. Las skills con herramientas propias se verán junto al catálogo de conectores MCP.
