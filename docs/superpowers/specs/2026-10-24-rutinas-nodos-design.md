# Rutinas con activador y pasos (diseño)

Estado: borrador para aprobar. Lo que dijo la persona va sin marca; lo que decidí yo va marcado **(mi decisión)**.

## Por qué

Hoy una rutina es una sola frase «cuándo | qué» con tres acciones fijas (`avisar`, `volumen`, `musica`). La persona quiere cosas como:

- «A las 8:00 la compu abre una canción o mi radio en YouTube Music, a cierto volumen, para despertarme.»
- «A las 15:00 mandame una notificación que diga "algo".»
- Más adelante, un reporte matutino de mails (como Claude Cowork o Gemini Spark).

Y quiere armarlas como bloques conectados (estilo n8n o Atajos de Apple), no escribiendo una frase.

## Qué se quiere

1. Una rutina = **un activador** + **una cadena de pasos**.
2. Cada paso es **cualquier herramienta que Epsilon ya tiene** (volumen, música, notas, navegador, y las de los conectores MCP), con sus argumentos puestos de antemano. Las herramientas nuevas que se agreguen aparecen solas como bloques.
3. La salida de un paso puede usarse en los pasos siguientes (por ejemplo, el título de la canción que abrió el paso 2 va dentro del aviso del paso 3).
4. Se crea con el botón «Nueva rutina», que abre un modal con nombre y editor.
5. El editor tiene **dos vistas de la misma rutina**: una pizarra de nodos conectados para pantallas grandes, y una lista vertical de bloques para pantallas chicas. Editás en cualquiera y se ve igual en la otra.
6. Si la compu no está disponible a la hora, la rutina se omite (ya funciona así: se avisa que se perdió, no se ejecuta tarde).

## Lo que NO es (por ahora)

- **Sin ramas ni condiciones** («solo si…», «si no…»). Quedan para cuando la base esté sólida, y tienen que poder verse también en la lista vertical.
- Sin bucles, sin pasos en paralelo, sin shell, sin JavaScript, sin texto libre ejecutado.
- Sin editor en Telegram. Telegram sigue listando rutinas y creando las simples con `/rutina cuándo | qué`.
- El reporte de mails con IA **no entra en la primera entrega**, pero el diseño no debe impedirlo (ver «Pasos con IA»).

## Lo que sería un fracaso aunque «funcione»

- Una rutina que hace algo que la persona no vio con claridad al crearla.
- Que gaste dinero en IA sin que se note.
- Que la vista de lista y la de pizarra se desincronicen o muestren cosas distintas.
- Que un paso salte un permiso que la persona había bloqueado.

## Decisiones tomadas

### Modelo de datos
Una rutina guardada en `routines.json` (por `safe_path`, reescritura por archivo temporal, como hoy):

- `id`, `name`, `enabled`
- `trigger`: los tipos de hoy (`daily` con días y hora, `once`, `cond` de disco/RAM, con su `tripped` y reposo de 1 h) **(mi decisión: no se agregan activadores nuevos en esta entrega)**
- `steps`: lista ordenada; cada paso tiene `id`, `tool` (id de herramienta del registro, p. ej. `volumen` o `mcp_<conector>__<acción>`) y `args` (valores o plantillas)
- `layout`: posición (x, y) de cada nodo, solo para la pizarra; la lista la ignora

Las rutinas viejas (`when`/`do`) se migran solas a este formato al leerlas. `/rutina cuándo | qué` sigue funcionando y crea una rutina de un paso.

### Salida de un paso
**(mi decisión)** La salida de un paso es el **texto** que devolvió la herramienta. Un paso posterior la usa con una plantilla (`{{paso 2}}`); en la interfaz se ve como una etiqueta que se arrastra o se elige, no se escribe a mano. El texto sustituido es dato: se redacta (`config.redact`), se recorta, y el argumento final se valida contra el esquema de la herramienta **después** de sustituir. No hay campos estructurados en esta entrega.

### Qué herramientas puede usar un paso
**(mi decisión)** Las que hoy tiene la IA (`ai` en `tools.py`) más las de los conectores habilitados, más dos bloques nuevos pensados para esto: `abrir` (una dirección o canción) y `esperar` (N segundos, con tope), más `avisar` con texto propio. Se excluyen las que solo tienen sentido dentro de una conversación (`/chat`, `/nuevo`, confirmaciones) y las que dependen de un canal (`enviar` a Telegram).

Una herramienta que la persona tiene **bloqueada** en `/permisos` no puede ser un paso: se rechaza al guardar y vuelve a rechazarse al ejecutar. Los sitios de `BROWSER_BLOCKED_SITES` y los bloqueos duros del navegador siguen valiendo.

### Permisos
**(mi decisión)** Como la rutina corre sin nadie mirando, la confirmación **al crearla** vale como permiso: se muestra la lista completa de pasos con sus argumentos exactos, y al ejecutarse no se pregunta de nuevo. Los pasos que borran o reescriben datos (por ejemplo `olvidar`, `editar` una nota) se marcan en esa confirmación como destructivos. Si una rutina cambia (se agrega o cambia un paso), vuelve a pedir confirmación.

### Cuando un paso falla
**(mi decisión)** La cadena se corta en ese paso y se avisa cuál falló y por qué (por la notificación y por Telegram, como hoy). No se sigue a ciegas. Todo queda en `routines.log`, una línea por paso.

