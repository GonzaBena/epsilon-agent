# Mejoras y Nuevas Herramientas para Epsilon

Propuestas seleccionadas para ampliar las capacidades de Epsilon manteniendo su filosofía: local-first, bajo costo de tokens, privacidad en `DATA_DIR` y permisos estrictos.

---

## 1. Consultas y análisis sobre datos guardados

Aprovechar que Epsilon ya extrae datos de tickets e imágenes a CSV y guarda notas/archivos en `DATA_DIR`.

### 1.1 `consultar_csv` / `analizar_gastos`
* **Objetivo:** Responder preguntas del estilo *"¿Cuánto gasté en el supermercado este mes?"* o *"Listame los tickets de la última semana"*.
* **Problema actual:** Si el CSV es largo, pasarle todo el contenido al LLM en cada mensaje gasta muchos tokens o puede superar límites de contexto.
* **Diseño propuesto:**
  * Una tool de lectura (`ai="read"`) que tome como argumento el nombre del archivo y un criterio/consulta.
  * Ejecutar una consulta o filtro en Python usando la librería estándar `csv` o SQLite en memoria (`sqlite3`).
  * Entregar a la IA únicamente las filas relevantes agregadas o filtradas para que formule la respuesta al usuario.

### 1.2 `enviar_archivo` (Integración Telegram)
* **Objetivo:** Cuando el usuario pide *"Mandame el ticket de ayer"* o *"Pasame el CSV de gastos"*, enviar el documento directamente al chat de Telegram en vez de solo indicar la ruta local como texto.
* **Diseño propuesto:**
  * Tool de lectura (`ai="read"`).
  * Validar la ruta mediante `safe_path` dentro de `DATA_DIR`.
  * Incorporar un método en el canal de Telegram para despachar `send_document` / `send_photo` con el archivo solicitado.

---

## 2. Utilidades locales rápidas (Sin costo de IA)

Tools determinísticas ejecutadas en Python puro que ahorran tokens y dan respuestas inmediatas.

### 2.1 `dolar` / `cotizaciones` (`/dolar`)
* **Objetivo:** Consultar cotizaciones en tiempo real (oficial, blue, MEP) sin gastar tokens en el modelo.
* **Diseño propuesto:**
  * Comando `/dolar` en el núcleo y tool `ai="read"` para que la IA también pueda usar el dato fresco si el usuario pregunta en lenguaje natural.
  * Conexión a una API pública y gratuita (ej. DolarApi) con caché en memoria de 5-15 minutos para evitar peticiones repetidas.
  * Se alinea con la configuración existente `USD_ARS` en `config.py`.

### 2.2 `clima` (`/clima [ciudad]`)
* **Objetivo:** Reporte rápido del tiempo actual y pronóstico.
* **Diseño propuesto:**
  * Consulta directa a `https://wttr.in/<ciudad>?format=3` o en formato JSON/texto conciso.
  * No gasta tokens y se resuelve en milisegundos.

### 2.3 `fecha_hora` (Herramienta contextual para la IA)
* **Objetivo:** Evitar que el modelo alucine fechas relativas ("hoy", "ayer", "el viernes que viene") cuando registra notas o busca archivos.
* **Diseño propuesto:**
  * Tool simple `fecha_hora` (`ai="read"`) o inyección directa de la fecha y hora actual en el system prompt / contexto de la conversación.

---

## 3. Integración con el entorno (Linux y Escritorio)

### 3.1 Bóveda de Obsidian (`obsidian_sync`)
* **Objetivo:** Integrar Epsilon con una bóveda existente de Obsidian en el sistema.
* **Diseño propuesto:**
  * Parámetro opcional en `.env` (ej. `OBSIDIAN_VAULT_PATH=/home/...`).
  * Las notas creadas o editadas pueden guardarse directamente en carpetas de la bóveda (respetando frontmatter y wikilinks si se desea).
  * Mantiene el principio de archivos Markdown en texto plano.

### 3.2 Comandos y scripts de sistema controlados
* **Objetivo:** Realizar tareas básicas de mantenimiento en la máquina host (ej. espacio en disco `df -h`, estado de servicios, backup de `DATA_DIR`).
* **Seguridad y permisos:**
  * Permiso estricto por defecto (`ai_default="deny"` o `"ask"`).
  * Whitelist estricta de comandos permitidos (sin ejecución de shell libre ni arbitraria) para evitar vulnerabilidades.

---

## 4. Tareas y recordatorios (Análisis de implementación)

Este módulo combina dos necesidades con complejidades técnicas distintas:

### 4.1 Tareas (To-Do List) — *Fácil de implementar*
* **Concepto:** Similar a cómo funciona `memory/store.py`, pero para tareas pendientes.
* **Almacenamiento:** Archivo plano `DATA_DIR/todo.md` con formato Markdown estándar:
  ```markdown
  - [ ] Comprar café (creado: 2026-10-21)
  - [x] Pagar internet (completado: 2026-10-21)
  ```
* **Comandos directos:**
  * `/todo [texto]`: Agrega una tarea pendiente.
  * `/tareas`: Lista las pendientes con su identificador.
  * `/hecho [número]`: Marca la tarea como completada.
* **Integración con IA:** Tools `ai="read"` (`tareas`) y `ai="write"` (`todo`, `hecho`).
* **Ventajas:** No requiere hilos extras, no consume recursos en segundo plano y es 100% determinístico.

### 4.2 Recordatorios asíncronos — *Requiere diseño proactivo*
* **Desafío técnico:** Epsilon está diseñado como un sistema *reactivo* (recibe un mensaje HTTP o webhook de Telegram y genera una respuesta `handle(Message) -> Reply`). No genera mensajes espontáneos por sí mismo.
* **Opciones de arquitectura:**

  1. **Opción A: Planificador ligero en el proceso (Background Thread / Asyncio Task)**
     * Un hilo o tarea asyncio interna que corre en `main.py` junto a los canales.
     * Almacena los recordatorios en `DATA_DIR/reminders.json` (para persistir tras reinicios de `epsilon.service`).
     * Cada 30-60 segundos verifica si hay recordatorios vencidos.
     * Al vencer, llama directamente al método de envío del canal correspondiente (ej. `bot.send_message` en Telegram).
     * *Pros:* No depende de herramientas externas del SO. Funciona dentro de la instancia existente.

  2. **Opción B: Delegación al sistema operativo (`systemd-run` o `at`)**
     * Al programar un recordatorio, se programa un comando que invoque a Epsilon (vía API local `POST /api/notify` o script CLI).
     * *Pros:* Cero consumo de ciclos de reloj mientras espera.
     * *Contras:* Mayor acoplamiento a Linux y permisos de usuario.

* **Recomendación para Epsilon:**
  * La **Opción A** es la más portable y encaja con la estructura de hilos de `main.py` (donde Telegram y los plugins ya corren en sus propios hilos sin tumbar la web).
