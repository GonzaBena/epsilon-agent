# Herramienta «navegador»: que la IA use tu Chromium como si fueras vos

Fecha: 2026-10-22. Estado: borrador para aprobar.

## Para qué

Hoy Epsilon habla y guarda cosas, pero no hace cosas en la compu. Se quiere que un mensaje de texto o un audio,
desde cualquier canal (web o Telegram) y en cualquier forma de hablar con la IA, haga que Epsilon actúe en la
compu donde corre. El primer caso es «poné mi supermix»: abrir YouTube Music en el Chromium de todos los días,
con la sesión de Google ya iniciada, encontrar «Mi Supermix» (que no siempre está en el mismo lugar) y darle play.

Esto es el primer paso hacia «que la IA pueda hacer cosas»: la base tiene que servir para más sitios, no solo
para música. Gasta crédito de IA a propósito; tiene que verse que está trabajando (ver «Aviso de gasto»).

## Lo que ya se probó (2026-10-22, no es suposición)

- Chromium 154 con `--remote-debugging-port=9222` en `~/.config/chromium-flags.conf` abre el puerto en
  `127.0.0.1:9222` **sobre el perfil por defecto** (el de todos los días). Hay que cerrar Chromium del todo
  (`pkill chromium`) y reabrirlo: si queda un proceso vivo, ignora la opción.
- `GET /json/list` devuelve las pestañas; `PUT /json/new?<url>` abre una; el WebSocket de cada pestaña acepta
  `Runtime.evaluate`.
- Con ese perfil, `music.youtube.com` carga con la sesión iniciada y la portada muestra «Supermix de ejercicio».
- La persona aceptó dejar el puerto abierto siempre («es personal»): cualquier programa local con su usuario
  puede controlar su navegador. Se documenta, no se mitiga más.

## Metas

- «poné mi supermix» funciona desde la web y Telegram, por texto y por audio, y suena en la compu de Epsilon.
- La IA ve y maneja pestañas de Chromium: listar, abrir una dirección, leer, hacer clic, escribir.
- Pasa por los mismos permisos y la misma lista de herramientas que el resto (`/permisos`, `ai_group`).
- Nada que **borre o envíe** corre sin preguntar.
- Si Chromium está cerrado, Epsilon lo abre.

## No-metas

- Controlar otros programas que no sean Chromium (ni capturas de pantalla, ni clics de mouse del sistema).
- Un shell libre o ejecutar comandos que escriba el modelo.
- Otros navegadores.
- Ser una herramienta de plugin o MCP: es una herramienta propia de `tools.py`.

## Anti-metas (sería un fracaso aunque «ande»)

- Que una página web logre dar órdenes a la IA: todo lo que se lee de una página es **dato**, rotulado como tal
  y redactado, igual que las notas y los PDF.
- Que mande un mensaje, publique, compre o borre sin que la persona lo apruebe.
- Que mate o reinicie el Chromium de la persona.
- Que gaste de más: la lectura de una página devuelve un resumen acotado, no el HTML.

## Cómo funciona

Todo esto es decisión de diseño de quien lo escribe, salvo lo marcado como «de la persona».

**Código.** Un módulo nuevo `src/epsilon/utils/navegador.py` (carpeta de utilidades locales: sin modelo, un solo
punto de red, bloqueado en los tests, como `dolar.py`). Habla con `127.0.0.1:9222`: HTTP para listar/abrir/cerrar
pestañas y WebSocket (dependencia nueva `websockets`, solo Python, sin node) para dar órdenes a una pestaña.

**Herramienta para la IA.** Grupo nuevo `navegador` en `AI_GROUPS` con acciones:

| acción | qué hace | permiso por defecto |
|---|---|---|
| `pestañas` | lista las pestañas visibles (id, título, sitio); ver guardrails 3 y 4 | permitir |
| `leer` | de una pestaña, devuelve el texto principal acotado y una lista numerada de lo que se puede tocar (`3: botón «Reproducir»`) | permitir |
| `abrir` | abre una dirección en una pestaña nueva y devuelve su id | permitir en sitios de confianza, preguntar en los demás |
| `clic` | toca el elemento número N de la última lectura de esa pestaña | ídem |
| `escribir` | escribe texto en el elemento N | ídem |

