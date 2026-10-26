from epsilon.data import notes
from epsilon.skills import store

# Las skills que vienen con Epsilon: se copian UNA vez a DATA_DIR/skills/ (la marca `.seeded` evita repetirlo,
# así una que borrás no vuelve) y desde ahí son tuyas: se editan, se borran o sirven de modelo. Solo texto, sin
# herramientas: una skill no da permisos.

MARK = "skills/.seeded"

SEEDS = {
    "informe": """---
name: informe
description: Resume un informe semanal en pocas líneas: lo que se hizo, lo que sigue pendiente y los problemas.
---
Te paso un informe semanal (o notas sueltas de la semana). Resumilo así, en español y sin inventar nada que no esté en el texto:

1. **Hecho**: lo que se terminó, en viñetas cortas.
2. **Pendiente**: lo que quedó sin terminar o se viene, con la fecha o la persona si aparece.
3. **Problemas**: lo que bloquea o preocupa. Si no hay, escribí «Sin problemas».

Reglas:
- Máximo 10 líneas en total.
- Una línea por idea, sin repetir lo mismo con otras palabras.
- Si falta información para alguna sección, decilo en una frase en vez de rellenar.
- Al final, una sola línea con lo más importante de la semana.
- No uses herramientas: trabajá solo con el texto que te di.""",
    "correo": """---
name: correo
description: Redacta el borrador de un correo con el tono y el largo que pidas. Solo lo escribe: no lo envía.
---
Te pido que redactes el borrador de un correo. Mirá qué te dije y escribilo así:

- **Asunto**: una línea clara, sin mayúsculas de más.
- **Saludo** acorde al tono que pedí (si no dije nada: cordial y profesional).
- **Cuerpo**: lo que te pedí transmitir, en párrafos cortos. Si dije un largo (breve, medio, detallado), respetalo.
- **Cierre** con una frase de acción si corresponde («Quedo atento…», «¿Te viene bien el jueves?»).

Reglas:
- Escribí en español rioplatense, a menos que te pida otro idioma.
- No inventes datos: nombres, fechas, montos o compromisos que no te di. Si falta algo importante, dejá [completar] en ese lugar.
- Entregá solo el borrador, listo para copiar. No lo envíes ni digas que lo enviaste: no tenés cómo.
- Al final preguntá en una línea si quiero cambiar el tono o el largo.
- No uses herramientas.""",
    "prioridades": """---
name: prioridades
description: Ordena una lista de pendientes por prioridad y propone qué hacer primero. Solo propone el orden: no toca tus notas.
---
Te paso una lista de pendientes. Ordenala por prioridad y explicame brevemente por qué:

1. **Hacer hoy**: lo urgente (vence pronto o alguien está esperando) y lo importante.
2. **Esta semana**: importante pero sin apuro.
3. **Cuando haya tiempo**: lo que se puede postergar sin consecuencias.
4. **Soltar o delegar**: lo que no vale la pena hacer o puede hacer otra persona.

Reglas:
- Mantené el texto de cada tarea tal cual; solo cambiá el orden y el grupo.
- Dentro de cada grupo, lo primero que haría yo va arriba.
- Si no podés decidir porque falta un dato (fecha, quién espera), hacé una pregunta corta en vez de adivinar.
- Cerrá con una sola recomendación: cuál sería la primera tarea y por qué.
- Solo proponés el orden: no cambies, marques ni borres nada en mis notas ni en mi lista de tareas, y no uses herramientas.""",
}


def seed() -> list[str]:
    """Copia las skills de fábrica la primera vez. Devuelve las que copió. Nunca levanta: si no se puede
    escribir, Epsilon arranca igual (y lo intenta de nuevo la próxima vez)."""
    try:
        mark = notes.safe_path(MARK)
        if mark.exists():
            return []
        copied = []
        for name, text in SEEDS.items():
            if store.get(name) is None:
                store.save(name, text)
                copied.append(name)
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text("1", encoding="utf-8")
        return copied
    except Exception:  # noqa: BLE001 - es un adorno de la primera vez: no tumba el arranque
        return []
