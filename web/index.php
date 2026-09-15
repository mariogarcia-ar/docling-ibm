<?php

declare(strict_types=1);

/**
 * Listado: explora `tests/fixtures` y muestra, por documento, si tiene extracción.
 *
 * Uso: `php -S localhost:8080 -t web` y abrir http://localhost:8080
 */

require_once __DIR__ . '/lib/catalogo.php';
require_once __DIR__ . '/lib/vista.php';

vista_cabecera('Visor de fixtures');
vista_listado(catalogo_listar());
vista_pie();
