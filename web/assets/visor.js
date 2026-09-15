/* Visor de fixtures — el único JavaScript de la app.
 *
 * Hace dos cosas, y por eso no hay framework:
 *  1. El checkbox «Mostrar campos sin dato» de cada extracción plegable.
 *  2. Los atajos de teclado del navegador entre documentos.
 *
 * Todo lo demás (el listado, la tabla, las banderas, el PDF, la navegación misma)
 * lo resuelve el servidor, así que la app funciona sin JS. Acá solo se AGREGA.
 *
 * No hay estado: el filtro es una consecuencia del DOM y la navegación usa el
 * `href` que el servidor ya puso en el `<a>`.
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

  /**
   * Sincroniza `--alto-encabezado` con el alto REAL de la barra superior.
   *
   * La barra es `sticky` y el subtítulo se parte en dos líneas en ventanas
   * angostas, así que su alto cambia (medido: 52px → 86px alrededor de los 502px
   * de ancho). Todo lo que se pega debajo de ella —la cabecera de la tabla y la
   * columna del documento— fija su `top` con esa variable, y el `scroll-margin` de
   * los grupos del listado también.
   *
   * ⚠️ El valor NO se puede adivinar con una media query: el punto de quiebre
   * depende del renderizado de la fuente, no solo del ancho (un breakpoint fijo en
   * 701px estaba 200px corrido, y el desfase solo se veía en una franja angosta de
   * anchos). Se mide, y se vuelve a medir en cada resize.
   *
   * El estilo en línea gana sobre el `:root` del CSS, que queda como fallback
   * para cuando este script no corre.
   */
  function sincronizarAltoDelEncabezado() {
    var encabezado = document.querySelector(".encabezado");
    if (!encabezado) return;
    var alto = Math.round(encabezado.getBoundingClientRect().height);
    if (alto > 0) {
      document.documentElement.style.setProperty("--alto-encabezado", alto + "px");
    }
  }

  var encabezado = document.querySelector(".encabezado");
  sincronizarAltoDelEncabezado();
  // `ResizeObserver` en vez de `window.onresize`: observa el ELEMENTO, así que
  // también reacciona si el alto cambia sin que cambie la ventana (por ejemplo al
  // cargar una fuente que ensancha el subtítulo). Escribir la variable NO cambia el
  // alto del encabezado (solo el `top` de lo que se pega debajo), así que no hay
  // bucle de observación.
  if (encabezado && typeof ResizeObserver === "function") {
    new ResizeObserver(sincronizarAltoDelEncabezado).observe(encabezado);
  } else {
    window.addEventListener("resize", sincronizarAltoDelEncabezado);
  }

  /**
   * ¿El foco está en un control donde las flechas significan otra cosa?
   *
   * Sin esta guarda, moverse con `←`/`→` dentro del texto de una observación
   * cambiaría de documento y se perdería lo que se estaba leyendo.
   *
   * ⚠️ NO alcanza con preguntar si es un `<input>`: la casilla «Mostrar campos sin
   * dato» es un input y es el control que más se toca en esta pantalla, así que
   * bloquear por etiqueta dejaba las flechas muertas después de usarla (medido).
   * Un checkbox no consume las flechas; un campo de texto sí. Por eso se mira el
   * TIPO y solo se bloquea lo que realmente se está escribiendo.
   */
  var TIPOS_DE_TEXTO = [
    "text", "search", "url", "tel", "email", "password", "number",
    "date", "datetime-local", "month", "week", "time",
  ];

  function escribiendoEnUnCampo() {
    var activo = document.activeElement;
    if (!activo) return false;
    if (activo.isContentEditable) return true;
    var tag = activo.tagName;
    // Un `<select>` cambia de opción con las flechas; un `<textarea>` mueve el
    // cursor. Los dos tienen que quedar afuera del atajo.
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag !== "INPUT") return false;

    return TIPOS_DE_TEXTO.indexOf(activo.type) !== -1;
  }

  /**
   * Atajos del navegador: `←` y `→` van al documento anterior y siguiente.
   *
   * ⚠️ El botón NO se busca entre los `.nav-boton[href]`. Filtrar por `href` y
   * tomar el primero o el último da el botón equivocado en los extremos de la
   * lista: en el primer documento no hay «anterior», así que el único con `href`
   * es «siguiente» y `←` avanzaba en vez de quedarse quieto — lo contrario de lo
   * que la flecha significa. Cada tecla se ata a un botón por su POSICIÓN en la
   * barra (`[0]` anterior, `[1]` siguiente), esté habilitado o no, y así el
   * estado deshabilitado del servidor se respeta sin repetir la condición acá.
   *
   * Los destinos siguen saliendo del `href` que emitió el servidor: el atajo no
   * puede apuntar a un destino distinto del que ofrece la barra.
   */
  document.addEventListener("keydown", function (evento) {
    if (evento.key !== "ArrowLeft" && evento.key !== "ArrowRight") return;
    if (evento.metaKey || evento.ctrlKey || evento.altKey || evento.shiftKey) return;
    if (escribiendoEnUnCampo()) return;

    var botones = document.querySelectorAll(".navegador .nav-boton");
    var boton = botones[evento.key === "ArrowLeft" ? 0 : 1];
    // Un `<span aria-disabled>` en el extremo no tiene `href`: no hay destino.
    if (!boton || !boton.hasAttribute("href")) return;

    evento.preventDefault();
    boton.click();
  });
})();

