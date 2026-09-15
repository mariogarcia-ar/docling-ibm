<?php

declare(strict_types=1);

/**
 * Presentación de los campos extraídos: qué se llama cada uno, en qué orden y
 * con qué formato se muestra.
 *
 * Las etiquetas NO se derivan del nombre técnico. Un nombre como
 * `digito_verificador_cuit_valido` rotulado «Digito Verificador Cuit Valido» es
 * peor que no rotularlo: el JSON ya está en pantalla. Los textos de acá son la
 * traducción del esquema (`src/voucherflow/llm/esquemas.py`, `esquema_extraccion`)
 * — si el esquema cambia, esta tabla es lo que hay que mirar.
 *
 * El orden es el del esquema, que va de la identificación del comprobante a los
 * datos inferidos; leer el original es entender por qué 25 campos tienen ese orden.
 */

/**
 * Campos de la extracción: clave → [etiqueta, tipo].
 *
 * `tipo` decide el formato: `texto` | `numero` | `booleano` | `lista` | `largo`.
 * `largo` es para lo que no entra en una fila (la observación del modelo).
 */
function presentacion_campos(): array
{
    return [
        'legibilidad' => ['Legibilidad de la imagen', 'texto'],
        'tipo_comprobante' => ['Tipo / letra', 'texto'],
        'codigo_afip' => ['Código AFIP', 'texto'],
        'razon_social_emisor' => ['Razón social del emisor', 'texto'],
        'cuit_emisor' => ['CUIT del emisor', 'texto'],
        'digito_verificador_cuit_valido' => ['DV del CUIT válido', 'booleano'],
        'fecha_emision' => ['Fecha de emisión', 'texto'],
        'nro_factura' => ['Punto de venta y número', 'texto'],
        'moneda' => ['Moneda', 'texto'],
        'subtotal' => ['Neto gravado', 'numero'],
        'no_gravado' => ['No gravado', 'numero'],
        'exento' => ['Exento', 'numero'],
        'discrimina_impuestos' => ['Discrimina impuestos', 'booleano'],
        'iva' => ['IVA', 'numero'],
        'impuestos_internos' => ['Impuestos internos', 'numero'],
        'percepciones_iibb' => ['Percepciones IIBB', 'numero'],
        'otros_impuestos' => ['Otros impuestos', 'numero'],
        'importe_total' => ['Importe total', 'numero'],
        'cierra_aritmetica' => ['Cierra la aritmética', 'booleano'],
        'rubro_emisor' => ['Rubro del emisor', 'texto'],
        'categoria_gasto_sugerida' => ['Categoría de gasto sugerida', 'texto'],
        'cantidad_comensales_personas' => ['Comensales / cubiertos', 'numero'],
        'cantidad_litros' => ['Litros', 'numero'],
        'centro_de_costo' => ['Centro de costo', 'texto'],
        'campos_no_legibles' => ['Campos no legibles', 'lista'],
        'observaciones' => ['Observaciones del modelo', 'largo'],
    ];
}

/** Campos que se muestran como «banderas» arriba, no en la tabla de datos. */
function presentacion_banderas(): array
{
    return [
        'legibilidad',
        'tipo_comprobante',
        'cuit_emisor',
        'digito_verificador_cuit_valido',
        'importe_total',
        'cierra_aritmetica',
        'discrimina_impuestos',
    ];
}

/**
 * Los campos de `campos` en el orden del esquema.
 *
 * Lo que el esquema no declara va al final: un campo nuevo del laboratorio se ve
 * igual (con su clave cruda como etiqueta) en vez de desaparecer de la vista.
 */
function presentacion_ordenar_campos(array $campos): array
{
    $declarados = presentacion_campos();
    $ordenados = [];
    foreach ($declarados as $clave => $meta) {
        if (array_key_exists($clave, $campos)) {
            $ordenados[$clave] = [$meta[0], $meta[1], $campos[$clave]];
        }
    }
    $extras = array_diff_key($campos, $declarados);
    ksort($extras);
    foreach ($extras as $clave => $valor) {
        $ordenados[$clave] = [$clave, 'texto', $valor];
    }

    return $ordenados;
}

/** Formatea un importe en es-AR, sin decimales si no los tiene. */
function presentacion_numero(float|int $valor): string
{
    if (is_float($valor) && floor($valor) !== $valor) {
        return number_format($valor, 2, ',', '.');
    }

    return number_format((float) $valor, 0, ',', '.');
}