Los permisos siguen siendo por herramienta y se ven en `/permisos`. La regla de sitios es una capa de código
extra en `core._run_calls`: aunque una acción esté en «permitir», fuera de los sitios de confianza pregunta. Si
la persona la pone en «bloquear», no se ofrece al modelo, como siempre. Sitios de confianza: ajuste nuevo
`BROWSER_TRUSTED_SITES` en el `.env`, por defecto `music.youtube.com,youtube.com`.

**«Nada que borre o envíe sin preguntar» (de la persona).** Dentro de un sitio de confianza, un clic o un texto
puede igual enviar o borrar (p. ej. un comentario de YouTube). Por eso `clic`/`escribir` sobre un elemento cuyo
rótulo parezca de envío o borrado (enviar, publicar, comentar, eliminar, borrar, comprar, suscribirse…) pregunta
siempre, en cualquier sitio. Es una heurística sobre el rótulo; la pregunta de permiso muestra el rótulo.

**Lectura (cuidar los tokens).** `leer` no manda el HTML: arma, con una función de JavaScript fija que corre en
la pestaña, el título, un texto principal recortado (tope en caracteres) y como mucho unos 40 elementos
interactivos visibles, numerados. Para encontrar «Mi Supermix» en una portada que cambia, la IA lee, elige el
número y hace clic; si no aparece, puede hacer scroll al leer de nuevo o buscar en YouTube Music.

**Clic.** Se hace con eventos de entrada del protocolo (`Input.dispatchMouseEvent` en el centro del elemento),
no con `element.click()`, porque la reproducción necesita un gesto real del usuario o Chromium la bloquea.

**Si Chromium está cerrado.** Epsilon lo abre (`chromium`, que ya lee `chromium-flags.conf` y por lo tanto el
puerto) y espera hasta unos segundos a que el puerto responda. El proceso se lanza **fuera del cgroup del
servicio** (p. ej. `systemd-run --user --collect chromium`): si no, `systemctl --user restart epsilon` mataría
el navegador de la persona. Argumentos fijos, sin shell. Si Chromium está abierto **sin** puerto (se abrió
antes de agregar la línea), no lo toca: contesta claro cómo reiniciarlo.

**Aviso de gasto (de la persona).** Los eventos de progreso que ya existen («Usando navegador: leer…») avisan en
la web y en Telegram que la IA está trabajando. El gasto se anota en `/uso` con la tarea de siempre del chat/IA.

**Errores.** Puerto caído, pestaña que ya no existe, elemento que cambió: mensaje en español, sin gastar otra
llamada al modelo.

**Voz.** Sin cambios: el audio ya llega transcripto al núcleo.

**Tests.** `navegador.py` tiene un único punto de red, bloqueado en `conftest.py`; los tests usan un falso.
Se prueban: permisos por sitio, la regla de rótulos de envío/borrado, el recorte de la lectura, el rótulo de
dato/redactado, el registro en `tools.py` y el menú de la IA, y que `/permisos` lista las acciones nuevas.

## Guardrails de seguridad

Todos se aplican **en código** (`navegador.py` y `core._run_calls`), nunca dependen de lo que diga el modelo ni
de un prompt. Si algo no se puede determinar (sitio, rótulo, tipo de campo), **falla cerrado**: pregunta o
rechaza. Son decisión de diseño de quien lo escribe; la persona pidió considerarlos y falta que los apruebe.

1. **Solo http/https.** `abrir` rechaza `file:`, `chrome:`, `chrome-extension:`, `javascript:`, `data:`, `view-source:`
   y cualquier otro esquema.
