<?php

declare(strict_types=1);

/**
 * Las vistas del visor. HTML plano, sin framework y sin build: el único JS es el
 * que hace falta para plegar cosas.
 *
 * Dos pantallas: el LISTADO (el explorador de la carpeta) y el DOCUMENTO (la
 * imagen/PDF a la izquierda y la extracción a la derecha). Lo que no se pudo
 * emparejar se muestra en el listado, no se esconde.
 */

require_once __DIR__ . '/presentacion.php';

/** Escapa texto para HTML. Todo lo que viene de un archivo pasa por acá. */
function h(mixed $valor): string
{
    return htmlspecialchars((string) $valor, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** URL de un archivo servido por `archivo.php`. */
function url_archivo(string $cual, string $ref): string
{
    return 'archivo.php?c=' . rawurlencode($cual) . '&ref=' . rawurlencode($ref);
}

/** URL de la ficha de un documento. */
function url_documento(string $ref): string
{
    return 'documento.php?doc=' . rawurlencode($ref);
}

/** Cabecera común de las dos pantallas. */
function vista_cabecera(string $titulo, bool $conNavegador = false): void
{
    ?>
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= h($titulo) ?></title>
<link rel="stylesheet" href="assets/visor.css">
</head>
<body<?= $conNavegador ? ' class="con-navegador"' : '' ?>>
<header class="encabezado">
  <a class="marca" href="index.php">visor de fixtures</a>
  <span class="subtitulo">tests/fixtures → tests/fixtures-extraction</span>
</header>
<main class="contenido">
    <?php
}

/** Pie común: cierra el layout y carga el JS. */
function vista_pie(): void
{
    ?>
</main>
<script src="assets/visor.js"></script>
</body>
</html>
    <?php
}

/**
 * Listado: un bloque por grupo, con una fila por documento.
 *
 * Los documentos se marcan según su cantidad de extracciones usando el mismo
 * código de color que el detalle — «sin extracción» es un estado visible, no una
 * fila que se lee igual que las demás.
 */
function vista_listado(array $catalogo): void
{
    ?>
    <section class="resumen">
      <?php foreach ($catalogo['totales'] as $clave => $valor): ?>
        <span class="dato"><b><?= h($valor) ?></b> <?= h(str_replace('_', ' ', $clave)) ?></span>
      <?php endforeach; ?>
    </section>

    <?php if ($catalogo['huerfanas'] !== []): ?>
    <section class="aviso">
      <h2><?= count($catalogo['huerfanas']) ?> extracción(es) sin documento</h2>
      <p class="nota">No se pudieron atar a ningún archivo de <code>tests/fixtures</code>. Se
      listan con el campo que se usó para buscarlas y por qué falló, en vez de omitirlas.</p>
      <table class="tabla tabla-huerfanas">
        <thead><tr><th>Extracción</th><th>Motivo</th><th>archivo_relativo</th><th>origen</th></tr></thead>
        <tbody>
        <?php foreach ($catalogo['huerfanas'] as $huerfana): ?>
          <tr>
            <td><?= h($huerfana['ref']) ?></td>
            <td><code><?= h($huerfana['motivo']) ?></code></td>
            <td><?= h($huerfana['archivo_relativo'] ?? '—') ?></td>
            <td class="ruta"><?= h($huerfana['origen'] ?? '—') ?></td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </section>
    <?php endif; ?>

    <?php foreach ($catalogo['grupos'] as $grupo => $documentos): ?>
    <section class="grupo">
      <h2><?= h($grupo) ?> <span class="conteo"><?= count($documentos) ?></span></h2>
      <table class="tabla">
        <thead>
          <tr><th>Documento</th><th class="col-tipo">Tipo</th><th class="col-bytes">Tamaño</th>
          <th class="col-extraccion">Extracción</th><th>Lectura</th></tr>
        </thead>
        <tbody>
        <?php foreach ($documentos as $documento): ?>
          <?php
            $cantidad = count($documento['extracciones']);
            $estado = $cantidad === 0 ? 'sin-extraccion' : ($cantidad > 1 ? 'multipagina' : 'con-extraccion');
            $primera = $documento['extracciones'][0] ?? null;
          ?>
          <tr class="fila-<?= h($estado) ?>">
            <td>
              <a href="<?= h(url_documento($documento['ref'])) ?>"><?= h($documento['nombre']) ?></a>
            </td>
            <td class="col-tipo"><?= h(strtoupper($documento['extension'])) ?></td>
            <td class="col-bytes"><?= h(presentacion_bytes($documento['bytes'])) ?></td>
            <td class="col-extraccion">
              <?php if ($cantidad === 0): ?>
                <span class="pastilla pastilla-vacia">sin extracción</span>
              <?php elseif ($cantidad === 1): ?>
                <span class="pastilla">1</span>
              <?php else: ?>
                <span class="pastilla pastilla-media"><?= h($cantidad) ?> páginas</span>
              <?php endif; ?>
            </td>
            <td class="celda-resumen">
              <?= $primera === null ? '<span class="vacio">—</span>' : h(presentacion_resumen($primera['campos'])) ?>
            </td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </section>
    <?php endforeach; ?>
    <?php
}

/**
 * Inserta el documento: imagen o PDF, según la extensión.
 *
 * El PDF va en un `<iframe>`: el visor nativo del navegador ya sabe paginarlo y
 * hacer zoom, y embeberlo evita sumar una librería de PDF que habría que
 * mantener. Un PDF que el navegador no muestre sigue siendo descargable por el
 * enlace de al lado.
 */
function vista_medio(array $documento): void
{
    $url = url_archivo('fixtures', $documento['ref']);
    ?>
    <figure class="medio">
      <?php if ($documento['extension'] === 'pdf'): ?>
        <iframe class="medio-pdf" src="<?= h($url) ?>#view=FitH" title="<?= h($documento['nombre']) ?>"></iframe>
      <?php else: ?>
        <a href="<?= h($url) ?>" target="_blank" rel="noopener">
          <img src="<?= h($url) ?>" alt="<?= h($documento['nombre']) ?>" loading="lazy">
        </a>
      <?php endif; ?>
      <figcaption>
        <a href="<?= h($url) ?>" target="_blank" rel="noopener"><?= h($documento['nombre']) ?></a>
        <span class="sep">·</span><?= h(strtoupper($documento['extension'])) ?>
        <span class="sep">·</span><?= h(presentacion_bytes($documento['bytes'])) ?>
      </figcaption>
    </figure>
    <?php
}

/** Las banderas de una extracción, en fila y arriba de todo. */
function vista_banderas(array $campos): void
{
    $ordenados = presentacion_ordenar_campos($campos);
    $banderas = array_intersect_key($ordenados, array_flip(presentacion_banderas()));
    ?>
    <ul class="banderas">
      <?php foreach ($banderas as $clave => [$etiqueta, $tipo, $valor]): ?>
        <li class="<?= h(presentacion_clase($valor, $tipo)) ?>">
          <span class="bandera-etiqueta"><?= h($etiqueta) ?></span>
          <span class="bandera-valor"><?= h(presentacion_valor($valor, $tipo)) ?></span>
        </li>
      <?php endforeach; ?>
    </ul>
    <?php
}

/** La tabla de campos de una extracción, con su procedencia. */
function vista_campos(array $extraccion): void
{
    $ordenados = presentacion_ordenar_campos($extraccion['campos']);
    ?>
    <div class="tabla-scroll">
      <table class="tabla tabla-campos">
        <thead>
          <tr><th>Campo</th><th>Valor</th><th class="col-clave">Clave del JSON</th></tr>
        </thead>
        <tbody>
        <?php foreach ($ordenados as $clave => [$etiqueta, $tipo, $valor]): ?>
          <?php
            // Las dos clases se escriben juntas y completas a propósito: el
            // filtro de JS busca `tr.fila-sin-dato` y un nombre abreviado acá
            // haría que el checkbox no filtrara nada, en silencio.
            $leido = presentacion_leido($valor, $tipo);
            $claseFila = $leido ? 'fila-con-dato' : 'fila-sin-dato';
            if ($tipo === 'largo' && $leido) {
                $claseFila .= ' fila-larga';
            }
          ?>
          <tr class="fila-campo <?= h($claseFila) ?>">
            <th scope="row"><?= h($etiqueta) ?></th>
            <td class="<?= h(presentacion_clase($valor, $tipo)) ?>">
              <?php if ($tipo === 'largo' && $leido): ?>
                <p><?= nl2br(h((string) $valor)) ?></p>
              <?php elseif (is_array($valor) && $valor !== []): ?>
                <ul class="lista-valores">
                  <?php foreach ($valor as $item): ?><li><?= h($item) ?></li><?php endforeach; ?>
                </ul>
              <?php else: ?>
                <?= h(presentacion_valor($valor, $tipo)) ?>
              <?php endif; ?>
            </td>
            <td class="col-clave"><code><?= h($clave) ?></code></td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <?php
}

/** La procedencia de una extracción: quién, con qué prompt, cuánto costó. */
function vista_procedencia(array $extraccion): void
{
    $uso = $extraccion['uso'];
    $imagen = $extraccion['imagen'];
    $aritmetica = $extraccion['aritmetica'];
    ?>
    <dl class="procedencia">
      <div><dt>Modelo</dt><dd><?= h($extraccion['modelo'] ?? '—') ?></dd></div>
      <div><dt>Prompt</dt><dd><?= h($extraccion['version_prompt'] ?? '—') ?></dd></div>
      <div><dt>Procesado</dt><dd><?= h(presentacion_fecha($extraccion['procesado_utc'])) ?></dd></div>
      <div><dt>Costo</dt><dd><?= $extraccion['costo_usd'] === null ? '—' : 'US$ ' . h(number_format((float) $extraccion['costo_usd'], 5, ',', '.')) ?></dd></div>
      <?php if (presentacion_leido($uso['total_tokens'] ?? null)): ?>
        <div><dt>Tokens</dt><dd><?= h(presentacion_numero((float) $uso['total_tokens'])) ?></dd></div>
      <?php endif; ?>
      <?php if (presentacion_leido($imagen['dimensiones'] ?? null)): ?>
        <div><dt>Dimensiones</dt><dd><?= h(implode(' × ', (array) $imagen['dimensiones'])) ?></dd></div>
      <?php endif; ?>
      <?php if ($extraccion['pagina'] !== null): ?>
        <div><dt>Página</dt><dd><?= h($extraccion['pagina']) ?></dd></div>
      <?php endif; ?>
      <?php if (is_array($aritmetica) && ($aritmetica['calculable'] ?? false)): ?>
        <div>
          <dt>Aritmética (en código)</dt>
          <dd>
            <?= h(presentacion_numero((float) ($aritmetica['suma'] ?? 0))) ?>
            vs <?= h(presentacion_numero((float) ($aritmetica['total'] ?? 0))) ?>
            <?php if (($aritmetica['diferencia'] ?? null) !== null): ?>
              · dif. <?= h(presentacion_numero((float) $aritmetica['diferencia'])) ?>
            <?php endif; ?>
          </dd>
        </div>
      <?php endif; ?>
    </dl>
    <?php if ($extraccion['avisos_esquema'] !== []): ?>
      <details class="plegable plegable-aviso">
        <summary>Avisos de esquema (<?= count($extraccion['avisos_esquema']) ?>)</summary>
        <ul class="lista-valores">
          <?php foreach ($extraccion['avisos_esquema'] as $aviso): ?><li><?= h(is_scalar($aviso) ? $aviso : json_encode($aviso, JSON_UNESCAPED_UNICODE)) ?></li><?php endforeach; ?>
        </ul>
      </details>
    <?php endif; ?>
    <?php
}

/** Una extracción completa (banderas + campos + procedencia + JSON crudo). */
function vista_extraccion(array $extraccion, string $sufijoId): void
{
    ?>
    <article class="extraccion">
      <div class="extraccion-encabezado">
        <h2>
          <a href="<?= h(url_archivo('extracciones', $extraccion['ref'])) ?>" target="_blank" rel="noopener">
            <?= h($extraccion['nombre']) ?>
          </a>
        </h2>
        <label class="control">
          <input type="checkbox" checked
                 data-filtro-vacios="#<?= h($sufijoId) ?>">
          Mostrar campos sin dato
        </label>
      </div>

      <?php vista_banderas($extraccion['campos']); ?>

      <div id="<?= h($sufijoId) ?>">
        <?php vista_campos($extraccion); ?>
      </div>

      <?php vista_procedencia($extraccion); ?>

      <details class="plegable">
        <summary>JSON crudo</summary>
        <pre class="json"><code><?= h(json_encode($extraccion['crudo'], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)) ?></code></pre>
      </details>
    </article>
    <?php
}

/**
 * Barra de navegación entre documentos.
 *
 * Se emite dentro de `<main>`, pero el CSS la saca del flujo (`position: fixed`),
 * así que su lugar en el documento no afecta al layout: no hay que envolver las
 * vistas en un contenedor nuevo para colocarla.
 *
 * Los destinos van como `<a>` y no como `<button>` con JS: así el recorrido
 * funciona sin JavaScript, se puede abrir en otra pestaña y el navegador muestra
 * el destino en la barra de estado. El JS solo AGREGA los atajos de teclado, y
 * los lee del `href` de estos botones — de modo que el atajo no puede apuntar a
 * un destino distinto del que ofrece la barra.
 *
 * En los extremos el botón se deshabilita (`aria-disabled` + sin `href`) en vez
 * de envolver al otro lado: un «anterior» en el primero que salte al último
 * rompe la lectura de dónde estás, y el atajo de teclado hereda esa decisión
 * sin repetirla.
 */
function vista_navegador(array $catalogo, string $ref): void
{
    // Sin secuencia no hay navegación: es el caso de una llamada con el catálogo
    // vacío (por ejemplo una vista armada a mano). Mostrar «0 / 0» sería peor que
    // no mostrar la barra.
    if (($catalogo['secuencia'] ?? []) === []) {
        return;
    }

    $vecinos = catalogo_vecinos($catalogo, $ref);
    $anterior = $vecinos['anterior'];
    $siguiente = $vecinos['siguiente'];

    /** Botón de navegación: `<a>` si hay destino, `<span>` deshabilitado si no. */
    $boton = static function (string $cual, ?string $destino) use ($catalogo): void {
        $flecha = $cual === 'anterior' ? '←' : '→';
        $etiqueta = $flecha . ' ' . $cual;
        if ($destino === null) {
            ?>
            <span class="nav-boton" aria-disabled="true"><?= h($etiqueta) ?></span>
            <?php
            return;
        }
        $nombre = $catalogo['documentos'][$destino]['nombre'] ?? $destino;
        ?>
        <a class="nav-boton" href="<?= h(url_documento($destino)) ?>" title="<?= h($nombre) ?>">
          <?= h($etiqueta) ?>
        </a>
        <?php
    };

    /** El nombre del destino, o el extremo de la lista. */
    $destino = static function (?string $ref) use ($catalogo): string {
        if ($ref === null) {
            return '';
        }

        return $catalogo['documentos'][$ref]['nombre'] ?? $ref;
    };
    ?>
    <nav class="navegador" aria-label="Recorrer documentos">
      <?php $boton('anterior', $anterior); ?>
      <span class="nav-destino">
        <?= $anterior === null ? 'principio de la lista' : h($destino($anterior)) ?>
      </span>

      <span class="nav-posicion">
        <?= h($vecinos['indice']) ?> / <?= h($vecinos['total']) ?>
        <span class="sep">·</span><kbd>←</kbd> <kbd>→</kbd>
      </span>

      <span class="nav-destino alineado-derecha">
        <?= $siguiente === null ? 'fin de la lista' : h($destino($siguiente)) ?>
      </span>
      <?php $boton('siguiente', $siguiente); ?>
    </nav>
    <?php
}

/**
 * Detalle de un documento.
 *
 * Una sola extracción: pantalla partida, documento a la izquierda y extracción a
 * la derecha.
 *
 * Varias extracciones (un PDF multipágina pagó una por página): el documento se
 * muestra UNA vez en una columna que queda fija al hacer scroll, y las
 * extracciones se apilan al lado. No se repite el medio por cada página — un PDF
 * grande incrustado N veces es N veces la descarga — y el visor nativo del PDF
 * ya permite saltar a la página que cada bloque declara.
 */
function vista_documento(array $documento, array $catalogo = []): void
{
    $cantidad = count($documento['extracciones']);
    ?>
    <nav class="migas">
      <a href="index.php">← listado</a>
      <span class="sep">/</span>
      <a href="index.php#<?= h($documento['grupo']) ?>"><?= h($documento['grupo']) ?></a>
      <span class="sep">/</span>
      <span class="actual"><?= h($documento['nombre']) ?></span>
      <?php if ($cantidad > 1): ?>
        <span class="pastilla pastilla-media"><?= h($cantidad) ?> extracciones</span>
      <?php endif; ?>
    </nav>

    <?php if ($cantidad === 0): ?>
      <div class="pantalla-partida">
        <?php vista_medio($documento); ?>
        <div class="panel panel-vacio">
          <h2>Sin extracción</h2>
          <p>No hay ningún <code>.extraccion.json</code> en
          <code>tests/fixtures-extraction/<?= h($documento['grupo']) ?></code> que apunte a este
          archivo. Para generarla:</p>
          <pre class="comando"><code>voucherflow-lab tests/fixtures/<?= h($documento['grupo']) ?> \
  -M extraer -p deepseek --workers 4 \
  -o tests/fixtures-extraction/<?= h($documento['grupo']) ?></code></pre>
        </div>
      </div>
      <?php
      // El navegador se emite ANTES de cortar: este es justamente el caso donde
      // más se salta al siguiente documento, y un `return` seco lo dejaba sin
      // barra (el bug que este orden evita).
      if ($catalogo !== []) {
          vista_navegador($catalogo, $documento['ref']);
      }
      return;
      ?>
    <?php endif; ?>

    <div class="pantalla-partida<?= $cantidad > 1 ? ' pantalla-apilada' : '' ?>">
      <div class="columna-medio">
        <?php vista_medio($documento); ?>
      </div>
      <div class="columna-extracciones">
        <?php foreach ($documento['extracciones'] as $indice => $extraccion): ?>
          <?php vista_extraccion($extraccion, 'campos-' . $indice); ?>
        <?php endforeach; ?>
      </div>
    </div>

    <?php if ($catalogo !== []) { vista_navegador($catalogo, $documento['ref']); } ?>
    <?php
}
