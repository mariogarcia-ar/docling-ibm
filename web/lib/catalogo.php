<?php

declare(strict_types=1);

/**
 * Catálogo del visor: recorre `tests/fixtures` y empareja cada documento con su
 * extracción de `tests/fixtures-extraction`.
 *
 * El emparejamiento NO puede ser «mismo nombre con otro sufijo». Medido sobre el
 * corpus real (95 extracciones / 94 documentos), el nombre del JSON no coincide
 * con el del documento en 53 casos. Las tres causas, las tres reales:
 *
 *  1. `archivo_relativo` cambia de forma según de dónde salió la corrida: para
 *     `otros/` y `negativos/` viene con la carpeta (`negativos/x.jpg`) y para el
 *     resto viene con el basename pelado (`x.jpg`). Un emparejador que asuma una
 *     sola de las dos formas pierde la mitad.
 *  2. Un PDF multipágina paga **una extracción por página** y el laboratorio las
 *     nombra `x_p01.extraccion.json` / `x_p02…`. El `_pNN` es de la PÁGINA, no
 *     del archivo, y el `origen` de esas extracciones apunta a un render
 *     temporal (`lab_pdf_<hash>/x_p01.jpg`) que ya no existe: despojar el sufijo
 *     es la única forma de atarlas a su PDF.
 *  3. Hay basenames repetidos en grupos distintos (`a6d79e19….png` está en
 *     `chicos/` y en `golden/`), así que el basename solo no alcanza: el grupo
 *     de la carpeta de la extracción desempata.
 *
 * El orden de los intentos importa: primero la ruta completa (no admite
 * ambigüedad), después el basename, y el `_pNN` como último recurso.
 *
 * Todo lo que no se puede atar se DEVUELVE como huérfano en vez de esconderse:
 * una extracción sin documento o un documento sin extracción son datos, no ruido.
 *
 * ⚠️ Los docblocks de este archivo NO pueden contener una barra seguida de
 * asterisco (cierra el comentario y el archivo deja de parsear). Pasó al
 * escribirlo, citando un glob de un temporal de PDF: el `php -l` lo destapó.
 */

/** Raíz del repo, derivada de la ubicación de este archivo (`web/lib/`). */
function catalogo_raiz(): string
{
    return dirname(__DIR__, 2);
}

/** Extensiones que el visor sabe mostrar. */
function catalogo_extensiones(): array
{
    return ['jpg', 'jpeg', 'png', 'pdf'];
}

/**
 * Sufijo de página que agrega el laboratorio a los documentos multipágina.
 *
 * `_p01`, `_p001`… El piso de 2 dígitos es deliberado: un archivo cuyo nombre
 * legítimamente termine en `_p1` no se toca.
 */
function catalogo_sufijo_pagina(): string
{
    return '/_p\d{2,}$/';
}

/** Ruta de una raíz del visor, verificada. */
function catalogo_raiz_datos(string $cual): ?string
{
    $raices = [
        'fixtures' => catalogo_raiz() . '/tests/fixtures',
        'extracciones' => catalogo_raiz() . '/tests/fixtures-extraction',
    ];
    $ruta = $raices[$cual] ?? null;
    if ($ruta === null) {
        return null;
    }
    $real = realpath($ruta);

    return $real === false ? null : $real;
}

/**
 * ¿La ruta está dentro de la raíz?
 *
 * Se compara con el separador final para que `/datos-otro` no pase como si
 * estuviera dentro de `/datos`.
 */
