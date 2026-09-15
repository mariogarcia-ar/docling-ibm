<?php

declare(strict_types=1);

/**
 * Sirve los bytes de un archivo del corpus.
 *
 * Existe por una razón concreta: el servidor embebido de PHP (`php -S -t web`)
 * solo sirve lo que está bajo su docroot, y `tests/fixtures` está fuera. Este
 * endpoint es la única puerta hacia esos bytes, y por eso valida de más en vez de
 * de menos — `?c=fixtures&ref=../../.env` no puede ser una lectura de archivos
 * arbitraria.
 *
 * Uso: `archivo.php?c=fixtures&ref=chicos/x.png`
 *      `archivo.php?c=extracciones&ref=chicos/x.extraccion.json`
 *
 * El JSON de extracción se sirve TAL CUAL (es lo que se descarga para inspeccionar
 * a mano); mostrar los campos es trabajo de `documento.php`, no de este endpoint.
 */

require_once __DIR__ . '/lib/catalogo.php';

/** Tipos por extensión. Lo desconocido se sirve como binario. */
const TIPOS = [
    'jpg' => 'image/jpeg',
    'jpeg' => 'image/jpeg',
    'png' => 'image/png',
    'pdf' => 'application/pdf',
    'json' => 'application/json; charset=utf-8',
];

/** Responde un error y termina. Texto plano: esto lo consume el navegador, no una persona. */
function responder_error(int $estado, string $mensaje): never
{
    http_response_code($estado);
    header('Content-Type: text/plain; charset=utf-8');
    header('X-Content-Type-Options: nosniff');
    echo $mensaje, "\n";
    exit;
}

$cual = isset($_GET['c']) && is_string($_GET['c']) ? $_GET['c'] : '';
$ref = isset($_GET['ref']) && is_string($_GET['ref']) ? $_GET['ref'] : '';

// `catalogo_resolver_ruta` rechaza `..`, rutas absolutas y todo lo que caiga
// fuera de la raíz. Cualquier fallo es un 404: no hace falta distinguir «no
// existe» de «no permitido», y decirlo sería filtrar información del disco.
$ruta = catalogo_resolver_ruta($cual, $ref);
if ($ruta === null) {
    responder_error(404, 'No existe o no es accesible.');
}

$tamano = (int) filesize($ruta);
$mtime = (int) filemtime($ruta);
$extension = strtolower(pathinfo($ruta, PATHINFO_EXTENSION));
$tipo = TIPOS[$extension] ?? 'application/octet-stream';

// ETag barato y suficiente: tamaño + mtime. El corpus es de solo lectura en la
// práctica (los fixtures están versionados), así que esto acierta casi siempre.
$etag = '"' . dechex($tamano) . '-' . dechex($mtime) . '"';

header('Content-Type: ' . $tipo);
header('Content-Length: ' . $tamano);
header('X-Content-Type-Options: nosniff');
// `no-cache` NO significa «no guardes»: significa «guardá, pero revalidá». Con el
// ETag de abajo, el navegador manda la condición y recibe un 304 sin cuerpo.
// Importa acá porque los `.extraccion.json` se REGENERAN (a diferencia de los
// fixtures): una copia sin revalidar mostraría una lectura vieja.
header('Cache-Control: no-cache');
header('Last-Modified: ' . gmdate('D, d M Y H:i:s', $mtime) . ' GMT');
header('ETag: ' . $etag);
// `inline` a propósito: la imagen y el PDF se muestran en la página. El nombre
// va entre comillas y sin rutas, que es lo que evita el header splitting.
header('Content-Disposition: inline; filename="' . rawurlencode(basename($ruta)) . '"');

$desde = $_SERVER['HTTP_IF_NONE_MATCH'] ?? null;
if (is_string($desde) && trim($desde) === $etag) {
    http_response_code(304);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'HEAD') {
    exit;
}

// Sin buffer: la lectura va directo al cliente.
while (ob_get_level() > 0) {
    ob_end_clean();
}
readfile($ruta);
