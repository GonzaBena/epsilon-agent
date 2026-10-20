# Plan: forma del mockup (paneles, menú, contraer)

Sigue a `docs/plan-web.md` y `docs/plan-config.md`. Objetivo: acercar la web al
mockup (header, menú lateral, paneles) sin dejar nada que no funcione.

## Decisiones

- **Solo lo que funciona.** Fuera: Memoria, Docs, Sesión, Resúmenes y el avatar.
  Dentro, con función real: Chat, Notas (`/ver`), Archivos (`/archivos`),
  Facturas y tickets (`/archivos tickets`) y, abajo de todo, Configuración. Canales y Uso van
  en la barra de arriba (ver «Barra de arriba, Canales y Uso» al final).
- **Sin buscador por ahora.** El «Buscar» del mockup apunta a conversaciones,
  que todavía no existen; la búsqueda de notas ya está en `/buscar`.
- **Paneles que se contraen.** Izquierdo: columna de íconos (64px) con el «+»
  de Nuevo chat arriba, el menú, y el botón de abrir abajo. Derecho (paso 2):
  columna mínima con su botón de abrir. El estado se recuerda en el navegador.
  En ventanas angostas (<1000px) el izquierdo queda siempre como columna de íconos.
- **Accesibilidad:** los íconos conservan su nombre (la etiqueta sigue en la
  página, fuera de la vista); los botones de abrir/cerrar llevan `aria-expanded`.
- **Nuevo chat** vacía la conversación de este navegador (como el viejo «Limpiar
  chat»); más adelante crearía una guardada en el servidor.
- **Menú escrito en cada HTML** (sin plantillas); un test vigila que sea igual.
- **Comandos por enlace** (`/?cmd=...`): solo se ejecutan solos los de una lista
  de lecturas (`links` en `tools.py`, vía `/api/tools`); nunca los que escriben o llaman
  al modelo.
- El preview de CSV sigue como texto; la tabla del mockup necesita filas
  estructuradas desde el núcleo (cambio aparte).

## Pasos (cada uno con commit y revisión en el navegador)

1. Header y panel izquierdo con su versión contraída. **Hecho.**
2. Panel derecho (Herramientas, Canales) con su versión contraída, píldoras de
   atajos sobre el cuadro de texto y la nota «puede equivocarse». **Hecho.**
   Las herramientas y píldoras escriben en el cuadro (reemplazan lo que hubiera)
   y no envían; «Imagen a CSV» abre el selector y deja un ejemplo seleccionado.
3. Pulido: color de las burbujas, botón de enviar cuadrado con ícono, estado vacío.
   **Hecho.** Tu burbuja usa `#1e2318` (radio 14px, sin borde); el botón de enviar es
   un cuadrado lima de 44px con flecha (`.send-btn`; `.send` de texto sigue para
   «Guardar cambios»); el estado vacío es un saludo de Epsilon con su marca. El
   saludo NO lleva la clase `msg`: «Nuevo chat» borra todos los `.msg`.

## Animación de los paneles

Al contraer, nada puede cambiar de lugar de golpe: se vio que los íconos saltaban
al centro del panel (todavía ancho) y recién después el panel se achicaba. Regla
actual: el relleno y la posición de los íconos son iguales abierto y contraído; el
panel solo se angosta y recorta, y las etiquetas y bloques se desvanecen. Se midió
la posición de cada pieza en el instante del clic: 0 saltos en las 4 direcciones
(antes saltaban 7 elementos al contraer la izquierda y 4 la derecha).

## Pendiente del mockup

La tabla del preview de CSV (hoy es texto con ` | `) y la etiqueta de herramienta
sobre cada respuesta necesitan que el núcleo devuelva datos estructurados.

## Archivo adjunto

