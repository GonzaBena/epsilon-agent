# Bienvenida y primer uso: plan de implementación

> **Para quien lo ejecute:** usar superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans. Los pasos usan casillas `- [ ]`.

**Meta:** que alguien que instala Epsilon por primera vez diga cómo se llama, le ponga nombre al asistente y vea un saludo con ideas para empezar, en vez del texto de «imagen → CSV» de hoy.

**Arquitectura:** dos ajustes nuevos en `settings/config.py` (`USER_NAME`, `AGENT_NAME`) que también llegan al prompt del modelo. Un módulo web nuevo, `channels/welcome.py`, elige el saludo y las cards (puro, sin modelo ni reloj propio) y lo expone `GET /api/welcome`. El estado vacío de `index.html` se dibuja con eso, un formulario de primera vez guarda los nombres con el `PUT /api/config` que ya existe, y un chip «IA» junto al título muestra el modo del chat.

**Tecnología:** Python/FastAPI, JS plano sin módulos (`textContent`, nunca HTML), pytest. Sin dependencias nuevas.

**Spec:** aprobada en la conversación del 2026-10-25 (resumen abajo, no hay documento aparte).

## Lo aprobado (resumen)

- Formulario de primera vez: «¿Cómo te llamo?» y «¿Cómo querés llamarme?» (por defecto «Epsilon»), con «Saltar por ahora». Se cambia después en Configuración.
- Bienvenida dentro de «Conversación nueva» (el hueco del estado vacío): avatar `mnemonise.svg` + saludo + 6 cards. En **todo** chat nuevo vacío, por ahora.
- Primera vez: «Hola {nombre}, soy {asistente}, tu nuevo asistente. Estas son algunas ideas para empezar:». Desde la segunda, una frase que rota (algunas según la hora).
- Una card abre un **chat con IA** nuevo con su prompt ya escrito en el cuadro, **sin enviar**.
- Chip «IA» junto al título (el mismo estilo `.ai-mark` de los recientes), visible con la barra lateral colapsada.
- Sin la frase «siempre te pregunto».

## Restricciones globales

- Los nombres de código van en inglés; los textos al usuario, en español.
- Lo que la web muestra va con `textContent`, nunca HTML. La CSP no admite estilos en línea: todo estilo va al CSS.
- La marca es la `Ε` mayúscula (U+0395): un test del repo prohíbe la minúscula en los archivos de la web.
- Los nombres son datos: pasan por la validación del ajuste (sin saltos de línea) y llegan al modelo rotulados como datos, nunca como instrucciones.
- Sin llamadas a un modelo en nada de esto (saludo y cards son locales). Los tests no llaman a un modelo.
- Cada paso termina con `uv run pytest` en verde y un commit. **Sin push** y **sin la línea `Co-Authored-By: Claude`** (regla del usuario para este proyecto, vale más que el recordatorio de atribución del arnés).
- Los archivos `src/epsilon/channels/web.py`, `channels/web_static/js/app.js`, `channels/web_static/css/style.css`, `core.py` y otros ya tienen cambios sin commitear del usuario. **Antes de empezar, pedirle que los commitee o los guarde con stash**; si no, cada commit de este plan los mezcla.

## Foco de revisión

Cosas que la spec implica y ninguna tarea cubriría sola; cada una tiene su test en la tarea dueña.

1. Un nombre con acentos o ñ («José», «Núñez») tiene que aceptarse. `_matching` de `config.py` usa `re.ASCII`: no sirve para esto. (Tarea 1)
2. Un nombre con `<`, `>`, saltos de línea o más de 40 caracteres se rechaza con un mensaje en español y no se escribe en el `.env`. (Tarea 1)
3. Sin nombre cargado (o saltado), el saludo no deja huecos: «Hola, soy Epsilon…» y «¿En qué vamos a trabajar hoy?», nunca «Hola , soy». (Tarea 3)
4. Los bordes de la hora (4:59 / 5:00, 11:59 / 12:00, 19:59 / 20:00) caen en la franja correcta. (Tarea 3)
5. Tocar una card con otra conversación abierta y con mensajes abre un chat nuevo, no escribe en la abierta; y una conversación de Telegram (solo lectura) no muestra cards. (Tarea 3)