function catalogo_esta_dentro(string $ruta, string $raiz): bool
{
    return str_starts_with($ruta, rtrim($raiz, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR);
}

/**
 * Resuelve una referencia relativa (`grupo/archivo.jpg`) a una ruta real.
 *
 * Devuelve `null` ante cualquier duda: referencias con `..`, absolutas o que
 * caigan fuera de la raíz. `archivo.php` sirve bytes con esto, así que el
 * rechazo tiene que estar acá y no en la vista.
 */
function catalogo_resolver_ruta(string $cual, string $ref): ?string
{
    $raiz = catalogo_raiz_datos($cual);
    if ($raiz === null || $ref === '' || str_contains($ref, "\0")) {
        return null;
    }
    // Windows y POSIX: normalizar antes de decidir.
    $ref = str_replace('\\', '/', $ref);
    if (str_starts_with($ref, '/') || preg_match('#(^|/)\.\.(/|$)#', $ref) === 1) {
        return null;
    }
    $real = realpath($raiz . '/' . $ref);
    if ($real === false || !is_file($real) || !catalogo_esta_dentro($real, $raiz)) {
        return null;
    }

    return $real;
}

/** Lista los archivos de una raíz con las extensiones pedidas, por ruta relativa. */
function catalogo_archivos(string $raiz, array $extensiones): array
{
    if (!is_dir($raiz)) {
        return [];
    }
    $encontrados = [];
    $it = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($raiz, FilesystemIterator::SKIP_DOTS)
    );
    foreach ($it as $archivo) {
        if (!$archivo->isFile()) {
            continue;
        }
        if (!in_array(strtolower($archivo->getExtension()), $extensiones, true)) {
            continue;
        }
        $rel = substr($archivo->getPathname(), strlen($raiz) + 1);
        $encontrados[str_replace('\\', '/', $rel)] = $archivo->getPathname();
    }
    ksort($encontrados);

    return $encontrados;
}

/**
 * Índice de documentos para resolver un nombre a UNA referencia.
 *
 * Tres vistas del mismo conjunto, de más precisa a menos:
 *  - `ruta`: la ruta relativa completa, sin ambigüedad posible.
 *  - `base`: el basename tal cual.
 *  - `sin_pagina`: el basename sin el `_pNN` del stem.
 */
function catalogo_indice_documentos(string $raiz): array
{
    $indice = ['ruta' => [], 'base' => [], 'sin_pagina' => []];
    foreach (array_keys(catalogo_archivos($raiz, catalogo_extensiones())) as $rel) {
        $indice['ruta'][$rel] = $rel;
        $base = basename($rel);
        $indice['base'][$base][] = $rel;
        $ext = pathinfo($base, PATHINFO_EXTENSION);
        $stem = pathinfo($base, PATHINFO_FILENAME);
        $clave = preg_replace(catalogo_sufijo_pagina(), '', $stem) . '.' . strtolower($ext);
        $indice['sin_pagina'][$clave][] = $rel;
    }

    return $indice;
}

/**
 * Elige UNA referencia entre las candidatas de un intento.
 *
 * Si hay más de una, desempata el grupo de la carpeta de la extracción (el caso
 * `a6d79e19….png` de `chicos/` y `golden/`). Si el desempate tampoco es
 * concluyente, devuelve `null` en vez de elegir al azar: un emparejamiento
 * inventado es peor que uno declarado como no resuelto.
 */
function catalogo_elegir(array $candidatas, string $grupo): ?string
{
    $candidatas = array_values(array_unique($candidatas));
    if (count($candidatas) === 1) {
        return $candidatas[0];
    }
    $mismo_grupo = array_values(array_filter(
        $candidatas,
        static fn (string $r): bool => str_starts_with($r, $grupo . '/')
    ));

    return count($mismo_grupo) === 1 ? $mismo_grupo[0] : null;
}

/**
 * Empareja una extracción con su documento.
 *
 * Devuelve `[ref_documento|null, motivo]`. El motivo explica por qué intento se
 * resolvió, para que la vista pueda mostrarlo (y para que un cambio futuro en el
 * laboratorio se vea como un motivo distinto en vez de como un silencio).
 */
