# Plan: web mínima por defecto

Sigue a `docs/plan-core.md`. La web es el canal estándar de Epsilon: siempre
disponible en la PC, sin depender de ningún servicio externo.

## Alcance

Página de chat en `http://127.0.0.1:8765` sobre el mismo núcleo que Telegram:
mensajes y comandos, adjuntar/arrastrar imágenes y PDFs, botones del núcleo
(Guardar, Cancelar, Resumir) e indicador de espera.

Fuera de alcance por ahora: conversaciones, memoria, panel de herramientas,
pantalla de canales y configuración (están en el mockup, vienen después).

## Decisiones

- **Solo en la PC.** La web escucha únicamente en `127.0.0.1`, sin login.
  Para el celular se usa Telegram. Exponerla a la red exigiría contraseña y HTTPS.
- **Tres defensas** (la web no tiene login, pero cualquier página abierta en
  el navegador puede intentar hablarle a `localhost`):
  1. Rechazar requests cuyo `Host` no sea localhost / 127.0.0.1 / [::1]
     (contra DNS rebinding).
  2. Rechazar requests con `Origin` distinto de la propia web.
  3. La API de mensajes recibe solo JSON; la subida (multipart) pasa por el
     chequeo de `Origin`.
- **Sesión única** (`web`): recargar la página no pierde un «Guardar» pendiente.
  El historial visible vive en el `localStorage` del navegador.
- **El contenido es dato:** el frontend lo muestra siempre con `textContent`,
  nunca como HTML.
- **Frontend sin build**, con HTML, CSS y JS en archivos separados
  (`channels/web_static/index.html`, `style.css`, `app.js`). Sin npm, sin CDN,
  sin fuentes externas.
- **Convivencia de canales:** la web corre en el hilo principal (uvicorn maneja
  las señales de systemd). Telegram corre en un hilo aparte, con su propio
  bucle de eventos y sin manejadores de señales; si falla, solo muere ese hilo
  y la web sigue. Alternativa si los hilos dan problemas: un único bucle asyncio.
- Solo `WEB_PORT` es configurable (por defecto 8765). El host queda fijo en
  127.0.0.1 a propósito: sin login, no se expone a la red.

## Pasos (cada uno con tests y commit)

1. **API y seguridad** (`channels/web.py`): `POST /api/message`,
   `POST /api/upload`, `GET /api/health`, y el middleware de Host/Origin.
2. **Página** (`web_static/`): chat, adjuntos y botones. Se revisa abriéndola
   en un navegador.
3. **Convivencia en `main`:** web en el principal, Telegram en un hilo
   aislado; reiniciar el servicio y verificar que ambos respondan, y que con
   Telegram sin configurar la web arranque sola.

## Notas

- `uv_build` empaqueta `web_static/` (verificado construyendo el wheel).

## Estado

Los 3 pasos están hechos. Verificado con arranques reales: Telegram con token
inválido no tumba la web; sin Telegram configurado la web arranca sola;
Telegram sin red reintenta sin límite; SIGTERM apaga todo en ~1 s; puerto
ocupado o WEB_PORT inválido cortan con un mensaje claro.