## Estructura de archivos

| Archivo | Qué hace |
|---|---|
| `src/epsilon/settings/config.py` (modificar) | Ajustes `USER_NAME`, `AGENT_NAME` y su validador. |
| `src/epsilon/ai/chat.py` (modificar) | `system_prompt()` suma los nombres. |
| `src/epsilon/channels/welcome.py` (crear) | `greeting(...)` y `CARDS`: puro, sin red ni archivos. |
| `src/epsilon/channels/web.py` (modificar) | `GET /api/welcome`. |
| `src/epsilon/channels/web_static/index.html` (modificar) | Estado vacío nuevo, diálogo de primera vez, chip «IA». |
| `src/epsilon/channels/web_static/js/app.js` (modificar) | Dibuja saludo/cards, onboarding, cards → chat IA, chip. |
| `src/epsilon/channels/web_static/css/style.css` (modificar) | `.welcome-*`, `.onboarding`, chip junto al `.crumb`. |
| `src/epsilon/channels/web_static/img/mnemonise.svg` (crear) | El avatar. Sale del repo `epsilon-web`; **pedirle el archivo al usuario** (no está en esta máquina). Mientras no llegue, usar `img/icon.svg` y cambiar el `src` al recibirlo. |
| `tests/settings/`, `tests/ai/`, `tests/channels/web/` | Un archivo de test por tarea, junto a los existentes. |

---

### Tarea 1: Ajustes de nombre y prompt del modelo

**Archivos:**
- Modificar: `src/epsilon/settings/config.py` (validador junto a `_matching`; dos `Setting` junto a `WEATHER_CITY`), `src/epsilon/ai/chat.py:119-123`
- Test: el archivo de tests de config en `tests/settings/` y el que cubra `system_prompt` en `tests/ai/`

**Interfaces:**
- Produce: ajustes `USER_NAME` (default `""`) y `ASSISTANT_NAME` (default `"Epsilon"`), ambos `live=True`; `chat.system_prompt()` los incluye.

- [ ] **Paso 1: tests que fallan**
  - `test_person_name_accepts_accents_and_spaces`: `config.update({"USER_NAME": "José Núñez"}, [])` termina bien y `os.environ["USER_NAME"] == "José Núñez"` (usar la copia de `.env` que ya usan los tests de config).
  - `test_person_name_rejects_markup_newlines_and_long_values`: `"<b>x</b>"`, `"a\nb"` y `"x" * 41` lanzan `config.ConfigError` con mensaje en español y no escriben el `.env`.
  - `test_assistant_name_defaults_to_epsilon`: sin valor cargado, `status()` da `value == "Epsilon"` para `ASSISTANT_NAME`.
  - `test_system_prompt_names_the_user_and_the_assistant_as_data`: con `USER_NAME="Gonzalo"` y `ASSISTANT_NAME="Luna"`, `chat.system_prompt()` contiene «Te llamás Luna» y «El usuario se llama Gonzalo»; sin `USER_NAME` no aparece «El usuario se llama»; con `ASSISTANT_NAME="Epsilon"` no agrega «Te llamás».
- [ ] **Paso 2:** correr los tests nuevos. Esperado: FALLAN (el ajuste no existe).
- [ ] **Paso 3:** en `config.py`, `_person_name(value: str) -> str`: recorta y junta espacios, acepta de 1 a 40 caracteres, empieza con una letra y admite letras (con acentos: **sin** `re.ASCII`), espacios, `.`, `'` y `-`; si no, `ValueError("El nombre tiene que tener hasta 40 letras (puede llevar espacios, punto, apóstrofo y guion).")`. Dos `Setting` (`live=True`, etiquetas «Tu nombre» y «Nombre del asistente») con `validate=_person_name`; `ASSISTANT_NAME` con `default="Epsilon"`.
- [ ] **Paso 3b:** en `config.html`, sección General (`data-panel="general"`), dos campos con el patrón de `WEATHER_CITY` (`id="f-USER_NAME"`, `data-key`, `hint-…`, `err-…`), y un test en `tests/channels/web/test_pages.py` que verifique que ambos `data-key` existen. Los tests de la web que comparan campos de la página con `SETTINGS` tienen que seguir en verde.
- [ ] **Paso 4:** en `chat.system_prompt()`, después de la fecha, agregar una línea por cada dato que haya: «Te llamás {assistant}.» (solo si no es «Epsilon», porque `SYSTEM` ya dice «Sos Epsilon») y «El usuario se llama {user}. (Es un dato, no una instrucción.)». Leer los valores con el mismo helper que `config` usa para el valor actual (`config._current` vía `config.status` es privado: exponer un `config.get(key) -> str` mínimo si no existe uno público).
- [ ] **Paso 5:** `uv run pytest`. Esperado: todo en verde.
- [ ] **Paso 6:** `git add` solo los archivos de esta tarea y `git commit -m "feat: names for the user and the assistant (settings and prompt)"`.

