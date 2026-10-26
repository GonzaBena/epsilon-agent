# Navegador: que la IA use tu Chromium

Diseño completo: `docs/superpowers/specs/2026-10-22-navegador-design.md`. Plan de implementación:
`docs/superpowers/plans/2026-10-22-navegador.md`.

## Qué es

Un grupo de herramientas de IA, `navegador` (`pestanas`, `leer`, `abrir`, `clic`, `escribir`), que maneja el
Chromium de todos los días por su puerto de depuración (`127.0.0.1:9222`). Sirve para pedidos como «poné mi
supermix»: la IA abre YouTube Music con tu sesión de Google, lee la página (texto y elementos numerados), toca el
elemento que corresponde y la música suena en la compu donde corre Epsilon. Funciona igual desde la web y
Telegram, por texto o por audio, porque vive en el núcleo como cualquier herramienta (mismos permisos, mismos
eventos de progreso «Usando navegador: …», mismo gasto en `/uso`).

## Preparar Chromium (una vez)

1. Agregar la línea `--remote-debugging-port=9222` a `~/.config/chromium-flags.conf`.
2. Cerrar Chromium del todo (`pkill chromium`: si queda un proceso vivo, ignora la opción) y abrirlo de nuevo.

Si Chromium está cerrado, Epsilon lo abre solo (en su propia unidad de systemd, así reiniciar el servicio no lo
cierra). Si está abierto sin el puerto, no lo toca y explica cómo reiniciarlo.

**Riesgo aceptado:** con el puerto abierto, cualquier programa de tu compu con tu usuario puede controlar tu
navegador y tu sesión de Google. Escucha solo en `127.0.0.1`. `BROWSER_ENABLED=no` apaga el grupo entero.

## Ajustes (`/config`, sección Navegador)

- `BROWSER_ENABLED` (por defecto `si`).
- `BROWSER_TRUSTED_SITES` (por defecto `music.youtube.com,youtube.com`): ahí la IA abre, toca y escribe sin preguntar;
  en cualquier otro sitio pregunta siempre.
- `BROWSER_BLOCKED_SITES` (por defecto `accounts.google.com,myaccount.google.com,mail.google.com`): nunca se listan,
  leen ni tocan, aunque el permiso diga «permitir».

## Guardrails (todos en código, no en el prompt)

`utils/navegador_reglas.py` (puras) y `utils/navegador.py`: solo http/https; nada de `localhost`, red local ni
direcciones con usuario (se revisa también el destino final tras redirecciones); sitios bloqueados; la IA solo ve
las pestañas que abrió Epsilon o de sitios de confianza; el único JavaScript que corre en una pestaña es fijo
(`READ_JS`); `leer` omite contraseñas y `escribir` rechaza campos de contraseña y tarjeta.
`ai/browser_guard.py`, enganchado en `core._run_calls` después de los permisos (solo puede endurecerlos): fuera de
los sitios de confianza pregunta; un botón con rótulo de enviar, publicar, borrar o comprar pregunta siempre; si la IA
leyó un sitio de afuera el mensaje queda «contaminado» y toda acción que no sea una lectura pregunta; topes de 20 acciones
y 3 pestañas nuevas por mensaje; la pregunta muestra el sitio y el rótulo reales, armados desde la pestaña. Cada acción
se agrega, redactada, a `DATA_DIR/browser.log`.

## Límites conocidos

- **DNS rebinding:** Epsilon resuelve el sitio para revisarlo y Chromium lo resuelve por su cuenta; un sitio malicioso con TTL 0
  podría pasar la revisión y conectar a la red local. Hace falta antes que la persona apruebe abrir un sitio de afuera. Cerrarlo del todo
  pide el dominio `Network` de CDP (`remoteIPAddress`); por ahora queda como límite.
- Una pestaña que ya está abierta y se va a `localhost`, la red local o un sitio bloqueado deja de verse y de poder usarse (se revisa en cada uso).

- Todo se hace con clics reales sobre lo que `leer` numeró; si la página cambia entre `leer` y `clic`, se pide leer de nuevo.
- `pestanas` muestra títulos de pestañas que abrió Epsilon en sitios de afuera y no contamina el mensaje.
- Solo Chromium, solo esta compu; no hay capturas de pantalla ni control de otros programas.

## Puesta en marcha (2026-10-22)

Primero que ande, después la seguridad: un mensaje con navegador tiene 16 vueltas del modelo; `click` espera la carga y adopta pestañas nuevas; `leer id abajo|arriba`; `BROWSER_GUARD=no` apaga las preguntas de la guardia (no los bloqueos ni los topes).

## Entender un nombre suelto (2026-10-26)

«Abrí el aula virtual de la facu» no pasa por Google: `navegador.resolve_target` busca, en orden, en

1. **los atajos de la persona** (`/sitio facu = aulasvirtuales.frba.utn.edu.ar`, `data/sites.py`, `DATA_DIR/sites.json`):
   el nombre del atajo tiene que aparecer entero en lo pedido («aula virtual de la facu» usa «facu»); gana el más largo.
   Es lo que solo sabe la persona («inglés» → el campus de idiomas). Solo los toca la persona: la IA no tiene herramienta.
2. **los marcadores de Chromium** (`_bookmarks`, solo lectura): la dirección COMPLETA del marcador (una carpeta de Drive
   abre esa carpeta), comparando con el nombre que le puso la persona; sin la regla de «letras en orden».
3. **el historial** (como antes): solo el sitio.

En las tres, sin acentos (`reglas.fold`), y una palabra de 5 letras o más vale también dentro del nombre del sitio
(«virtual» → aulasvirtuales). Si nada coincide, el resultado le dice al modelo que no busque en Google ni invente la
dirección y que le pregunte a la persona (que puede guardarla con `/sitio`). Ni el historial ni los marcadores llegan al
modelo. Pendiente: una búsqueda web de verdad (ver la charla del 2026-10-26: SearXNG local o la API de Brave).
