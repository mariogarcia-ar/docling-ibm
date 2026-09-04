---
description: "Extrae los campos de control de un comprobante/factura (questions.yaml) y devuelve el resultado en JSON"
agent: "agent"
argument-hint: "Ruta del archivo a procesar (ej. files/2026-06/11F8A95F/xxx.md)"
---
Ejecutá `wip/extract_template.py` sobre el archivo indicado por el usuario para
extraer los campos definidos en [questions.yaml](../../questions.yaml), usando
el modelo `qwen2.5vl:3b` de Ollama.

Archivo a procesar: ${input:archivo}

Pasos:
1. Verificá que el archivo exista en el workspace.
2. Corré: `python wip/extract_template.py "${input:archivo}" -m qwen2.5vl:3b`
3. Mostrale al usuario el JSON resultante, campo por campo, en una tabla o lista clara.
4. Si algún campo obligatorio (tipo_factura, cuit_emisor, fecha_emision, nro_factura,
   importe_total_facturado) quedó vacío o en "N/A", marcalo explícitamente como un
   dato a revisar manualmente.