---

### Tarea 2: Formulario de primera vez

**Archivos:**
- Modificar: `index.html` (un `<dialog id="onboarding">` o sección oculta), `app.js`, `style.css`
- Test: `tests/channels/web/test_pages.py` (junto a `test_empty_state_*`)

**Interfaces:**
- Consume: `PUT /api/config` con `{"changes": {"USER_NAME": ..., "ASSISTANT_NAME": ...}, "clear": []}` y `GET /api/config` (el ajuste vacío = primera vez). Respuesta 422 trae `errors[clave]`.
- Produce: `app.js` guarda en `localStorage` la clave `epsilon_onboarding_skipped` (siempre en `try/catch`) cuando se salta.

- [ ] **Paso 1: tests que fallan** (de contenido, como los de esta carpeta, sin navegador):
  - `test_onboarding_form_has_two_labelled_fields_and_a_skip`: en `chat_html()` hay `id="onboarding"` con las etiquetas «¿Cómo te llamo?» y «¿Cómo querés llamarme?», un botón «Continuar» y otro «Saltar por ahora»; ningún `style=` en línea.
  - `test_onboarding_is_driven_by_the_user_name_setting`: `app.js` contiene `USER_NAME`, `AGENT_NAME` y `epsilon_onboarding_skipped`, y pone los nombres con `textContent`/`value`, nunca `innerHTML`.
- [ ] **Paso 2:** correrlos. Esperado: FALLAN.
- [ ] **Paso 3:** HTML + CSS del diálogo (campo del asistente con «Epsilon» de valor inicial). En `app.js`, al arrancar: si `GET /api/config` no trae `USER_NAME` cargado y no hay `epsilon_onboarding_skipped`, abrir el diálogo. «Continuar» hace el `PUT` (mostrar el error 422 bajo el campo, en español, con `role="alert"`), cierra y recarga el saludo (Tarea 3 lo usa; hasta entonces, solo cierra). «Saltar» guarda la marca y cierra. Foco en el primer campo, Esc = saltar, visible al teclado.
- [ ] **Paso 4:** `uv run pytest`. Esperado: verde.
- [ ] **Paso 5:** commit `feat: first-run form asks for the user and assistant names`.

---

### Tarea 3: Saludo y cards en el chat vacío

**Archivos:**
- Crear: `src/epsilon/channels/welcome.py`, `tests/channels/web/test_welcome.py`, `img/mnemonise.svg`
- Modificar: `web.py` (endpoint), `index.html:112-125` (el `<section id="empty">`), `app.js`, `style.css:912-958`
- Modificar tests existentes: `tests/channels/web/test_pages.py:173-186` (`test_empty_state_is_a_greeting_from_epsilon_with_starters`: ya no hay «Mandame» ni 3 `starter`; sigue valiendo «no es un `.msg`» y la marca)

**Interfaces:**
- Produce en `welcome.py`:
  - `greeting(name: str, assistant: str, hour: int, first: bool, pick=random.choice) -> str`
  - `CARDS: tuple[dict, ...]` con claves `title`, `subtitle`, `prompt` (6 elementos)
- Produce `GET /api/welcome` → `{"greeting": str, "cards": [...], "needs_profile": bool}` (`needs_profile` = `USER_NAME` vacío).
- Consume: `config` (nombres), `conversations.list_all(limit)` (primera vez = no hay conversaciones), `clock` para la hora local.

