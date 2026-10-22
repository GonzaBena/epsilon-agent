# Planificador: rutinas que Epsilon hace solo

## Por qué
Hoy Epsilon solo actúa cuando le escribís. Con volumen, música y ventanas ya sabe y controla la PC; falta que pueda
actuar **sin que se lo pidas en ese momento**: «a las 23:00 bajá el volumen», «en 30 min avisame que saque la pizza»,
«avisame si el disco pasa del 90 %».

## Decisiones de la persona (22/10/2026)
- Avisos: **ambos** (escritorio y Telegram).
- Su PC es de **escritorio**: no hay condición de batería (`bateria<N` se descartó de la fase 3).
- Rutinas perdidas (la PC estaba apagada): **solo avisar**, nunca ejecutarlas tarde.

## Estado
- Fase 1 (núcleo de tiempo, `/rutina`, `/rutinas`, avisos por escritorio y Telegram, `ROUTINES_ENABLED`): **hecha**.
- Fase 2 (IA: grupo `rutinas` con `crear`, `ver` y `borrar`; la pregunta muestra la regla ya traducida): **hecha**.
- Fase 3 (condiciones `disco>N` y `ram>N`, disparo por cruce de umbral y reposo de 1 h): **hecha**.
- Fase 4 (página web `/rutinas`: lista, crear con la sintaxis de `/rutina`, pausar, borrar): **hecha**. Entra solo en el menú de la izquierda; la barra de arriba sigue con sus 4 entradas.

## Idea central
Una **rutina** es una regla corta y estructurada: *cuándo* + *qué*. La IA no corre sola nunca.

- **Crear**: sin IA con un comando de sintaxis fija, o con IA («poné el volumen en 20 todas las noches a las 23»):
  el modelo traduce a la regla estructurada y la persona **confirma la regla ya traducida** (lo que se va a ejecutar,
  no lo que dijo el modelo). Ejecutar una rutina no usa modelo: costo cero.
- **Ejecutar**: un hilo propio mira el reloj y llama a las MISMAS funciones que los comandos (`multimedia.volume`, etc.),
  por lo que heredan sus topes (volumen ≤ 100 %).
- **Avisar**: notificación de escritorio (`notify-send`, ya instalado) y/o mensaje de Telegram.

## Sintaxis (sin IA)
```
/rutina 23:00 | volumen 20                  todos los días a las 23:00
/rutina lun-vie 09:00 | avisar Standup      días de semana
/rutina en 30m | avisar sacar la pizza      una sola vez, dentro de 30 minutos
/rutina disco>90 | avisar poco espacio      condición (fase 3), se dispara al cruzar el umbral
/rutinas                                    lista numerada (próxima ejecución, última, estado)
/rutina borrar N   /rutina pausar N   /rutina seguir N
```

## Qué puede hacer una rutina (lista blanca fija)
`avisar <texto>` · `volumen <valor>` · `musica <acción>`.
No hay shell, ni comandos libres, ni notas, ni modelo. Ampliar la lista es una decisión explícita (una línea en código + test),
nunca algo que pueda pedir el texto de una regla.

## Piezas
Carpeta nueva `routines/` (capa propia, como pide CLAUDE.md):

| Archivo | Qué hace |
|---|---|
| `routines/rules.py` | Parsea y valida la sintaxis; calcula la próxima ejecución. Puro, sin hilos ni reloj propio. |
| `routines/store.py` | `DATA_DIR/routines.json` (nombre fijo, vía `safe_path`, escritura por archivo temporal), tope de 50 reglas, texto por `config.redact`. |
| `routines/engine.py` | El hilo: cada 30 s `tick(now)` → qué rutinas tocan → ejecuta → anota `DATA_DIR/routines.log`. Se frena con un `Event`. |
| `routines/notify.py` | Un punto único para avisar: `notify-send` + Telegram. Bloqueado en los tests, como `_run` de `multimedia`. |

Cambios en lo existente:
- `main.py`: prende y apaga el motor (no es un canal: no aparece en Canales; un fallo no tumba la web).
- `channels/telegram.py`: expone `notify(text)` (manda a `ALLOWED_USER_ID`, redactado) para el motor.
- `core.py` + `tools.py`: comandos `/rutina`, `/rutinas`; en fase 2, grupo de IA `rutinas`.
- `settings/config.py`: interruptor `ROUTINES_ENABLED` (sí/no), el freno general.

## Reglas de seguridad
- Solo la lista blanca; los argumentos se validan al **crear** y otra vez al **ejecutar**.
- Una rutina creada por la IA siempre pide confirmación (permiso `ask` fijo en `crear`); `ver` es `allow`, `borrar` es `ask`.
- Mínimo 1 minuto entre ejecuciones de la misma rutina; máximo 50 reglas; aviso de hasta 200 caracteres.
- Todo lo que ejecuta queda en `routines.log` (cuándo, qué, resultado). Los errores no se reintentan en bucle.
- `ROUTINES_ENABLED=no` frena todo sin borrar nada.
- Si la PC estaba apagada a la hora de una rutina: **no** se ejecuta tarde (bajar el volumen a las 23:00 a las 08:00 sería
  un susto); se avisa «me perdí X» al volver. Las de «una sola vez» vencidas también se avisan, no se ejecutan.
- Zona horaria y hora de `utils/clock.now()`; los tests la reemplazan.

## Fases (cada una con tests y commit propio)
1. **Núcleo de tiempo**: `rules`, `store`, `engine`, `notify`; acciones `avisar`/`volumen`/`musica`; `/rutina` y `/rutinas`; `ROUTINES_ENABLED`.
2. **IA**: grupo `rutinas` (`crear`, `ver`, `borrar`) con confirmación de la regla traducida.
3. **Condiciones**: `disco>N`, `ram>N` (sin batería: su PC es de escritorio), con disparo por cruce de umbral y reposo de 1 h para no repetir. Lecturas nuevas en `utils/sistema.py`.
4. **Web**: página `/rutinas` (lista, pausar, borrar) y la fila en el menú; la barra repetida en cada HTML tiene su test.

## Tests
Sin hilos ni reloj real: `tick(now)` es una función pura sobre reglas + estado; el reloj, `notify` y las acciones se reemplazan.
Casos: próxima ejecución (días, cambio de día, «en 30m»), rutina perdida, tope de reglas, sintaxis maliciosa
(`; rm -rf /`, `--help`, números enormes), una rutina que falla no frena a las demás, `ROUTINES_ENABLED=no`,
y el bloqueo en `conftest.py` de `notify` para que ningún test avise de verdad.

## Fuera de alcance (por ahora)
Que una rutina llame al modelo (gasto sin supervisión), ejecutar comandos de notas o de archivos, rutinas
encadenadas, y «ventana activa» como disparador. Si más adelante hace falta, es otra decisión aparte.
