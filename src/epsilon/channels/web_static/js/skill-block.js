// Busca en el texto de una respuesta una skill propuesta: el primer bloque de código que empieza con un
// encabezado «---» que tiene `name:`. Devuelve el texto del SKILL.md o null. No toca la página: solo lee texto.
"use strict";

window.findSkillBlock = function findSkillBlock(text) {
  const match = /```[a-z]*\r?\n(---\r?\n[\s\S]*?\r?\n---[\s\S]*?)\r?\n```/.exec(String(text || ""));
  if (!match) return null;
  const head = /^---\r?\n([\s\S]*?)\r?\n---/.exec(match[1]);
  return head && /^\s*name\s*:/m.test(head[1]) ? match[1] : null;
};
