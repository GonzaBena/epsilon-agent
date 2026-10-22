# Herramientas de IA agrupadas

## Por qué
Con 17-19 herramientas el prompt crecía y el modelo (sobre todo uno chico) elegía peor. Ahora ve 5 grupos.

## Diseño
- Fachada: por dentro nada cambia. Cada acción es una herramienta de `tools.TOOLS` con su permiso
  (`permissions.json`, `/permisos`, preguntas de confirmación). Solo cambia lo que se le declara al modelo.
- `Tool.ai_group` / `Tool.ai_action` + `tools.AI_GROUPS` (grupo -> para qué sirve).
- `chat._specs()` arma un `ToolSpec` por grupo (`actions` = las acciones no bloqueadas; la descripción junta los
  `ai_help` de cada acción). Un grupo sin acciones permitidas no se ofrece.
- Los proveedores (`gemini.py`, `ollama.py`) declaran `accion` (enum, requerido) + `args` con `ToolSpec.parameters()`
  y leen la llamada con `messages.parse_call`. `Step.calls` entrega `grupo.acción`; `Call.action` guarda la acción
  para reenviar el historial.
- `core._run_calls`: `tools.route(id)` -> herramienta interna (permiso, ejecución, etiqueta de la pregunta). El
  resultado vuelve con `tools.model_name(id)` (el grupo). Un nombre interno suelto, una acción inventada o una
  herramienta bloqueada nunca corre.

## Grupos
| Grupo | Acciones (herramienta) |
|---|---|
| notas | guardar (nota), guardar_con_nombre (nota_nombrada), editar línea (nota_editar), leer (ver), buscar |
| archivos | listar (archivos), enviar, resumir, csv |
| memoria | agregar (recordar), ver (memoria), editar, borrar (olvidar) |
| consultar | fecha, dolar, clima, sistema |
| pc | volumen, sonando, musica, ventanas |
| rutinas | crear (rutina_crear), ver (rutina_ver), borrar (rutina_borrar) |

## Las tareas son una nota
Primero hubo un grupo `tareas`, pero el modelo lo confundía con una lista con nombre («la lista de la compra» iba a
`/todo`). Como `todo.md` ya es una nota de casillas, la IA ya no tiene herramientas de tareas: agrega con
`notas.guardar_con_nombre` (nota `todo`, `- [ ] texto`), lee con `notas.leer` y marca con `notas.editar`
(`- [ ]` -> `- [x]` en esa línea). `/todo`, `/tareas` y `/hecho` siguen para la persona.

`notas.editar` (`nota_editar`, permiso «preguntar») es la única excepción a «nunca pisar»: cambia una línea por su
número (`notes.edit_line`; con texto vacío la borra), con archivo temporal y `.bak`. `leer` numera las líneas para la IA.
La primera versión reemplazaba un fragmento exacto (`viejo`, `=>`, `nuevo`) y un modelo de 4B no lo copiaba bien. Memoria, archivos y consultas quedan como están, para no
perder precisión ni permisos por herramienta.

## Medición
Declaraciones de Gemini: 17 herramientas / 5.323 caracteres -> 5 grupos / 4.669 (-12%); después, sin el grupo de tareas, 4 grupos. La ganancia esperada es
sobre todo en la elección de herramienta, no en el tamaño.

## Plan B
Si un modelo local no sigue el enum de `accion`: que la acción sea la primera palabra de `args` (sin tocar proveedores).

## Pendiente
Probar con modelos reales (Gemini y Ollama): que elijan bien grupo y acción. Ofrecer solo los grupos que vienen
al caso según el mensaje, si todavía fuera mucho.
