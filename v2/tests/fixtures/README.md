# Fixtures de datos reales (v2/tests/fixtures)

Conjunto de archivos reales copiados desde `files/` para usar como **datos de
prueba** (unitarias de F1/F2/integración). `files/` es una carpeta **temporal
ignorada por git** (`.gitignore`), por lo que los tests no pueden depender de
ella: estas copias son la fuente estable y versionada.

## Criterio de selección (2026-09-06)

Se tomaron los archivos de `files/` ordenados por tamaño y se eligieron:

- `grandes/` — los **10 más grandes** (~4.3–4.7 MB; cubren imágenes pesadas).
- `chicos/` — los **10 más chicos** (~4–13 KB; PDFs/PNGs mínimos).
- `otros/` — **20 del rango medio** (muestreo uniforme entre extremos).
- `pdf_escaneados/` — **5 PDFs sin capa de texto** (detectados con PyMuPDF,
  T-101; cubren la ruta `pdf_escaneado → imagen → OCR/VLM` para la paridad de
  T-105).
- `pdf_aptos_layout/` — **5 PDFs con texto nativo real apto para
  `pdftotext --layout`** (el texto supera a la imagen; cubren la ruta de texto
  nativo / recuperación de layout).

Distribución total: 50 archivos: 27 jpg · 16 pdf · 4 png · 3 jpeg.

Cada archivo conserva su **nombre original** (id UUID). La correspondencia con
la ruta original de `files/` y el tamaño queda en [`manifest.json`](manifest.json).

## Uso en tests

El `conftest.py` de la suite expone:

- `fixtures_dir` → `Path` a `v2/tests/fixtures/`.
- `fixtures_manifest` → dict cargado desde `manifest.json`.
- `fixtures_archivos` → lista de `Path` a todos los archivos copiados.

Ejemplo:

```python
def test_procesa_fixture(fixtures_dir):
    pdf_chico = fixtures_dir / "chicos" / "<nombre>.pdf"
    assert pdf_chico.exists()
```

## Regenerar

Si `files/` cambia y querés refrescar las copias, borrá `v2/tests/fixtures/`
(excepto este README y el script) y re-copíá con el mismo criterio. El
`manifest.json` se regenera junto con las copias para mantener la trazabilidad
(no guardar `files/` en el repo).
