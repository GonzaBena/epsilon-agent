# El prompt con el que arranca «Crear conversando»: abre un chat nuevo con IA con este texto escrito (sin enviar).
# La IA entrevista a la persona y termina proponiendo el SKILL.md completo en un bloque de código; la persona
# lo revisa y lo guarda con el botón «Guardar como skill». La IA no guarda nada.

STARTER_PROMPT = """Quiero crear una skill nueva para Epsilon: instrucciones mías para que hagas cierta tarea a mi manera. Hacéme preguntas, de a una por vez, para entender qué tarea es, cuándo se usa, qué formato o tono quiero y cómo me gusta que quede. Cuando tengas lo suficiente, proponeme la skill COMPLETA en UN solo bloque de código, con este formato exacto:

```markdown
---
name: nombre-en-minusculas
description: una frase que diga para qué sirve y cuándo usarla
---
Las instrucciones, paso a paso, en español.
```

El nombre será el comando (/nombre): en minúsculas, sin espacios, con guion si hace falta. No guardes nada: yo reviso el texto y lo guardo con el botón «Guardar como skill». Empezá con la primera pregunta."""
