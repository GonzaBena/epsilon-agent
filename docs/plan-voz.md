# Plan: voz (Whisper local)

Sigue a `docs/plan-tools.md`. Objetivo: poder **mandar un audio** (web y Telegram) en
vez de escribir. La voz es una forma de ENTRADA: audio → texto → `core.handle` como
cualquier mensaje. No es una herramienta del panel ni entra al registro.

## Decisiones propuestas

- **Local, con `faster-whisper` y el modelo `large-v3-turbo`.** Sin tokens, sin costo
  por uso, y el audio no sale de la PC. Pesa ~1,6 GB en disco y ~1–1,5 GB de RAM
  mientras está cargado.
- **CPU primero (`int8`).** Tu PC tiene 16 núcleos y 15 GB de RAM; la GPU (GTX 1650
  SUPER, 4 GB) entraría, pero exige instalar las librerías CUDA (~1 GB más) y un
  aviso aparte. Se mide en CPU y, si queda lenta, se agrega GPU como opción
  (`WHISPER_DEVICE`).
- **Se carga al primer audio y se descarga solo** tras un rato sin usarse (por
  defecto 10 minutos; ajustable). Un solo audio a la vez (candado).
- **Un solo módulo lo toca: `transcribe.py`**, como `llm.py` con Gemini. Los tests
  nunca cargan el modelo (`conftest.py` lo bloquea y usan uno falso).
- **Instalación opcional**: `uv sync --extra voz`. Quien no quiera voz no descarga
  nada; sin la extra, mandar un audio responde cómo activarla.
- **El modelo se descarga solo** (decisión del usuario). Con el primer audio, si no está,
  se baja en segundo plano (~1,6 GB, una sola vez) y la respuesta avisa «estoy
  descargando el modelo de voz; mandá el audio de nuevo en unos minutos». No se deja
  colgado un pedido durante minutos. Si la descarga falla, el próximo audio informa el
  error y vuelve a intentar.
- **Idioma**: español por defecto (`WHISPER_LANGUAGE`, editable; vacío = detectar).
- **Telegram**: la nota de voz se transcribe y el pedido **se ejecuta directo, sin
  pedir confirmación** (si no, sería lo mismo que escribir). Antes de la respuesta
  va un mensaje aparte con lo entendido («🎙 «…»») para ver qué escuchó. Una confirmación
  opcional (apagada por defecto, en la configuración) queda como idea para más adelante:
  agregaría fricción, por eso no se hace ahora.
- **Web**: botón de micrófono en el cuadro de texto. Graba, transcribe y **deja el
  texto en el cuadro sin enviarlo**, para revisarlo y corregirlo (igual que las
  herramientas del panel). El micrófono funciona en `localhost`.
- **Seguridad**: el audio es DATO. Tope de tamaño y de duración, solo `audio/*`, se
  procesa en un archivo temporal que se borra siempre, y NO se guarda en `DATA_DIR`
  salvo que algún día se pida. Telegram sigue respondiendo solo al ID autorizado. Lo
  transcripto es texto del usuario (un audio que dice «/resumir x» lo ejecuta igual
  que si lo escribieras).

## Pasos (cada uno con tests y commit)

1. `transcribe.py` sin canales: cargar bajo demanda, descargar tras inactividad,
   candado, errores claros (falta la extra, falta el modelo, audio ilegible o muy
   largo). Tests con un modelo falso y reloj controlado. **Hecho** (`transcribe.py`,
   `tests/test_transcribe.py`; `conftest.py` bloquea cargar o bajar Whisper de verdad).
   Todavía no está conectado a ningún canal ni declarada la extra `voz`: eso es el paso 2.
2. Instalar la extra, dejar que se descargue el modelo y probar con un audio real.
   **Hecho** (CPU, `int8`, audio de prueba de 13,6 s en mp3 y en webm/opus):
   - Cargar el modelo: ~5 s. Transcribir con el modelo cargado: ~6 s (≈0,45× el tiempo del
     audio). Primer audio (carga + transcripción): ~11 s. El texto sale bien en español
     (con algún error de palabra, normal en Whisper).
   - RAM: ~1,1 GB con el modelo cargado. **Soltar el modelo NO bajaba la RAM** (seguían ~1 GB):
     el asignador de Linux no la devuelve. Con `malloc_trim` baja a ~200 MB, y quedó en la
     descarga por inactividad (`_release_memory`).
   - Hallazgo de la prueba real: `faster-whisper` 1.2.1 no anda con `av` 19 (`metadata_errors`);
     la extra fija `av<19` y un test lo vigila. Sin esto, todo audio fallaba.
   - El modelo ya estaba en la caché de Hugging Face: no hizo falta bajar nada.
3. Telegram: notas de voz y archivos de audio. **Hecho.** `on_voice` (notas de voz, audios y
   documentos `audio/*`, solo del ID autorizado): baja el audio a un archivo temporal fuera
   de `DATA_DIR` (se borra siempre), transcribe en otro hilo (no traba al bot), manda
   «🎙 «…»» y después responde al pedido sin confirmación. Problemas de voz (no instalada,
   descargando, sin voz, muy largo) se muestran tal cual; un fallo inesperado da «No pude
   procesar el audio». Más de 20 MB se rechaza antes de bajarlo. La ayuda lo menciona.
   **Palabras de comando** (`transcribe.spoken_commands`, vale para todo canal): al principio
   del texto transcripto, «más X» pasa a `+ X` y «barra uso» a `/uso`. En el medio no se toca.
   Pendiente: Whisper se comió el «más» inicial de una frase corta (probable recorte del
   detector de voz); se mide con una grabación corta.
4. Web: endpoint `/api/transcribe` y botón de micrófono. **Hecho.**
   - `POST /api/transcribe` (multipart, solo `audio/*`, tope de 20 MB; mismas defensas de
     Host/Origin que `/api/upload`). Devuelve `{text}` y NO toca el núcleo ni guarda nada: el
     audio va a un archivo temporal fuera de `DATA_DIR` que se borra siempre. Problemas de voz
     → 422 con el mensaje; un fallo inesperado → 500 «No pude procesar el audio.» (detalle a la
     terminal, redactado).
   - Botón de micrófono junto al clip (oculto si el navegador no puede grabar). Un toque graba,
     otro para y transcribe; el texto se SUMA al cuadro sin enviarse. Los avisos («Grabando…»,
     «Transcribiendo…», errores) salen en una línea bajo el cuadro. Al parar se apaga el micrófono.
   - Verificado en el navegador con un micrófono falso (tono): grabación real en webm/opus,
     envío real, texto en el cuadro, nada enviado. El permiso del micrófono y la voz real
     quedan por probar con tu navegador.
5. Configuración: ajustes de idioma, minutos de inactividad y dispositivo en `/config`;
   estado de la voz en el panel (cargado / descargado / no instalado). Docs.

## Sin verificar hasta probarlo

- Velocidad con audios largos (1 y 5 minutos): se midió solo con uno de 13,6 s.
- (Resuelto) `faster-whisper` 1.2.1 y `ctranslate2` 4.8.3 tienen paquete para Python 3.12.
- Grabación del micrófono en el navegador (permisos, formato `webm/opus`).
