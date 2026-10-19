# Plan: pantalla de configuración

Sigue a `docs/plan-web.md`. Objetivo: que quien instale Epsilon cargue sus
claves desde la web, sin editar el `.env` a mano.

## Qué se configura

| Ajuste | Editable | Cuándo se aplica |
|---|---|---|
| `GEMINI_API_KEY` | sí | al instante (`llm.py` lee el entorno en cada llamada) |
| `GEMINI_MODEL` | sí | al instante |
| `TELEGRAM_TOKEN` | sí | al reiniciar |
| `ALLOWED_USER_ID` | sí | al reiniciar |
| `WEB_PORT` | sí | al reiniciar |
| `DATA_DIR` | no, solo se muestra | cambiarlo en caliente dejaría las notas viejas atrás |

La pantalla también muestra el estado de cada canal (activo, sin configurar,
detenido con su error).

## Decisiones

- **El `.env` es la única fuente.** Se escribe con `dotenv.set_key` (conserva
  comentarios y claves ajenas), con permisos `0600`. Está en `.gitignore`.
- **Las claves nunca vuelven al navegador, ni parcialmente.** La API devuelve
  `{"set": true, "length": N}` y la página dibuja N puntos. Los puntos van como
  *placeholder* del campo, nunca como su valor: el campo real queda vacío, y
  vacío significa «no tocar». Para borrar una clave hay un botón aparte.
- **Validación estricta antes de escribir**, todo o nada. Un valor con salto de
  línea podría inyectar otras variables en el `.env`. Token de Telegram con su
  formato, ID solo dígitos, puerto 1–65535, modelo `[\w.-]`, sin saltos de línea.
- **Borrar = escribir el valor vacío** (`KEY=""`), no quitar la línea. Así el
  aviso de reinicio detecta la diferencia sin estado extra.
- **Botones «Probar» que no gastan tokens:** Telegram con `getMe` (gratis);
  Gemini con los metadatos del modelo (`models.get`), dentro de `llm.py`.
  Solo corren cuando el usuario los toca.
- **Reinicio manual, nunca desde la web.** Si el proceso se cae, un botón
  dentro de la misma web no sirve. La pantalla muestra un aviso persistente,
  por cada ajuste cambiado, con la razón concreta («Telegram lee el token una
  sola vez al arrancar») y el comando: `systemctl --user restart epsilon`, o
  cerrar y volver a ejecutar `uv run epsilon`. El aviso sale de comparar lo
  guardado en el `.env` con lo que el proceso tiene cargado: sobrevive a
  recargar la página y desaparece solo tras reiniciar.
- **Si algo falla al arrancar, se ve por qué** (consola y journal): una línea
  clara por canal con qué ajuste revisar (token rechazado, puerto ocupado...),
  en vez de un traceback suelto; y el mismo error queda visible en el estado
  de canales de la pantalla.
- **Primer arranque:** si falta la clave de Gemini, el chat muestra un aviso
  con enlace a la configuración.

## Seguridad

Es la pantalla más sensible: puede cambiar quién tiene permiso en Telegram.
Valen Host, Origin y JSON, y ninguna respuesta incluye secretos. Límite
conocido: cualquier programa local de la PC que hable con `localhost:8765`
también podría cambiarla, porque no hay login. Aceptado para uso personal.

## Pasos (cada uno con tests y commit)

1. **`config.py`:** lista de ajustes, validación, lectura de estado (sin
   valores secretos), escritura segura y cálculo de «pendiente de reinicio».
2. **API:** `GET /api/config`, `PUT /api/config` (cambios parciales) y
   `POST /api/config/test/{gemini|telegram}`.
3. **Estado de canales y errores claros al arrancar** en `main`; `llm.check()`
   y la prueba de Telegram.
4. **Página** `config.html` / `config.js`: campos enmascarados, aviso de
   reinicio con sus razones, estado de canales y aviso de primer arranque.
   Se revisa en el navegador.

## Fuera de alcance

Login, formularios para otros canales, cambiar `DATA_DIR`, elegir el modelo de
una lista, mostrar el gasto en esta pantalla (`/uso` ya existe).
