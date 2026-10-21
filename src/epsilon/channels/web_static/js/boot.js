// Corre ANTES de que se dibuje la página (sin defer) y apaga las transiciones del esqueleto.
// Así, cuando shell.js aplica el estado inicial de los paneles (el que recordó el navegador, o
// el de una ventana angosta), el menú queda en su lugar de entrada en vez de verse contraerse.
// shell.js saca la clase cuando termina de arrancar.
"use strict";

document.documentElement.classList.add("no-motion");