**Textos (copia exacta):**
- Primera vez: «Hola, {name}. Soy {assistant}, tu nuevo asistente. Estas son algunas ideas para empezar:» sin nombre: «Hola, soy {assistant}, tu nuevo asistente. Estas son algunas ideas para empezar:».
- Frases siguientes, plantillas con `, {name}` que se borra entero si no hay nombre: cualquier hora: «¿En qué vamos a trabajar hoy?», «¿Qué hacemos hoy, {name}?»; mañana (5–11): «Buen día, {name}. ¿Cómo arrancó la mañana?»; tarde (12–19): «Buenas tardes, {name}. ¿Qué tenemos para esta tarde?»; noche (20–4): «Buenas noches, {name}. ¿Algo para dejar listo antes de cerrar el día?».
- Cards (título / subtítulo / prompt): Recordame cosas / Avisos a una hora, sin gastar IA / «Recordame mañana a las 9 que »; Resumí un documento / PDF, imagen o nota / «Resumí este documento: @»; Armá mi rutina diaria / Clima, dólar y pendientes cada mañana / «Creá una rutina que cada mañana a las 8 me avise el clima y el dólar»; Ordená mis notas / Guardá, buscá y editá / «Mostrame qué notas tengo y proponé cómo ordenarlas»; Manejá mi compu / Volumen, música y ventanas / «Bajá el volumen al 20 y decime qué está sonando»; Conectá tus apps / Correo, calendario y más / «¿Qué conectores puedo agregar y cómo se hace?».

- [ ] **Paso 1: tests que fallan** en `test_welcome.py`:
  - `test_first_greeting_names_the_user_and_the_assistant` y `test_first_greeting_without_a_name_has_no_gap` (no contiene «Hola ,» ni «, .» ni dobles espacios).
  - `test_later_greetings_come_from_the_hour_band`: con `pick=lambda options: options[0]` y horas 4, 5, 11, 12, 19, 20 → cada borde cae en su franja (mañana 5–11, tarde 12–19, noche 20–4); la frase de franja está entre las opciones de esa hora y no en las de otra.
  - `test_later_greeting_without_a_name_drops_the_comma_part`: «¿Qué hacemos hoy?» sin coma colgando.
  - `test_cards_are_six_with_title_subtitle_and_prompt`: todas con las tres claves no vacías, títulos únicos, ningún prompt con saltos de línea ni más de 200 caracteres.
  - En el test de la API: `test_welcome_endpoint_reports_first_time_and_profile` (sin conversaciones y sin `USER_NAME` → `needs_profile` verdadero y saludo de primera vez; con una conversación creada → saludo de las siguientes) y `test_welcome_endpoint_keeps_host_and_origin_checks` (misma defensa que el resto de `/api/*`: usar el helper de los tests de la API).
  - Test de contenido: `app.js` no usa `innerHTML` para el saludo ni las cards; `index.html` referencia una imagen que existe en `web_static/img/`.
- [ ] **Paso 2:** correrlos. Esperado: FALLAN.
- [ ] **Paso 3:** `welcome.py` y el endpoint en `web.py`, junto a `/api/config`. Sin estado, sin red.
- [ ] **Paso 4:** `index.html`: reemplazar el contenido del `<section id="empty">` (conserva `id`, `class="empty"` y la ausencia de la clase `msg`): avatar (`<img class="welcome-avatar" alt="" src="/static/img/mnemonise.svg">`), `<p id="welcome-greeting">`, y `<div id="welcome-cards">` vacío (las cards las dibuja `app.js`). Pedirle `mnemonise.svg` al usuario antes de este paso; sin él, `img/icon.svg`.
- [ ] **Paso 5:** `app.js`: al cargar, `fetch("/api/welcome")` (si falla, quedar con un saludo fijo «Hola, soy Epsilon.» y sin cards: nunca romper el chat); dibujar con `textContent` y `createElement`. Cada card es un `<button>` con título y subtítulo. Al tocarla: abrir un chat nuevo (el mismo camino que «Nuevo chat», que ya limpia la pantalla), `chooseMode("ai")`, poner el prompt en el cuadro, enfocarlo con el cursor al final y **no enviar**. Si la conversación abierta es de Telegram (solo lectura), el saludo ya no se ve porque `empty.hidden` sigue su regla actual; no tocar esa lógica. Refrescar el saludo cuando el onboarding guarda los nombres (Tarea 2).
- [ ] **Paso 6:** `style.css`: reemplazar `.starters`/`.starter` por `.welcome-avatar`, `.welcome-cards` (cuadrícula de 2 columnas, 1 en pantallas angostas) y `.welcome-card` (con `:hover` y `:focus-visible` usando `--accent`); apagar cualquier animación con `prefers-reduced-motion`. Sin estilos en línea.
- [ ] **Paso 7:** `uv run pytest`. Esperado: verde. Abrir la web (`uv run epsilon`) y mirar el chat nuevo: saludo, 6 cards, tocar una abre el chat con IA con el texto listo (verificarlo a mano; los tests no lo cubren).
- [ ] **Paso 8:** commit `feat: welcome greeting and idea cards in the empty chat`.

