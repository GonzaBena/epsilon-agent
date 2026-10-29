# Atajo de texto del Voice Companion (Alt+C) — diseño

Fecha: 2026-10-29. Extiende `2026-10-29-voice-companion-design.md` (el Companion de voz, pasos 1 a 7 hechos).

## Intención

Estás programando (o haciendo cualquier cosa) y te surge una duda o algo que anotar: «recordame agregar esta feature a
este proyecto». Hoy sin micrófono hay que ir a la web o a Telegram y explicar el contexto a mano («en tal proyecto, en
tal sección»). Con este atajo apretás **Alt+C**, escribís una frase, Enter, ves cómo piensa y responde, cerrás la tarjeta
y seguís trabajando. El contexto («dónde estoy») lo pone Epsilon solo.

## Objetivos

- Un segundo atajo global (`VOICE_TEXT_HOTKEY`, por defecto `ALT, C`) que abre una tarjeta con un campo de texto.
- Al enviar, la misma tarjeta muestra el progreso en vivo (Pensando…, herramientas, texto de la respuesta mientras se
  escribe), **sin audio**.
- Cada pedido lleva el contexto de la ventana en foco, leído **antes** de que la tarjeta tome el teclado.
- Cerrar la tarjeta (Esc o Alt+C otra vez) no cancela el turno: sigue y la respuesta queda guardada.
- Gastar lo mínimo: texto primero; la captura de pantalla solo si el modelo la pide.

## No-objetivos y anti-objetivos

- No es un chat: sin historial navegable en la tarjeta, sin arrastrar ni redimensionar (la conversación igual queda
  guardada y se ve en la web).
- No cambia Alt+X: su tarjeta sin foco, su audio y su regla de captura quedan como están.
- Anti-objetivo: que abrir la tarjeta le robe el foco a la ventana de trabajo de forma que no se pueda volver a ella, o
  que el contexto de la ventana sea la propia tarjeta.
- Anti-objetivo: que algo destructivo corra sin preguntar. Se mantiene el modo «YOLO seguro».

## Cómo funciona (lo que decidiste vos)

1. Alt+C abre la tarjeta con el campo de texto con foco.
2. Enter envía. El campo se reemplaza por el estado: «Pensando…», pasos de herramientas y el texto en vivo.
3. Esc / Alt+C cierra la tarjeta; el turno no se corta.
4. Se usa texto siempre que se pueda. La captura de pantalla (`pc.captura`) corre sola, sin preguntar, cuando el modelo
   la pide, aunque el modelo sea de la nube: no te importa mandar capturas a la nube porque no usás planes gratuitos.
5. Se respeta `IMAGES_TO_CLOUD` (commit `3e3becc`): con él apagado la captura no se ofrece a un modelo de nube.

## Decisiones mías (revisalas)

- **Contexto de ventana**: al llegar la orden `ask` el canal lee la ventana activa con `utils/ventanas.py` (título, clase,
  workspace; solo lectura) y la antepone al mensaje como dato rotulado («Ventana en foco: …»), redactada y recortada. No se
  manda imagen de entrada.
- **Misma conversación y modo**: el texto entra por `Assistant.ask` en la conversación `companion:voice`, con
  `permissions.SAFE_AUTO` y los topes de siempre (`VOICE_MAX_TURNS`, tope de costo). Una pregunta de permiso que llegara se
  deniega, igual que por voz.
- **Gasto**: se anota como «Companion» (distinto de «Voz») para poder separarlo en /uso.
- **Captura sin preguntar en este atajo**: el turno de texto marca el origen; `permissions` deja correr `sees_screen` sola
  cuando el origen es el atajo de texto y `IMAGES_TO_CLOUD` lo permite. Alt+X sigue con `_local_model`.
- **Sin audio**: el `Speaker` ignora los turnos con origen texto (el evento `turn start` lleva `source: "text"`).
- **Cerrar no cancela**: solo esconde la tarjeta. Empezar a grabar con Alt+X o enviar otro texto sí corta el turno anterior
  (como hoy).

## Piezas y cambios

- `voice/client.py`: el script del atajo ya manda una palabra al socket; se suma la palabra `ask` (y el script se genera
  con ambas). El servidor sigue descartando lo que no conoce.
- `channels/voice.py`: nuevo comando `ask` en el socket → pide a la tarjeta el modo entrada y, cuando llega el texto, llama
  a `Assistant.ask(text, context=…, source="text")`. Mantiene el atajo de Hyprland igual al ajuste (como `VOICE_HOTKEY`).
- `utils/hyprland.py`: escribe y aplica también el atajo de texto (mismo mecanismo de `ok` exacto, Lua o clásico).
- `voice/overlay.py` / `overlay_window.py`: un **modo entrada**. Hoy la ventana usa `KeyboardMode.NONE` (nunca roba el
  teclado). En modo entrada pasa a `ON_DEMAND` (o `EXCLUSIVE` si hace falta) con un `Gtk.Entry`, y al enviar o cerrar
  vuelve a `NONE` y a ser transparente a los clics. Nuevas órdenes JSON: `input` (abrir) y evento de vuelta
  `submit:<texto>` / `closed` por stdout. Lo que se escribe es dato: se valida el largo (tope 500 caracteres) antes de
  salir del proceso de la ventana.
- `settings/config.py`, `settings_default.json`, `config.html`: `VOICE_TEXT_HOTKEY` (texto, default `ALT, C`; vacío =
  desactivado) y su hint, junto a `VOICE_HOTKEY`.
- `ai/permissions.py`: la excepción de captura descrita arriba.
- Docs: `CLAUDE.md` (sección Voice Companion) y `docs/plan-voice-companion.md`.

## Restricciones y riesgos

- **Foco con layer-shell** es lo más delicado: pedir el teclado solo mientras el campo está abierto y devolverlo al cerrar,
  para que la ventana de trabajo recupere el foco. Se prueba primero con `--demo`.
- Leer la ventana activa **antes** de abrir la tarjeta; si falla, se sigue sin contexto (nunca frena el pedido).
- Si la ventanita no está disponible (`VOICE_OVERLAY` apagado o falló), el atajo de texto avisa que necesita la
  ventanita; no hay alternativa por notificaciones (no tienen campo de texto).
- Pruebas con falsos: nada de `hyprctl`, ventanas, audio ni modelos reales (regla del proyecto). El modelo se prueba aparte
  con Ollama local.

## Pruebas (qué se verifica)

- El socket acepta `ask`, descarta lo desconocido y no ejecuta si la ventanita está apagada.
- `Assistant.ask` con contexto: el mensaje lleva la ventana en foco rotulada como dato y redactada.
- El `Speaker` no habla un turno `source: "text"`.
- Captura: corre sola con origen texto + `IMAGES_TO_CLOUD` sí; no se ofrece con modelo de nube + `IMAGES_TO_CLOUD` no; Alt+X
  no cambia.
- El script del atajo y la config de Hyprland incluyen ambos atajos; `VOICE_TEXT_HOTKEY` vacío no escribe ninguno.
- El protocolo de la ventana: `input` abre el modo, `submit` sale con el texto recortado, Esc cierra sin enviar.

## Lo que queda para quien lo construya

- Elegir `ON_DEMAND` vs `EXCLUSIVE` según lo que Hyprland haga con el foco (probar en vivo).
- Distribución exacta de la tarjeta en modo entrada (reusa el estilo existente; sin rediseño).
- Nombre exacto de los eventos nuevos, mientras sigan el esquema de `voice/events.Bus`.