Al elegir una imagen o PDF se muestra una miniatura cuadrada de 80px, alineada a la
izquierda sobre el cuadro de texto, con el nombre debajo (cortado con «…», y entero
en el tooltip) y un botón × arriba a la derecha. La × aparece al pasar el mouse, con
el foco del teclado, y siempre en pantallas táctiles (`hover: none`). Las imágenes
se muestran desde una dirección local del navegador (`blob:`, sin subirlas a ningún
lado; la CSP lo permite solo para `img-src`) y se liberan al quitar el archivo. Los
PDF no tienen miniatura: llevan un ícono.

## Barra de arriba, Canales y Uso. **Hecho.**

Rediseño a partir del canvas «Epsilon - Chat web» (páginas Chat y Uso de IA).

- **Barra de arriba** (en las cuatro páginas, repetida a mano; un test vigila que coincidan):
  Chat · Canales · Uso, con la actual marcada (`aria-current`), y a la derecha el modelo (que
  también lleva a Configuración). Uso salió del menú de la izquierda; **Configuración sigue ahí,
  abajo de todo** (un engranaje arriba, como en el diseño, no se encontraba). Se dejaron afuera
  Memoria y Docs del diseño: todavía no existen.
- **Interruptores** (`button role="switch"`, pista lima, perilla; sin animación si el sistema pide
  menos movimiento) para todo ajuste de sí/no: en `/config` van dentro del formulario (se guardan
  con «Guardar»); en Canales actúan al instante.
- **`/canales`**: una tarjeta por canal con estado en palabras (el punto de color solo acompaña) y, si
  se puede, el interruptor. Telegram se apaga y prende de verdad (ver `main.stop_channel` y
  `telegram.stop()`); la web dice «Siempre activa». Se refresca cada 4 s. Voz **no** es un canal: queda
  en `/config` con sus dos interruptores.
- **`/uso`**: tarjetas (Hoy, Esta semana desde el lunes, Este mes, Total), la tabla de las
  peticiones de hoy (Hora, Acción, Canal, Modelo, tokens y precio) con filtro por modelo, y el
  conmutador USD/ARS, que solo aparece si cargaste `USD_ARS` (a mano; no se consulta ninguna
  cotización). El registro `usage.jsonl` ahora anota hora, acción y canal (variables de contexto, así
  dos pedidos a la vez no se mezclan); lo anterior se sigue leyendo y muestra «—». Un modelo sin
  precio cargado cuenta sus tokens pero no inventa un costo.
- Verificado en el navegador con datos de prueba: apagar y prender Telegram, el filtro por modelo,
  el cambio a pesos y la tabla sin partirse en líneas.

## Marca: logo y favicon. **Hecho.**

- Las imágenes del README y del repositorio viven en `docs/img/` (`icon-192.png` es el logo del
  README; `og-social-preview.png`, 1280×640, es para subir a GitHub en Settings → Social preview).
- Las de la web viven en `channels/web_static/img/` y salen como `/static/img/...`:
  `favicon.ico` + `favicon-32.png` + `apple-touch-icon.png` en el `<head>` de cada página, y
  `/favicon.ico` en la raíz (los navegadores lo piden solos). El logo del header es
  `apple-touch-icon.png` (un cuadrado entero: el redondeo lo pone el CSS, clase `.brand-logo`).
- `bot-avatar-512.png` es la foto del bot: se sube a mano en @BotFather (`/setuserpic`). `tray-24.png`
  y `tray-48.png` quedan para un ícono de bandeja si algún día hay una app de escritorio; hoy no se usan.
- Un test comprueba que todo lo que las páginas y el README enlazan existe.

## Sin verificar

- El comportamiento en ventana angosta (no se pudo achicar la ventana del navegador).
- El desvanecimiento de la × por foco de teclado: una vez leí opacidad 0 y la
  siguiente 1; lo atribuyo a que la pestaña automatizada no dibuja fotogramas.
- La suavidad de la animación al contraer: en la pestaña automatizada los
  fotogramas llegan a saltos. Sin transición, las 4 combinaciones de paneles
  llegan al ancho correcto (verificado).
