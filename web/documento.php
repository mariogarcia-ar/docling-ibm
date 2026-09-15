<?php

declare(strict_types=1);

/**
 * Ficha de un documento: el archivo a la izquierda, su extracción a la derecha.
 *
 * Uso: `documento.php?doc=chicos/243a8b81-….png`
 */

require_once __DIR__ . '/lib/catalogo.php';
require_once __DIR__ . '/lib/vista.php';

$catalogo = catalogo_listar();
$ref = isset($_GET['doc']) && is_string($_GET['doc']) ? $_GET['doc'] : '';

$documento = $catalogo['documentos'][$ref] ?? null;

if ($documento === null) {
    http_response_code(404);
    vista_cabecera('Documento no encontrado');
    ?>
    <div class="panel panel-vacio">
      <h2>No existe ese documento</h2>
      <p>La referencia <code><?= h($ref) ?></code> no corresponde a ningún archivo de
      <code>tests/fixtures</code>. Puede que el archivo se haya movido o renombrado.</p>
      <p><a href="index.php">← volver al listado</a></p>
    </div>
    <?php
    vista_pie();
    exit;
}

vista_cabecera($documento['nombre']);
vista_documento($documento);
vista_pie();
