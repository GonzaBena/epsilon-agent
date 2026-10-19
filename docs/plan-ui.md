# Plan: forma del mockup (paneles, menú, contraer)

Sigue a `docs/plan-web.md` y `docs/plan-config.md`. Objetivo: acercar la web al
mockup (header, menú lateral, paneles) sin dejar nada que no funcione.

## Decisiones

- **Solo lo que funciona.** Fuera: Memoria, Docs, Sesión, Resúmenes y el avatar.
  Dentro, con función real: Chat, Notas (`/ver`), Archivos (`/archivos`),
  Facturas y tickets (`/archivos tickets`), Uso (`/uso`) y Configuración.
- **Sin buscador por ahora.** El «Buscar» del mockup apunta a conversaciones,
  que todavía no existen; la búsqueda de notas ya está en `/buscar`.
- **Paneles que se contraen.** Izquierdo: columna de íconos (64px) con el «+»
  de Nuevo chat arriba, el menú, y el botón de abrir abajo. Derecho (paso 2):
  columna mínima con su botón de abrir. El estado se recuerda en el navegador.
  En ventanas angostas (<1000px) el izquierdo queda siempre como columna de íconos.
- **Accesibilidad:** los íconos conservan su nombre (la etiqueta sigue en la
  página, fuera de la vista); los botones de abrir/cerrar llevan `aria-expanded`.
- **Nuevo chat** vacía la conversación de este navegador (como el viejo «Limpiar
  chat»); más adelante crearía una guardada en el servidor.
- **Menú escrito en cada HTML** (sin plantillas); un test vigila que sea igual.
- **Comandos por enlace** (`/?cmd=...`): solo se ejecutan solos los de una lista
  de lecturas (`ALLOWED_COMMANDS` en `app.js`); nunca los que escriben o llaman
  al modelo.
- El preview de CSV sigue como texto; la tabla del mockup necesita filas
  estructuradas desde el núcleo (cambio aparte).

## Pasos (cada uno con commit y revisión en el navegador)

1. Header y panel izquierdo con su versión contraída. **Hecho.**
2. Panel derecho (Herramientas, Canales) con su versión contraída, píldoras de
   atajos sobre el cuadro de texto y la nota «puede equivocarse».
3. Pulido: color de las burbujas, botón de enviar cuadrado con ícono, estado vacío.

## Sin verificar

El comportamiento en ventana angosta (no se pudo achicar la ventana del navegador).
