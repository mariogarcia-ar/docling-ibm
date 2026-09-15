/* Visor de fixtures — el único JavaScript de la app.
 *
 * Hace una sola cosa, y por eso no hay framework: el checkbox «Mostrar campos sin
 * dato» de cada extracción plegable. Todo lo demás (el listado, la tabla, las
 * banderas, el PDF) lo resuelve el servidor, así que funciona sin JS.
 *
 * No hay estado: el filtro es una consecuencia del DOM. Marcar la casilla
 * esconde las filas `tr.fila-no-leido` que están DENTRO del contenedor que
 * declara el `data-filtro-vacios`.
 */

(function () {
  "use strict";

  /** Aplica el filtro de un checkbox a su contenedor. */
  function aplicar(checkbox) {
    var id = checkbox.getAttribute("data-filtro-vacios");
    var contenedor = id ? document.querySelector(id) : null;
    if (!contenedor) return;

    // `checked` = mostrar también las filas sin dato.
    var mostrarVacios = checkbox.checked;
    var filas = contenedor.querySelectorAll("tr.fila-sin-dato");
    filas.forEach(function (fila) {
      fila.hidden = !mostrarVacios;
    });

    // Cuántos campos no tienen dato: sin este número, un documento con 20 campos
    // ausentes se ve igual que uno con 3. Se muestra siempre (marcado o no) — es
    // un dato de la lectura, no del filtro.
    var ocultas = filas.length;
    var etiqueta = checkbox.closest(".control");
    if (etiqueta) {
      var anterior = etiqueta.querySelector(".control-conteo");
      if (anterior) anterior.remove();
      if (ocultas > 0) {
        var span = document.createElement("span");
        span.className = "control-conteo";
        span.textContent = "(" + ocultas + " sin dato)";
        etiqueta.appendChild(span);
      }
    }
  }

  document.addEventListener("change", function (evento) {
    var objetivo = evento.target;
    if (objetivo instanceof HTMLInputElement && objetivo.hasAttribute("data-filtro-vacios")) {
      aplicar(objetivo);
    }
  });

  // Estado inicial: el HTML nace con la casilla marcada (se ven todos los
  // campos), así que solo hay que contar los ausentes.
  document.querySelectorAll("input[data-filtro-vacios]").forEach(aplicar);
})();