function catalogo_emparejar(array $indice, array $extraccion, string $grupo): array
{
    $relativo = (string) ($extraccion['archivo_relativo'] ?? '');
    $origen = (string) ($extraccion['origen'] ?? '');

    // 1. `origen` con la ruta del repo: la forma más precisa cuando está.
    $prefijo = 'tests/fixtures/';
    if (str_starts_with($origen, $prefijo)) {
        $resto = substr($origen, strlen($prefijo));
        if (isset($indice['ruta'][$resto])) {
            return [$indice['ruta'][$resto], 'origen'];
        }
    }

    // 2. `archivo_relativo` que ya trae carpeta (`negativos/x.jpg`), o con el
    //    grupo antepuesto si la corrida lo guardó pelado.
    foreach ([$relativo, $grupo . '/' . $relativo] as $candidata) {
        if ($candidata !== '/' && isset($indice['ruta'][$candidata])) {
            return [$indice['ruta'][$candidata], 'archivo_relativo'];
        }
    }

    // 3. El basename, tal cual y sin el `_pNN` de página.
    $base = basename($relativo !== '' ? $relativo : $origen);
    if ($base !== '') {
        foreach ([$base, basename($origen)] as $clave) {
            if ($clave === '' || !isset($indice['base'][$clave])) {
                continue;
            }
            $elegida = catalogo_elegir($indice['base'][$clave], $grupo);
            if ($elegida !== null) {
                return [$elegida, 'basename'];
            }
        }
        $ext = pathinfo($base, PATHINFO_EXTENSION);
        $stem = pathinfo($base, PATHINFO_FILENAME);
        $clave = preg_replace(catalogo_sufijo_pagina(), '', $stem) . '.' . strtolower($ext);
        if (isset($indice['sin_pagina'][$clave])) {
            $elegida = catalogo_elegir($indice['sin_pagina'][$clave], $grupo);
            if ($elegida !== null) {
                return [$elegida, 'pagina_sin_sufijo'];
            }
        }
    }

    return [null, 'sin_emparejar'];
}

/** Lee un JSON de extracción, o `null` si el archivo no es legible. */
function catalogo_leer_extraccion(string $ruta): ?array
{
    $texto = @file_get_contents($ruta);
    if ($texto === false) {
        return null;
    }
    $datos = json_decode($texto, true);

    return is_array($datos) ? $datos : null;
}

/**
 * Normaliza una extracción a lo que la vista necesita.
 *
 * `resultado` y `extraccion` son el mismo contenido en el laboratorio (una es la
 * lectura y la otra la lectura consolidada); se prefiere `extraccion` porque es
 * la consolidada, y se cae a `resultado` para no depender de cuál vino.
 */
function catalogo_normalizar_extraccion(string $ref, array $datos): array
{
    $campos = $datos['extraccion'] ?? null;
    if (!is_array($campos)) {
        $campos = is_array($datos['resultado'] ?? null) ? $datos['resultado'] : [];
    }

    return [
        'ref' => $ref,
        'nombre' => basename($ref),
        'modelo' => $datos['modelo'] ?? null,
        'modo' => $datos['modo'] ?? null,
        'fuente' => $datos['fuente'] ?? null,
        'version_prompt' => $datos['version_prompt'] ?? null,
        'procesado_utc' => $datos['procesado_utc'] ?? null,
        'costo_usd' => $datos['costo_usd'] ?? null,
        'uso' => is_array($datos['uso'] ?? null) ? $datos['uso'] : [],
        'imagen' => is_array($datos['imagen'] ?? null) ? $datos['imagen'] : [],
        'pagina' => isset($datos['pagina']) ? (int) $datos['pagina'] : null,
        'esquema_validado' => $datos['esquema_validado'] ?? null,
        'reintentos_esquema' => $datos['reintentos_esquema'] ?? null,
        'avisos_esquema' => is_array($datos['avisos_esquema'] ?? null)
            ? $datos['avisos_esquema']
            : [],
        'aritmetica' => is_array($datos['aritmetica'] ?? null) ? $datos['aritmetica'] : [],
        'campos' => $campos,
        'crudo' => $datos,
    ];
}

