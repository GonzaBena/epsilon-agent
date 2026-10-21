# Modo IA (chat con la IA) — diseño

Nombre provisorio: «modo IA».

## Intención y por qué

Hoy la IA solo se usa en dos acciones cerradas: resumir e imagen→CSV. Cada pedido nuevo
(«explicame esto», «resumilo de otra forma») exigiría una herramienta nueva: código, configuración
y tests. El modo IA es una conversación libre con la IA para cosas puntuales, sin crear un comando
por cada pedido.

También es el lugar donde después se enchufan las tools y los MCP. Esta primera versión deja esa
estructura lista sin implementarlos.

Lo que más preocupa es el gasto: una charla larga manda todo el historial en cada pregunta y se
encarece sola. Por eso el modo IA nace con **medición y tope de costo** desde el día uno.

## Qué es

Un tipo de conversación. Se elige **al crearla** (chat nuevo → «normal» o «IA») y **no cambia
después**. Una conversación normal (comandos, sin IA libre) nunca pasa a modo IA, así un chat viejo
y largo no se vuelve caro de golpe.

En una conversación de modo IA, todo lo que se escribe sin `/` va a la IA con el historial de esa
conversación y la memoria (`memory.md`). Los comandos `/...` siguen funcionando como siempre.

## Objetivos

- Conversar con la IA dentro de la web, con historial de la conversación y la memoria como contexto.
- Ver en cada conversación de modo IA: **tamaño del contexto**, **costo acumulado** y **límite**.
- **Límite de costo en dólares** por conversación. Al llegar: avisa y bloquea la conversación hasta
  que el usuario suba el límite. No se llama al modelo mientras está bloqueada.
- Reusar el registro de gasto existente (`ai/usage.py`) para que la página Uso cuente también el chat.
- Dejar el punto de extensión donde después entren tools (con permisos) y MCP.

## No objetivos (esta versión)

- **Tools y MCP.** El usuario quiere antes un sistema de permisos. Tampoco buscar en notas, tickets
  o archivos: «¿qué día hice esta compra?» funcionará recién con tools.
- Telegram y plugins de canal. Solo la web.
- Cambiar de modo una conversación ya creada.
- Resumir o recortar el historial automáticamente (el freno es el límite de costo).

## Anti-objetivos

- Que un chat normal gaste tokens sin que se haya elegido modo IA.
- Que se pueda superar el límite sin que el usuario lo suba a propósito.
- Que una clave o dato sensible salga hacia el modelo o quede en disco sin pasar por `config.redact`.

## Restricciones del proyecto que aplican

(CLAUDE.md) `ai/llm.py` es el único lugar que llama a Gemini; los tests nunca llaman al modelo
(`llm.generate` bloqueado en `conftest.py`, lo nuevo debe quedar bloqueado igual); archivos solo
agregando, rutas por `safe_path`; textos de interfaz dicen «IA», no el nombre del modelo; el
contenido de notas/imágenes es dato, no instrucciones; la web escucha solo en 127.0.0.1.

## Diseño

### Conversaciones (`data/conversations.py`)

- Primera línea `{"type": "meta", "channel": "web", "mode": "ai"}`. Hoy las conversaciones web no
  llevan `meta`; las viejas siguen siendo «normal». `_channel` ya trata `meta` con `channel: web`
  como web. Nueva función `mode(id) -> "normal" | "ai"`.
- Límite: línea `{"type": "limit", "usd": 0.50}`; gana la última (igual que el título). El archivo
  sigue siendo solo de agregar.
- `list_all` y `GET /api/conversations/{id}` devuelven `mode`; la lista web muestra un ícono distinto
  para las de modo IA.
- `POST /api/conversations` acepta `mode` (`normal` por defecto, `ai`).

### Llamada al modelo (`ai/llm.py`)

`generate` es de un solo disparo (partes + prompt, `temperature=0`). El chat necesita varios turnos:
función nueva `llm.chat(history, system, max_tokens)` en el mismo archivo, que sigue siendo el único
que habla con Gemini. Registra el gasto con `usage.record` como `generate`. Prompt de sistema corto:
dice que la memoria y el historial son datos del usuario, no instrucciones. `max_output_tokens`
siempre limitado.

