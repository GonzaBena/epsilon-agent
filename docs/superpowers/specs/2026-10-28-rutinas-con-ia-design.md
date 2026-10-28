# Rutinas con IA: que Epsilon decida sola, a una hora, gastando lo mínimo

Estado: diseño para aprobar (2026-10-28). Nada construido todavía. Maqueta de la pantalla de permisos:
`2026-10-28-rutinas-ia-permisos-mockup.html` (en esta carpeta).

## Para qué (el porqué)

Hoy las rutinas hacen **pasos fijos sin modelo** (costo cero): `08:00 | volumen 15 > abrir yt > musica play`. Sirven cuando
todo se sabe de antemano. No sirven cuando hay que **decidir** con criterio: «a las 8, poné música para despertarme» exige
mirar la hora, lo que a la persona le gusta y qué hay en sus playlists.

Se quiere que una rutina pueda pedirle algo a la IA en lenguaje natural, **sin perder las dos cosas que hacen
confiable a Epsilon: el control de la persona y el costo mínimo**.

**Regla de diseño (la persona la pidió expresamente): esto es una función GENERAL, no «el despertador de una persona».**
El despertador es un ejemplo. La misma pieza tiene que servir para «resumime las notas de hoy y avisame», «a las 9 poné un
video de lo que suelo ver», «cada lunes ordename los pendientes» o cosas que hoy no imaginamos. Nada en el diseño ni en el
código puede asumir YouTube Music, un horario o un gusto en particular: todo sale de la instrucción de la persona, de su
memoria y de las herramientas que ella habilite.

## Metas, no metas y anti-metas

**Metas**
- Una rutina con IA = un activador (los de hoy: hora, condición) + una **instrucción** + las **herramientas permitidas**.
- Costo bajo y que baje con el uso: la IA completa corre **una vez** para aprender; después solo se llama al modelo donde
  hay que **elegir**, con un contexto mínimo.
- El control sigue en la persona: lo que puede usar la IA lo marca ella, de antemano, y es lo único que corre.
- Que cualquiera la configure a su manera: otras herramientas, otras instrucciones, otros modelos (Gemini u Ollama).

**No metas (esta versión)**
- Condiciones disparadas por IA, ni rutinas que la IA cree o edite sola: solo la persona crea, edita y confirma (igual que
  conectores y skills).
- Modelos de decisión especiales (p. ej. Jev, solo en la nube y todavía en alfa): el diseño deja la puerta abierta (el paso
  «elegir» es una interfaz chica) pero no se integra ninguno ahora.
- Repetir a ciegas pasos del navegador que dependen de la página (`clic 4`, `escribir`): los números de elemento cambian en
  cada carga. Esos pasos quedan «libres» (los decide la IA cada vez).

**Anti-metas (sería un fracaso aunque «ande»)**
- Que una rutina use una herramienta que la persona no marcó.
- Que gaste sin que la persona lo sepa (sin tope, o sin avisar cuánto).
- Que una rutina diga «puse X» sin que X haya pasado (el modelo chico mintió así en pruebas reales).
- Que la receta aprendida cambie sin que la persona la haya visto y confirmado.

## Cómo funciona (de punta a punta)

### 1. Crear: instrucción + grilla de permisos
En el editor de rutinas, además de nombre y cuándo, hay un campo **«Qué le pedís a la IA»** y debajo la **grilla de
herramientas** (ver maqueta). Cada botón tiene cuatro estados que cambian con cada toque:

| estado | significa |
|---|---|
| **normal** | no se usa (la IA no la ve) |
| **sugerida** (ámbar) | Epsilon cree que hará falta, pero NO está permitida hasta que la persona la confirme |
| **verde** | permitida: es lo único que la IA puede usar en esta rutina |
| **rojo** | bloqueada a propósito (un «nunca» explícito que queda a la vista) |

Ciclo del toque: sugerida/normal → verde → rojo → normal. Normal y rojo **se comportan igual** (la IA no puede usarlas); la
diferencia es solo que rojo es una decisión visible de la persona. *(Suposición mía, a confirmar al construir la pantalla.)*