/**
 * Catálogo completo: grupos → documentos → extracciones, más lo que no cerró.
 *
 * La clave de un documento es su ruta relativa a `tests/fixtures` (p. ej.
 * `chicos/x.jpg`), que es estable y ya viene saneada para usar en una URL.
 */
function catalogo_listar(): array
{
    $raizDocumentos = catalogo_raiz_datos('fixtures');
    $raizExtracciones = catalogo_raiz_datos('extracciones');
    $vacio = [
        'ok' => false,
        'grupos' => [],
        'huerfanas' => [],
        'documentos' => [],
        'totales' => ['documentos' => 0, 'extracciones' => 0, 'con_extraccion' => 0,
            'sin_extraccion' => 0, 'multipagina' => 0],
    ];
    if ($raizDocumentos === null || $raizExtracciones === null) {
        $vacio['error'] = 'No se encontraron las carpetas tests/fixtures y tests/fixtures-extraction.';
        return $vacio;
    }

    $indice = catalogo_indice_documentos($raizDocumentos);
    $documentos = [];
    foreach (array_keys($indice['ruta']) as $rel) {
        $grupo = str_contains($rel, '/') ? explode('/', $rel)[0] : '(raíz)';
        $documentos[$rel] = [
            'ref' => $rel,
            'grupo' => $grupo,
            'nombre' => basename($rel),
            'extension' => strtolower(pathinfo($rel, PATHINFO_EXTENSION)),
            'bytes' => (int) @filesize($raizDocumentos . '/' . $rel),
            'extracciones' => [],
        ];
    }

    $huerfanas = [];
    $totalExtracciones = 0;
    foreach (catalogo_archivos($raizExtracciones, ['json']) as $rel => $absoluto) {
        if (!str_ends_with($rel, '.extraccion.json')) {
            continue;
        }
        $datos = catalogo_leer_extraccion($absoluto);
        if ($datos === null) {
            $huerfanas[] = ['ref' => $rel, 'motivo' => 'json_ilegible'];
            continue;
        }
        $totalExtracciones++;
        $grupo = str_contains($rel, '/') ? explode('/', $rel)[0] : '(raíz)';
        [$refDocumento, $motivo] = catalogo_emparejar($indice, $datos, $grupo);
        if ($refDocumento === null) {
            $huerfanas[] = [
                'ref' => $rel,
                'motivo' => $motivo,
                'archivo_relativo' => $datos['archivo_relativo'] ?? null,
                'origen' => $datos['origen'] ?? null,
            ];
            continue;
        }
        $documentos[$refDocumento]['extracciones'][] = catalogo_normalizar_extraccion($rel, $datos);
    }

    // Dentro de un documento, las páginas en orden.
    foreach ($documentos as &$documento) {
        usort(
            $documento['extracciones'],
            static fn (array $a, array $b): int => ($a['pagina'] ?? 0) <=> ($b['pagina'] ?? 0)
        );
    }
    unset($documento);

    $grupos = [];
    foreach ($documentos as $documento) {
        $grupos[$documento['grupo']][] = $documento;
    }
    ksort($grupos);
    foreach ($grupos as &$lista) {
        usort($lista, static fn (array $a, array $b): int => strcmp($a['nombre'], $b['nombre']));
    }
    unset($lista);

    $con = count(array_filter($documentos, static fn (array $d): bool => $d['extracciones'] !== []));

    return [
        'ok' => true,
        'grupos' => $grupos,
        'huerfanas' => $huerfanas,
        'documentos' => $documentos,
        'totales' => [
            'documentos' => count($documentos),
            'extracciones' => $totalExtracciones,
            'con_extraccion' => $con,
            'sin_extraccion' => count($documentos) - $con,
            'multipagina' => count(array_filter(
                $documentos,
                static fn (array $d): bool => count($d['extracciones']) > 1
            )),
        ],
    ];
}