/**
 * ¿El valor está leído?
 *
 * ⚠️ `null` y `0` NO son lo mismo y la distinción es del dominio: el laboratorio
 * usa `null` para «no legible / no figura» y `0` para «figura y vale cero». El
 * caso medido es el monto 0 que no cierra la aritmética. Un `if (!$valor)`
 * colapsaría los dos y borraría esa información de la pantalla, así que acá se
 * pregunta por `null` y por cadena vacía, nunca por truthiness.
 *
 * ⚠️ La misma trampa, un nivel más adentro: en una lista del esquema
 * (`campos_no_legibles`), el `[]` es una RESPUESTA — «no hubo ninguno» — y no un
 * dato faltante. Por eso el `tipo` es opcional: quien conoce la forma del campo
 * lo pasa (`lista`) y el vacío se lee como leído. Sin esto, el mejor resultado
 * posible (todo legible) se mostraba como una celda en blanco y el filtro lo
 * escondía detrás de la misma etiqueta que un `null`.
 */
function presentacion_leido(mixed $valor, ?string $tipo = null): bool
{
    if ($valor === null) {
        return false;
    }
    if (is_string($valor)) {
        return trim($valor) !== '';
    }
    if (is_array($valor)) {
        return $tipo === 'lista' ? true : $valor !== [];
    }

    return true;
}

/** Texto a mostrar de un valor, ya formateado según su tipo. */
function presentacion_valor(mixed $valor, string $tipo): string
{
    if ($valor === null) {
        return 'sin dato';
    }
    if (is_bool($valor)) {
        return $valor ? 'sí' : 'no';
    }
    if (is_array($valor)) {
        // Una lista vacía se dice con una palabra; una celda en blanco se lee
        // como un error de render, no como «ninguno».
        return $valor === []
            ? 'ninguno'
            : implode(' · ', array_map(static fn ($v): string => (string) $v, $valor));
    }
    if (is_int($valor) || is_float($valor)) {
        return presentacion_numero($valor);
    }

    return (string) $valor;
}

/**
 * Clase CSS del valor, para que lo ilegible y lo cero se lean distinto.
 *
 * `cero` es explícito y no `numero`: un 0 de importe es una lectura, y merece
 * verse igual que las demás (lo que se apaga es el `null`).
 */
function presentacion_clase(mixed $valor, string $tipo): string
{
    if (!presentacion_leido($valor, $tipo)) {
        return 'valor valor-vacio';
    }
    if (is_bool($valor)) {
        return 'valor ' . ($valor ? 'valor-si' : 'valor-no');
    }
    if (is_int($valor) || is_float($valor)) {
        return 'valor valor-numero';
    }

    return 'valor valor-' . $tipo;
}

/** Fecha UTC → texto local corto, sin depender de la zona del proceso. */
function presentacion_fecha(?string $iso): string
{
    if ($iso === null || $iso === '') {
        return '—';
    }
    try {
        return (new DateTimeImmutable($iso))->format('Y-m-d H:i');
    } catch (Exception) {
        return $iso;
    }
}

/** Tamaño en bytes → texto legible. */
function presentacion_bytes(int $bytes): string
{
    if ($bytes < 1024) {
        return $bytes . ' B';
    }
    if ($bytes < 1024 * 1024) {
        return number_format($bytes / 1024, 0) . ' KB';
    }

    return number_format($bytes / 1024 / 1024, 1) . ' MB';
}

/**
 * Resumen de una extracción en una línea, para el listado.
 *
 * Prioriza lo que identifica al comprobante; si no hay nada de eso (un negativo,
 * por ejemplo) cae al motivo, que suele ser la parte útil de esa lectura.
 */
function presentacion_resumen(array $campos): string
{
    $partes = [];
    if (presentacion_leido($campos['tipo_comprobante'] ?? null)) {
        $partes[] = 'tipo ' . $campos['tipo_comprobante'];
    }
    if (presentacion_leido($campos['razon_social_emisor'] ?? null)) {
        $partes[] = $campos['razon_social_emisor'];
    }
    if (presentacion_leido($campos['importe_total'] ?? null)) {
        $partes[] = '$ ' . presentacion_numero((float) $campos['importe_total']);
    }
    if ($partes !== []) {
        return implode(' · ', $partes);
    }
    if (presentacion_leido($campos['observaciones'] ?? null)) {
        $texto = trim((string) $campos['observaciones']);

        return mb_strimwidth($texto, 0, 150, '…', 'UTF-8');
    }

    return 'sin campos leídos';
}