**Sugerencias sin gastar IA:** salen de las palabras clave del texto, con el mismo mecanismo de `chat.preload` (grupos,
conectores, skills). Las herramientas **privadas** (`ask` por defecto: historial, ventanas) o que **escriben** (crear
notas, rutinas) **nunca** se sugieren solas.

**No hay «preguntar»:** nadie va a contestar a las 8. Una herramienta que no está en verde está bloqueada, y si la IA la
necesita, no la usa y lo cuenta.

**Confirmar al crear = permiso** (la regla que ya tienen las rutinas): al guardar, Epsilon muestra la instrucción, el
activador y las herramientas en verde, y la persona confirma. El servidor lo exige (`confirmed: true`).

### 2. Aprender (primera ejecución, «Aprender y probar»)
- Corre la IA como en el chat (`core._loop` / `_run_calls`), **solo con las herramientas en verde**, con el tope de pasos
  habitual. Es una ejecución de verdad (puede poner música): es la «Probar ahora» que ya existe, con el mismo aviso.
- Epsilon **registra** cada llamada: herramienta, argumentos y salida (recortada).

### 3. Proponer la receta (sin modelo)
Del registro se arma una cadena de pasos. Cada paso se clasifica mirando **de dónde salió su argumento**:

- **Fijo:** el argumento es el mismo texto que ya estaba en la instrucción o no depende de nada (`fecha`, `volumen 15`,
  `sonando`). Se repetirá tal cual, sin modelo.
- **Elegir:** el argumento es un **ítem que apareció en la salida de un paso anterior** (el id de una playlist que estaba en
  la lista de `musica_playlists`). La fuente es ese paso, y las **opciones** son los ítems de esa salida.
- **Libre:** ni una cosa ni la otra (el «lofi» que el modelo inventó para buscar; un `clic` por número). Epsilon le
  pregunta a la persona: **dejarlo fijo** («siempre buscar lofi») o **dejarlo a la IA** cada vez.

Para que «ítems de una salida» no sea una adivinanza, **cada herramienta declara qué es seleccionable en su salida** (campo
opcional del registro, p. ej. un patrón para los ids de `musica_playlists`, `musica_buscar` y `yt_historial`). Una
herramienta que no lo declara no puede producir pasos «elegir»: sus argumentos dependientes quedan «libres». Es
conservador a propósito y se amplía herramienta por herramienta.

La receta se muestra con una etiqueta por paso (**fijo · elige la IA · libre**) y la persona la **confirma** antes de que se
use. Sin confirmar, la rutina sigue corriendo con la IA completa.

### 4. Ejecuciones siguientes (lo barato)
- Los pasos **fijos** corren sin modelo, como las rutinas de hoy (`routines/runner`, `core.run_step`).
- Un paso **elegir** hace UNA llamada corta al modelo con: la instrucción, la hora local, las opciones (las líneas de la
  salida del paso fuente, con su descripción) y las preferencias de la persona (su memoria, recortada). Contesta con **un
  ítem**; el código verifica que sea una de las opciones. Si no, reintenta una vez; si sigue mal, cae al punto 5.
- Un paso **libre** deja que la IA lo resuelva con una llamada con herramientas, acotada a las verdes.
- Variar: las opciones se entregan en el orden que ya da cada herramienta (p. ej. `musica_playlists` pone primero la que
  hace más que no suena), así que «elegí la primera que encaje» rota sola.
- Al final, **una línea de aviso** con lo que hizo («Puse *new calm*: tranquila y de energía baja»).

### 5. Si algo falla (receta vieja)
Un paso fijo que falla, una salida sin opciones (YouTube cambió) o una elección inválida: **esa vez** se cae a la IA completa
(con el mismo tope), se avisa «la receta quedó vieja» y la rutina queda marcada para **volver a aprender** (la persona
confirma la receta nueva). Si la IA completa también falla, se avisa el error y no se reintenta solo.

## Costo y topes
- Cada ejecución anota su gasto como «Rutina IA» en `/uso` (y con modelo local es 0). Sin precio conocido del modelo, la
  rutina con IA **no corre** (como el límite de los chats): nunca gasta a ciegas.
