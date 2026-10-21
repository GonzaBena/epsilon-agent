// Fórmulas ($...$ y $$...$$) para markdown-it, dibujadas con KaTeX (js/vendor/katex.min.js).
//
// Salida MathML (sin HTML con estilos): la política de seguridad de la web no deja estilos en
// línea y así tampoco hacen falta hojas de estilo ni fuentes. trust:false apaga \href, \url,
// \includegraphics y similares; si una fórmula no se entiende, se muestra el texto tal cual.
"use strict";

function markdownMath(md, katex) {
  function render(source, display) {
    return katex.renderToString(source, { output: "mathml", displayMode: display, throwOnError: false, trust: false });
  }

  function math(state, silent) {
    const src = state.src;
    const start = state.pos;
    if (src.charCodeAt(start) !== 0x24) return false; // $
    const display = src.charCodeAt(start + 1) === 0x24;
    const open = display ? 2 : 1;

    // Un $ suelto (precios: «$5 y $10») no abre una fórmula: pegado a un espacio o a un dígito no cuenta.
    const first = src.charAt(start + open);
    if (!first || /\s/.test(first)) return false;

    let end = start + open;
    while ((end = src.indexOf("$", end)) !== -1) {
      if (src.charCodeAt(end - 1) === 0x5c) { end += 1; continue; } // \$ escapado
      if (display ? src.charCodeAt(end + 1) === 0x24 : true) break;
      end += 1;
    }
    if (end === -1) return false;
    const body = src.slice(start + open, end);
    if (!body.trim() || (!display && (/\s$/.test(body) || /\d/.test(src.charAt(end + 1))))) return false;

    if (!silent) {
      const token = state.push("html_inline", "", 0); // md.render lo escribe tal cual: lo genera KaTeX, no el modelo
      token.content = render(body, display);
    }
    state.pos = end + open;
    return true;
  }

  md.inline.ruler.before("escape", "math", math);
}

window.markdownMath = markdownMath;
if (typeof module !== "undefined") module.exports = markdownMath;
