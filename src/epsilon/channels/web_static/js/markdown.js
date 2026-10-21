// Markdown de las respuestas del bot. Usa markdown-it (js/vendor/), que devuelve HTML en texto;
// para no tocar innerHTML ese HTML se parsea con DOMParser (un documento aparte, inerte: no corre
// scripts ni carga nada) y se pasan sus nodos a la página.
//
// Seguridad: html:false escapa cualquier etiqueta cruda que escriba el modelo (puede estar
// influido por el contenido de una imagen o un PDF) y markdown-it ya descarta enlaces
// javascript:, vbscript: y data:. Las imágenes se apagan (no hay que pedir nada afuera).
"use strict";

const md = window.markdownit
  ? window.markdownit({ html: false, linkify: false, breaks: true }).disable("image")
  : null;
if (md && window.katex && window.markdownMath) md.use((m) => window.markdownMath(m, window.katex));

// Devuelve un fragmento con el texto ya dibujado. Si la librería no cargó, el texto plano.
window.renderMarkdown = function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  if (!md) {
    fragment.append(text);
    return fragment;
  }
  const doc = new DOMParser().parseFromString(md.render(text), "text/html");
  for (const link of doc.body.querySelectorAll("a")) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  fragment.append(...document.importNode(doc.body, true).childNodes);
  return fragment;
};
