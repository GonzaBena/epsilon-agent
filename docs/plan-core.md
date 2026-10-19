# Plan: núcleo agnóstico de canales

## Objetivo

Epsilon se usa de a una persona, hospedado por ella misma. La web local es el
canal por defecto y estándar: siempre disponible, sin depender de ningún
servicio externo. Telegram, Slack, etc. son canales opcionales; si uno se cae
o no está configurado, el resto sigue funcionando.

## Decisiones

- La web escucha solo en `127.0.0.1`, sin login (uso personal, no multiusuario).
  Si algún día se expone a la red, se agrega contraseña.
- Toda la lógica (comandos, imagen→CSV, confirmaciones) vive en `core.py`.
  Los canales solo traducen mensajes de ida y vuelta.
- La confirmación antes de guardar queda en un solo lugar (el núcleo), no una
  vez por canal.
- Los botones son otro mensaje (`Message(action=...)`), así funcionan igual
  en cualquier canal.
- Cada canal limita el largo del texto según sus propias reglas (Telegram:
  4000 caracteres); el núcleo devuelve el texto completo.

## Estructura objetivo

```
core.py          handle(Message) -> Reply, estado pendiente por sesión
channels/
  telegram.py    el bot.py actual, adelgazado
  web.py         FastAPI en 127.0.0.1 (después)
main (entrada)   levanta los canales configurados
```

Tipos: `Message` (text, attachment, session_id, action), `Reply` (text,
actions), `Attachment` (data, name, mime), `Action` (id, label).

Estado pendiente por sesión (en memoria por ahora): CSV esperando
confirmación, modo `/ticket`, archivo guardado esperando «Resumir / Nada más».

## Pasos (cada uno con tests y commit)

1. Tipos y esqueleto de `core`, con tests de los comandos simples
   (`+`, `/ver`, `/buscar`, `/archivos`, `/uso`, ayuda). No se toca `bot.py`.
2. Flujos con estado: imagen→CSV con confirmación, `/ticket` y `/cancelar`,
   resumir con botones. Tests mockeando `llm.generate`.
3. Mover `bot.py` a `channels/telegram.py` como traductor fino. La config
   deja de hacer `SystemExit` al importar y pasa al arranque.
4. Punto de entrada que levanta los canales configurados; actualizar
   `deploy/epsilon.service`; reiniciar y confirmar que quedó activo.

Estado: los 4 pasos están hechos. Queda pendiente decidir cómo conviven dos
canales bloqueantes en el mismo proceso (se resuelve al sumar la web).

## Después

- Web mínima por defecto (FastAPI + chat simple).
- Pantalla de configuración y canales (claves, whitelist) para no tocar `.env`.
- Registro de herramientas, conversaciones, memoria.
- Más canales (Slack) y más proveedores de modelo detrás de `llm.generate`.

## Notas

- `bot.py` define `other` dos veces; la primera es código muerto. Limpiar al migrar.
- Hoy no hay tests de los flujos de `bot.py`; los de `core` serán los primeros.