2. **Nada local ni de la red de la casa.** `abrir` rechaza `localhost`, `127.0.0.0/8`, `::1`, rangos privados
   y `.local`: protege la propia web de Epsilon (`:8765`), el puerto de depuración (`:9222`) y el router. La
   dirección se resuelve y se revisa **antes** de abrir y de nuevo con la dirección final tras redirecciones.
3. **Sitios bloqueados de forma dura** (`BROWSER_BLOCKED_SITES`, por defecto: `accounts.google.com`,
   `myaccount.google.com`, `mail.google.com`, gestores de contraseñas y bancos que la persona agregue). Ni se
   listan, ni se leen, ni se tocan, aunque la acción esté en «permitir». Se ve por el sitio **real de la
   pestaña** (lo lee el código del navegador), no por lo que diga el modelo.
4. **Mínima exposición de pestañas.** `pestañas` y `leer` solo muestran título y dirección de (a) las pestañas
   que Epsilon abrió y (b) las de sitios de confianza. El resto aparece como «otra pestaña» sin datos: tus
   pestañas personales no viajan a un modelo en la nube.
5. **Nada de JavaScript del modelo.** El único código que corre dentro de una pestaña son funciones fijas del
   módulo (leer, ubicar un elemento). El modelo solo pasa números y texto, nunca código.
6. **Sin secretos.** `leer` omite los campos de contraseña y no devuelve cookies, `localStorage` ni
   valores de formularios sensibles; `escribir` rechaza campos de contraseña, tarjeta o código de seguridad.
   Todo lo leído pasa por `config.redact`.
7. **Contaminación por lectura.** Lo leído de un sitio que **no** es de confianza marca el turno como
   «contaminado»: desde ahí, cualquier acción que escriba o actúe (navegador, notas, memoria, enviar) pregunta,
   aunque esté en «permitir». Así una página con instrucciones escondidas no puede encadenar acciones solas.
8. **La pregunta de permiso dice la verdad.** Muestra el sitio real, el rótulo del elemento y el texto a escribir,
   armados por el código desde la pestaña, no copiados de lo que dijo el modelo.
9. **Topes por turno.** Como mucho unas pocas pestañas nuevas y unas decenas de acciones por mensaje (dentro de
   `MAX_TOOL_STEPS`), y cancelar (`core.cancel`) corta antes de la próxima acción.
10. **Se pueden apagar.** `BROWSER_ENABLED=false` quita el grupo entero de lo que ve la IA; y `/permisos` permite
    bloquear acción por acción.
11. **Registro.** Cada acción (hora, acción, sitio, resultado, si se pidió permiso) se agrega, redactada, a un
    archivo en `DATA_DIR`. Solo se agrega, como las conversaciones.
12. **El puerto.** El código solo habla con `127.0.0.1:9222`, comprueba que responde Chromium antes de confiar
    y no acepta una dirección de WebSocket que apunte a otro lado. Que el puerto quede siempre abierto es una
    decisión de la persona y queda dicho en la documentación.

Tests: cada guardrail tiene el suyo (esquemas, rangos de red, sitios bloqueados, pestañas ocultas, campos de
contraseña, contaminación entre herramientas, texto de la pregunta, topes).

## Se lo deja a quien lo construya

- Nombres internos exactos, formato del texto de `leer`, topes concretos (caracteres, cantidad de elementos).
- Cómo esperar a que una página termine de cargar.
- La lista exacta de palabras de envío/borrado (la de arriba es el punto de partida).
- Cómo documentar la línea `--remote-debugging-port=9222` para quien instale Epsilon (README / docs).

## Riesgos conocidos

- YouTube Music cambia su página: el clic por número leído en el momento lo tolera mejor que un selector fijo,
  pero un modelo chico puede equivocarse de elemento. Es la razón de la prueba real antes de dar esto por hecho.
- El nombre «Mi Supermix» puede estar en la portada, en la biblioteca o solo salir buscándolo.
- Autoplay: si Chromium bloquea el sonido, el clic real debería alcanzar; se verifica en la primera prueba.