### Despertador: abrir música
**(mi decisión)** `abrir` usa el Chromium con puerto de depuración que ya maneja el navegador de la IA; si no está abierto, hace un respaldo con `xdg-open` solo para direcciones http(s). Los navegadores suelen bloquear que el audio arranque solo, así que el despertador típico es: volumen → abrir → esperar unos segundos → `musica play`. **Esto hay que comprobarlo con una prueba rápida antes de dar el diseño por bueno.**

### Interfaz
- Botón «Nueva rutina» abre un modal con nombre, editor, «Probar ahora» y «Guardar».
- Ancho grande: pizarra de nodos (activador a la izquierda, pasos conectados, se arrastran, las líneas muestran de dónde sale cada dato). Ancho chico: lista vertical de bloques en fila, con «＋ agregar paso». **(mi decisión)** El ancho mediano usa la lista.
- Elegir un paso muestra un formulario con un campo por parámetro de la herramienta, a partir de su esquema (ya existe algo así en los botones de conectores).
- «Probar ahora» **(mi decisión)** ejecuta la rutina de verdad, una vez, y muestra el resultado de cada paso. Antes pide confirmar.
- Reglas de la web que siguen: todo lo que viene del servidor va con `textContent`; sin HTML en línea ni librerías desde CDN (si hace falta una, va vendorizada en `js/vendor/`); la CSP no permite estilos escritos en el HTML.

### La IA y las rutinas
**(mi decisión)** El grupo `rutinas` de la IA conserva `rutina_crear` (con permiso `ask`), pero acepta la forma con pasos. La pregunta de permiso muestra los pasos tal como los entendió el código, no lo que dijo el modelo. La IA sigue sin poder agregar conectores ni cambiar permisos.

### Pasos con IA (después)
Un bloque «preguntarle a la IA» (por ejemplo, para el reporte de mails con un conector de Gmail) no se construye ahora, pero la estructura de pasos con salida de texto ya lo permite. Cuando se haga: se marca como que gasta, lleva un tope de dólares por ejecución, y las herramientas que pida la IA en ese paso solo corren si su permiso es `allow` (un `ask` se niega, porque nadie puede responder).

## Qué se deja al que construye

- La separación de capas: `routines/rules.py` sigue puro (sin hilos ni archivos). Cómo se ejecuta un paso (probablemente un ejecutor que `core` registra al arrancar, para que `routines/` no importe el núcleo) lo decide quien construye.
- Cómo se dibuja la pizarra y qué librería (o ninguna) se usa.
- La sintaxis exacta de las plantillas y los topes numéricos (pasos por rutina, largo de la salida, espera máxima).
- El umbral de ancho entre pizarra y lista.
- Las pruebas: los tests no ejecutan herramientas reales; el ejecutor de pasos se inyecta.

## Decisiones menores (mi decisión, a revisar al leer)

- **Qué cuenta como cambio que pide confirmar de nuevo:** cualquier cambio en pasos (agregar, quitar, reordenar, cambiar herramienta o argumentos) y en el activador. Renombrar y mover nodos no. Se confirma en cada puerta de entrada: el modal web, `/rutina` en Telegram (pide «sí» con la frase entendida) y `rutina_crear` de la IA (pregunta de permiso). Las rutinas viejas migradas ya estaban confirmadas y no se vuelven a preguntar.
- **«Probar ahora»** ejecuta lo que está en pantalla, aunque no esté guardado, y antes muestra la misma lista exacta de pasos para confirmar. Eso cuenta como confirmación solo para esa prueba.
- **Permisos cambiados después de crear:** se revisan al ejecutar. Si una herramienta pasó a bloqueada, o es de un conector cuyo hash cambió, la cadena se corta antes de ese paso y se avisa (los pasos anteriores ya corrieron).
- **`abrir`, `esperar` y `avisar` con texto** son herramientas normales del registro (`tools.py`). Para la IA, `abrir` pasa por `browser_guard` y por todas las reglas del navegador (sitios bloqueados, `file:`, red local); `esperar` no la ve. En una rutina, `abrir` respeta los bloqueos duros pero no las preguntas, porque la confirmación al crear ya las cubre.
- **Despertador:** se hace primero una prueba rápida de si el audio arranca solo. Si no arranca, igual se entrega el motor de pasos y el editor, y el despertador queda con el paso extra `musica play`.
- **Ejecución:** cada rutina corre en su propio hilo, nunca en el hilo del reloj de 30 s; la próxima vez se adelanta antes de ejecutar como hoy; dos ejecuciones de la misma rutina no se superponen (la segunda se omite y se avisa). Tope de duración por ejecución: 5 minutos.
- **Condiciones de disco/RAM:** el motor expone al primer paso la variable `{{disparador}}` («el disco al 93 %»), y la migración la agrega al aviso viejo para que el mensaje siga igual.
- **Plantillas:** solo se puede referenciar un paso anterior. Si ese paso falló, la cadena ya se cortó; si no devolvió texto, se sustituye por vacío.

## Respuesta de la persona sobre permisos

Elegida la opción A: la confirmación al crear la rutina cubre todas las herramientas que hoy preguntan (navegador, `ventanas`, conectores MCP), salvo las bloqueadas en `/permisos` y los bloqueos duros del navegador. Los pasos de navegador corren directo, sin modelo: por ejemplo, activador «todos los días 8:00» y paso `abrir` con el link de la playlist de YouTube Music (el paso acepta también un nombre de app o sitio). Solo un paso que use IA se marca como que gasta y se confirma al crear.