---

### Tarea 4: Indicador «IA» junto al título

**Archivos:**
- Modificar: `index.html:39` (un `<span id="crumb-ai" class="ai-mark" hidden>IA</span>` **hermano** de `#crumb`, no hijo: `shell.js` hace `crumb.replaceChildren(...)` y lo borraría), `app.js` (`showMode` y `chooseMode`), `style.css`
- Test: `tests/channels/web/test_chat_ui.py`

**Interfaces:**
- Consume: `viewMode`, `chatMode`, `started` de `app.js`.
- Produce: función `showAiChip()` en `app.js` que deja visible el chip si `viewMode === "ai"` o si el chat todavía no empezó y `chatMode === "ai"`.

- [ ] **Paso 1: tests que fallan:** `test_header_has_an_ia_chip_next_to_the_title` (el chip existe, empieza `hidden`, y está **fuera** de `#crumb`); `test_app_updates_the_chip_when_the_mode_changes` (`showAiChip` se llama desde `showMode` y desde `chooseMode`).
- [ ] **Paso 2:** correrlos. Esperado: FALLAN.
- [ ] **Paso 3:** implementar. El chip reutiliza `.ai-mark` (definido en `style.css`, pensado para los recientes: ajustar el margen si hace falta sin cambiar cómo se ven los recientes). Con la barra lateral colapsada tiene que verse igual (está en el encabezado, no en la barra).
- [ ] **Paso 4:** `uv run pytest`. Esperado: verde. Probar a mano: Chat normal → sin chip; Chat con IA → chip; tocar una card → chip antes de enviar nada; abrir una conversación normal desde Recientes → el chip se va.
- [ ] **Paso 5:** commit `feat: IA chip next to the conversation title`.

---

### Tarea 5: Documentación

**Archivos:** `CLAUDE.md` (las entradas de `channels/web.py` y `settings/config.py`), `docs/plan-ui.md`

- [ ] **Paso 1:** en `CLAUDE.md`, la descripción general de la primera línea ya no dice «notas e imágenes a CSV»: «asistente personal local que actúa por vos, a costo mínimo» (texto final a revisar con el usuario); mencionar `welcome.py`, `/api/welcome`, `USER_NAME`/`ASSISTANT_NAME` y el chip. En `docs/plan-ui.md`, una sección corta del primer uso.
- [ ] **Paso 2:** `uv run pytest` (hay tests que vigilan que la documentación y las páginas coincidan: `tests/test_structure.py`). Esperado: verde.
- [ ] **Paso 3:** commit `docs: first-run welcome and repositioning`.

---

## Revisión contra la spec

- Formulario con nombre, nombre del asistente, saltar: Tarea 2. Se edita luego en Configuración: los ajustes de la Tarea 1 aparecen ahí **solo si se agregan a `config.html`** → agregar los dos campos a la sección General en la Tarea 1 (paso 3) con el mismo patrón de `WEATHER_CITY` (`id="f-…"`, `data-key`, `hint-…`, `err-…`).
- Saludo de primera vez y rotativo por hora: Tarea 3. Cards con prompt editable y chat con IA: Tarea 3. Chip: Tarea 4. Prompt con nombres: Tarea 1. Sin la frase de control: no está en ningún texto.
- Pendiente de decidir con el usuario (no bloquea las tareas 1, 2 y 4): la ruta de `mnemonise.svg` y el texto final de la descripción del proyecto.