- **Tope por ejecución** (pasos de herramientas y tokens de salida) y **tope diario por rutina** en US$. Al llegar al
  tope diario, esa rutina avisa y se salta hasta mañana. *(Los valores por defecto los propone quien construye; deben ser
  ajustes en `settings.json`.)*
- Mostrar en la lista de rutinas cuánto gastó hoy y la última vez.

## Seguridad
- Solo corren las herramientas en verde; todo lo demás se bloquea (nunca «preguntar»). Las herramientas de conectores llevan
  su `version` (hash) como hoy: si cambia, el paso no corre.
- Siguen los bloqueos duros de `navegador.py` y de `BROWSER_BLOCKED_SITES`. La IA nunca corre JavaScript propio.
- Lo que una herramienta devuelve (títulos de playlists, páginas) es **dato, no instrucciones**: el paso «elegir» solo puede
  devolver un ítem de la lista, no texto libre que viaje a otro paso.
- La IA no puede crear, editar ni prender rutinas con IA. La receta solo la cambia la persona.
- Los pasos que escriben (crear nota, etc.) pueden estar en verde solo si la persona los marcó; nunca se sugieren.

## Datos
Una regla de `DATA_DIR/routines.json` suma `ai` (la persona la ve y puede editarla):
`{"instruction": "...", "tools": {"musica_poner": "green", "yt_historial": "red"}, "recipe": [...pasos con kind fijo|elegir|libre...],
"learned_at": "...", "stale": false, "spent_today": ..}`. Las reglas viejas no cambian. Reescritura por archivo temporal y
`store.update` con candado, como hoy.

## Piezas y fases (cada una útil por sí sola, con sus tests y su commit)
1. **Regla con IA + grilla + corrida completa con tope.** Ya sirve: la IA corre a la hora con las verdes y avisa. Sin receta
   todavía (cada ejecución usa la IA completa).
2. **Registro y clasificación** (puro, sin modelo): del registro de una ejecución a la receta (fijo / elegir / libre), con la
   declaración de ítems seleccionables en las herramientas de música.
3. **Repetir la receta:** pasos fijos sin modelo, paso «elegir» con llamada mínima, caída a la IA completa y marca de «vieja».
4. **Pantalla:** editor con el campo de instrucción, la grilla de cuatro estados, la sugerencia por palabras clave, la
   confirmación y la vista de la receta.

## Qué se deja a quien construye
- Dónde vive el código (`routines/` sin importar el núcleo, como hoy; la parte que llama al modelo en `ai/`), nombres y forma
  exacta del registro.
- Qué tarea de modelo usa el paso «elegir» (hoy no hay una dedicada; lo razonable es reutilizar la de resúmenes y sumar un
  ajuste solo si hace falta) y los valores por defecto de los topes.
- Cuánta memoria entra al contexto de «elegir».
- Cómo se extraen exactamente las opciones de una salida (lo que cada herramienta declare).

## Decisiones tomadas por mí (para revisar)
- Normal y rojo se comportan igual; rojo es solo un «nunca» visible.
- Sin «preguntar» en rutinas con IA: o está en verde o está bloqueada.
- Una receta no se usa hasta que la persona la confirma; hasta entonces corre la IA completa.
- Un solo intento de caída a la IA completa por ejecución, y no se reintenta solo.
- Solo las herramientas que declaran ítems seleccionables producen pasos «elegir» (conservador).
- Solo la persona crea o edita rutinas con IA.
- Sin precio del modelo, no corre.

## Pruebas (sin modelo, sin red)
- Clasificación: argumento en la instrucción → fijo; argumento que es un ítem de una salida previa → elegir; sin origen → libre.
- Una herramienta sin ítems declarados nunca genera «elegir».
- «Elegir» rechaza una respuesta que no es una opción; reintenta una vez; cae a la IA completa.
- Una herramienta en rojo, normal o ámbar sin confirmar nunca corre; no existe el estado «preguntar».
- Topes: por ejecución y diario; sin precio no corre; el gasto se anota como «Rutina IA».
- Receta vieja: se marca, se avisa y no se reintenta sola.
- Las sugerencias por palabras clave no incluyen herramientas privadas ni de escritura.