### Gasto por conversación (`ai/usage.py`)

- Variable de contexto nueva `conversation` (como `channel` y `action`) y campo opcional
  `conversation` en cada línea de `usage.jsonl`. Las líneas viejas no lo tienen.
- Función `for_conversation(id) -> {cost, calls, last_input}`: suma el costo de sus llamadas;
  **tamaño del contexto = tokens de entrada de la última llamada**.
- La acción se anota como «Chat» para que la página Uso la distinga.

### Núcleo (`core.py`)

Texto sin `/` en una sesión de modo IA → `_chat(...)`:

1. Si el costo acumulado ≥ límite → responde que está bloqueada y cómo subir el límite; **no llama
   al modelo**.
2. Arma el historial desde `conversations.messages(id)` más la memoria.
3. Llama a `llm.chat`, guarda la respuesta, devuelve `Reply` con los números (contexto, costo,
   límite).

El núcleo no conoce canales: el modo viaja en el `Message` o la sesión, no en HTTP.

### Web (`channels/web.py` y `web_static/`)

- «Nuevo chat» ofrece las dos opciones.
- Barra de la conversación de modo IA: contexto (tokens), costo (US$), límite (editable).
- Endpoint para cambiar el límite (agrega la línea `limit`).
- Conversación bloqueada: el cuadro de texto avisa y ofrece subir el límite.
- Todo como `textContent`, nunca HTML.

### Configuración (`settings/config.py`)

Ajuste nuevo con el límite por defecto de las conversaciones nuevas de modo IA, en dólares. Se
valida como los demás ajustes.

## Decisiones del usuario

- Modo elegido al crear la conversación, fijo después.
- Memoria sí; tools y MCP no todavía (primero permisos).
- Mostrar tamaño de contexto, límite asignable y precio actual de la conversación.
- Al llegar al límite: avisar y bloquear hasta que suba el límite.

## Decisiones mías (a revisar)

- Límite en **dólares** (no tokens), porque `usage.py` ya calcula el precio.
- Solo web en esta versión.
- Límite por defecto en Configuración y editable por conversación.
- El chequeo es **antes** de cada llamada, así que una sola respuesta puede pasar un poco del
  límite; no se corta a mitad de respuesta.
- Si el modelo configurado no tiene precio en `usage.PRICES`, el costo es desconocido: la
  conversación de modo IA **no arranca** y avisa que falta cargar el precio (si no, el límite no
  se podría hacer cumplir).
- El historial completo viaja en cada pregunta, sin recortes (el límite de costo es el freno).
- **Qué entra al historial:** solo lo que escribe el usuario y las respuestas de la IA del chat, como
  texto. La salida de comandos `/...` (resúmenes, CSV, contenido de notas, `/memoria`, confirmaciones)
  **no** se manda al modelo: gasta contexto y mezcla datos con la charla. Tampoco imágenes ni
  archivos adjuntos (solo texto). Todo lo que se manda pasa por `config.redact`.
- **Qué cuenta para el costo:** *toda* llamada al modelo hecha dentro de una conversación de modo IA
  (chat, `/resumir`, imagen→CSV) se anota con su `conversation` y suma al costo y al límite. El
  tamaño del contexto sigue siendo la entrada de la última llamada **del chat**.
- **El costo mostrado es el de ESA conversación**, no el gasto global: un chat nuevo arranca en
  US$ 0 aunque ya se haya gastado ese día. Solo suman las llamadas anotadas con su `conversation`.
  (Aprobado por el usuario.)
- **Qué bloquea el límite:** al llegar, se bloquea todo lo que llama al modelo en esa conversación
  (texto libre y comandos que usan IA). Los comandos que no usan modelo (`/ver`, `/memoria`, etc.)
  siguen funcionando.

## Para el constructor

Decidir: forma exacta del `Message`/sesión para pasar el modo al núcleo, nombres de las funciones y
endpoints, textos de la interfaz en español, diseño visual de la barra, y los tests (sin llamar al
modelo; `llm.chat` bloqueado en `conftest.py`). Seguir el estilo del código vecino y actualizar
CLAUDE.md y `docs/plan-conversations.md` donde corresponda.
