# Plan: varios modelos y proveedores (idea para la v2, sin implementar)

Hoy `ai/llm.py` es el único lugar que habla con un modelo y solo habla con Gemini: `GEMINI_MODEL` elige entre
modelos de Gemini, nada más. Claude, Ollama u otro proveedor necesitan código nuevo.

- **Qué se quiere.** (1) Poder usar otros proveedores (Anthropic, Ollama local, cualquier API compatible con
  OpenAI). (2) Elegir qué modelo atiende cada tipo de tarea.
- **Lo que NO se hace: adivinar si una tarea es simple o intensa.** Un clasificador gastaría tokens y una
  heurística sobre el texto se equivocaría, contra «gastar lo mínimo». Epsilon ya sabe qué tarea es porque cada
  una tiene su camino: `/ia`, `/resumir`, imagen→CSV y el chat con herramientas. Lo elige la persona, por tarea.

## Orden de trabajo

1. **Abstracción de proveedores, con Gemini como único backend.** `llm.py` pasa a ser una interfaz con un
   proveedor por backend; cada uno implementa lo que hoy existe: `generate` (un disparo) y `chat_tools` (con
   herramientas), más `check` y `current_model`. Todo tiene que quedar andando igual y los tests no cambian de
   comportamiento (siguen sin llamar a ningún modelo; `conftest.py` bloquea todos los proveedores).
2. **Anthropic y Ollama (o API compatible con OpenAI).** Un proveedor por vez, cada uno con su clave o URL
   en el `.env`, su prueba de conexión sin gasto (`settings/checks.py`) y su entrada en Configuración.
3. **Modelo por tarea.** Un ajuste por tipo de tarea en Configuración (`/ia`, resúmenes, imagen→CSV, chat con
   herramientas) y un modelo predeterminado para las que no se elijan. Más adelante, quizá una regla simple por
   tamaño (p. ej. «PDF de más de N páginas usa el modelo fuerte»); no ahora.

## Puntos a resolver (lo que complica cada proveedor)

- **Precios y límite de costo.** `usage.PRICES` y el límite por conversación (`chat.status`) necesitan un precio
  por modelo. Un modelo local cuesta cero: hay que tratarlo como «precio conocido, gratis» y no como «sin precio»
  (que hoy bloquea el chat).
- **Herramientas.** Declarar y devolver llamadas a herramientas cambia entre proveedores, y no todos los modelos
  locales las soportan: el proveedor tiene que decir si puede, y si no, el modo IA no ofrece herramientas.
- **Adjuntos.** Imágenes y PDF se mandan distinto, y un modelo local puede no leerlos: la imagen→CSV y el resumen
  de PDF tienen que avisar con claridad en vez de fallar.
- **Reglas que siguen valiendo.** Las claves nunca salen del servidor (ni a logs ni a errores: `config.redact`),
  `max_output_tokens` siempre limitado, el contenido de notas/imágenes es DATO, y los permisos de las
  herramientas se aplican en código (`core._run_calls`), no en el prompt.
