# Idea: documentación de Epsilon

Estado: **idea guardada, sin construir**. Retomarla cuando exista una V1 de Epsilon y se decida
si se crea una web propia del proyecto.

## Punto de partida

Pensada a partir de la documentación de OpenClaw (<https://docs.openclaw.ai>), que tiene doce
áreas: Empezar, Instalar, Canales, Agentes, Capacidades, Plugins, Modelos, Plataformas, Operación,
Referencia, Versiones y Ayuda. Es grande porque OpenClaw es un producto grande; Epsilon necesita
solo una parte, a su escala.

Las preguntas que debería responder, según lo que se imaginó:

- ¿Cómo cambio el modelo de IA que uso?
- ¿Qué modelos de IA tengo disponibles?
- ¿Qué canales hay por defecto y cuáles puedo agregar?

## La división

Quien abre la documentación **dentro** del programa ya lo tiene instalado. Por eso se separa en dos:

| Dónde | Qué cubre |
| --- | --- |
| **Web del proyecto** (si se crea) | Empezar, instalar, actualizar, correrlo como servicio, versiones. Hoy vive en el `README.md`. |
| **Página Docs dentro de Epsilon** | Todo lo que se hace con el programa ya andando. |

### Temas de la página Docs (dentro de la app)

1. **Canales:** cuáles hay, cuáles se pueden prender y apagar, cómo se suma uno nuevo.
2. **Modelos:** cuál se usa hoy, cómo cambiarlo, cómo probar la conexión sin gastar tokens.
3. **Herramientas:** los comandos y su uso; además, voz y notas.
4. **Ayuda:** qué hacer si el bot no responde, dónde ver los logs, cómo reiniciar el servicio.
5. **Seguridad:** lo que hoy solo está en el README (solo 127.0.0.1, claves que no salen del servidor, etc.).

## Cómo se construiría (suposiciones, sin decidir)

- Una cuarta página en la barra de arriba (Chat · Canales · Uso · Docs), con el mismo estilo y un
  índice de temas a la izquierda.
- Lo que cambia con el uso sale de lo que Epsilon ya sabe, no de texto escrito a mano: comandos
  de `tools.py`, canales de `/api/channels`, modelo actual de la configuración. Así no se
  desactualiza cuando se agregue algo.
- Cada tema enlaza a la página donde se hace el cambio (por ejemplo, «cambiar el modelo» → `/config`).
- Lo que no se puede leer de ningún lado (cómo agregar un canal nuevo) es texto escrito aparte.

## Pregunta abierta

Hoy Epsilon solo guarda el nombre del modelo (`GEMINI_MODEL`) y no tiene una lista. «¿Qué modelos
tengo disponibles?» pide elegir entre dos caminos: consultar a Gemini la lista en vivo, o mostrar
unos pocos sugeridos escritos a mano. Falta decidir cuál.
