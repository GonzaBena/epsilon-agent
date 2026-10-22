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
| notas | guardar (nota), guardar_con_nombre (nota_nombrada), leer (ver), buscar |
| archivos | listar (archivos), enviar, resumir, csv |
| memoria | agregar (recordar), ver (memoria), editar, borrar (olvidar) |
| tareas | agregar (todo), ver (tareas), completar (hecho) |
| consultar | fecha, dolar, clima, sistema |

## Medición
Declaraciones de Gemini: 17 herramientas / 5.323 caracteres -> 5 grupos / 4.669 (-12%). La ganancia esperada es
sobre todo en la elección de herramienta, no en el tamaño.

## Plan B
Si un modelo local no sigue el enum de `accion`: que la acción sea la primera palabra de `args` (sin tocar proveedores).

## Pendiente
Probar con modelos reales (Gemini y Ollama): que elijan bien grupo y acción. Ofrecer solo los grupos que vienen
al caso según el mensaje, si todavía fuera mucho.
