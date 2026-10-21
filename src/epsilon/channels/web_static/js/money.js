// Dólares para mostrar, igual en el chat (barra de costo) y en la página Uso.
// Una llamada chica cuesta fracciones de centavo (un «hola» ronda US$ 0,00003): con 4 decimales fijos
// se vería como 0,0000 aunque el costo exista. Por eso, bajo 1 dólar, se agregan decimales hasta
// que se vean 3 cifras (con un tope).
"use strict";

window.formatUsd = function formatUsd(usd) {
  let digits = 2;
  if (usd < 1) {
    digits = 4;
    if (usd > 0) digits = Math.min(8, Math.max(4, Math.ceil(-Math.log10(usd)) + 2));
  }
  return `US$ ${usd.toLocaleString("es-AR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
};
