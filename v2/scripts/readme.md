# `scripts/` — verificación por tarea y utilidades operativas

Dos tipos de script conviven acá:

1. **Verificación por tarea** (`F<n>/t<n><nn>.py`): cada tarea del plan tiene un
   script que la ejercita de punta a punta y sale con **código 0**. Son la
   contraparte ejecutable de los tests de `tests/` (misma convención que
   `tests/golden/F<n>/` + `scripts/F<n>/paridad_*.py`).

   ⚠️ Los de `F1/*` y `F2/t20*.py` **piden argumentos** (`rutas`): invocados
   pelados salen con exit 2. No es un fallo.

   ```bash
   python scripts/F6/t602.py          # 12 escenarios + 6 fronteras
   python scripts/F3/t305.py          # paridad de la letra (5/5 vs 2/5)
   ```

2. **Utilidades operativas** (raíz de `scripts/`): herramientas para preparar y
   diagnosticar el corpus **antes** de procesarlo. No pertenecen a una fase.

   | Script | Para qué |
   |---|---|
   | `reducir_tokens.py` | Pre-reduce el peso y los tokens de visión de un corpus de imágenes. |

---

## `reducir_tokens.py` — bajar tokens del corpus

Reduce las imágenes a un **lado mayor objetivo** (1024 px por defecto, sin
agrandar nunca), las reencoda con calidad moderada y espeja el árbol de carpetas
bajo una salida (`procesadas/`). Menos bytes en disco/red y **menos tokens de
visión** cuando la imagen después viaja a un VLM.

Porta el loop de shell con `ffmpeg` y le corrige cuatro defectos:

1. `scale=1024:-1` **agranda** las imágenes chicas (más peso y más tokens que el
   original). Acá una imagen que ya entra en el objetivo no se toca.
2. `scale=1024:-1` fija el **ancho**, no el lado mayor: una foto vertical de
   3000×4000 salía 1024×1365 en vez de 768×1024 (~45% más tokens). Acá se
   reduce por lado mayor.
3. Siempre escribía JPEG, incluso con salida `.png` (bytes JPEG en un archivo
   `.png`). Acá el formato se elige por extensión, o se fuerza con `--formato jpg`.
4. Sin paralelismo, sin reanudación ni reporte. Acá hay `--workers`, se saltea
   el destino ya existente y hay resumen + reporte JSON.

**Dimensiones alineadas al VLM.** Las dimensiones destino se calculan con
`voucherflow.validation.prompt_qween.dimensiones_objetivo_vlm` — el **mismo**
`smart_resize` de Qwen2.5-VL que usa el pipeline v2 — así el servidor no
re-escalea y el conteo de tokens es predecible. Los defaults (1024 px / calidad
80 / piso de lado menor 256) salen de la librería: una sola fuente de verdad con
la vista de revisión (E-QWE-2). Si la librería no se puede importar, cae a una
alineación local a múltiplos de 28 y lo **declara** en el resumen
(`defaults_desde`).

```bash
# Siempre conviene empezar midiendo (no escribe nada)
python scripts/reducir_tokens.py ../files --solo-medir

# Elegir la carpeta de salida (default: procesadas)
python scripts/reducir_tokens.py ../files -o salida

# Prueba con 20 imágenes, con una línea por archivo
python scripts/reducir_tokens.py ../files --limite 20 --detalle

# Corpus completo: 4 workers y reporte JSON
python scripts/reducir_tokens.py ../files -o salida --workers 4 \
  --reporte salida/reporte.json

# Otra resolución (p. ej. antes de un OCR clásico)
python scripts/reducir_tokens.py ../files --lado-mayor 1536 --workers 4
```

**Carpeta de salida (`-o` / `--salida`).** El árbol se espeja desde la **raíz de
la entrada**, sin repetir el nombre de la carpeta de entrada. Con
`../files/2025-08/2D2C9343/foto.jpg`:

| `-o` | Archivo resultante |
|---|---|
| *(omitida)* | `procesadas/2025-08/2D2C9343/foto.jpg` |
| `salida` | `salida/2025-08/2D2C9343/foto.jpg` |
| `/tmp/reducido` | `/tmp/reducido/2025-08/2D2C9343/foto.jpg` |

⚠️ `-o` es **solo para las imágenes**; el JSON del reporte va aparte con
`--reporte`. Y si la salida cae dentro de la entrada (p. ej. `-o .`), el script
**excluye** del recorrido los archivos que ya están dentro de ella, para no
reprocesar lo reducido.

Salida (ejemplo real, 200 imágenes de `../files`):

```text
archivos                  : 200
  reducidos               : 199
  omitidos                : 1
  fallos                  : 0
peso                      : 573.6 MiB  →  20.0 MiB   (-96.5%)
tokens de visión (estim.) : 1,423,303  →  214,020   (-85.0%)
```

### Banderas

| Bandera | Efecto |
|---|---|
| `-o, --salida` | Carpeta raíz de salida (default `procesadas`). El árbol se espeja desde la raíz de la entrada. |
| `--lado-mayor PX` | Lado mayor objetivo (default 1024). **Nunca agranda.** |
| `--calidad 1-100` | Calidad de reencode (default 80). |
| `--piso-lado-menor PX` | Piso del lado menor, para imágenes muy alargadas (default 256). |
| `--sin-alinear` | No alinear a múltiplos de 28 (solo si el destino NO es Qwen2.5-VL). |
| `--backend pillow\|ffmpeg` | Motor de reencode (default `pillow`). |
| `--formato mismo\|jpg` | Conserva la extensión o fuerza JPEG (default `mismo`). |
| `--extensiones LISTA` | Extensiones a procesar (default `.jpeg,.jpg,.png`). |
| `--forzar` | Reescribe el destino aunque exista; sin esto **reanuda**. |
| `--copiar-no-reducidas` | Copia sin tocar las que ya entran en el objetivo (salida completa). |
| `--solo-medir` | No escribe nada: mide y reporta qué se reduciría. |
| `--workers N` | Concurrencia (default 4). Usá 1 si el equipo se calienta. |
| `--limite N` | Procesa solo las primeras N (0 = todas). |
| `--detalle` | Una línea por archivo (a stderr). |
| `--reporte ARCHIVO.json` | Reporte completo (resumen + por archivo). |

Códigos de salida: `0` ok (o nada que hacer) · `1` hubo fallos · `2` error de uso
o backend no disponible · `130` interrumpido.

### Cosas que conviene saber

- **Es reanudable.** Si el destino existe, se saltea. Ese es el modo de retomar
  un lote cortado; `--forzar` lo reescribe.
- **Mide el destino real al reanudar**, no lo que *habría* calculado: así el
  reporte no miente si el archivo existente se generó con otros parámetros.
- **En `--solo-medir` el peso destino no se mide** (no se escribió) y el resumen
  lo declara en vez de reportar un «-100%» inventado. Los tokens sí se comparan:
  se derivan de las dimensiones calculadas, no del archivo escrito.
- **Los tokens son una estimación** (`ceil(ancho/28) × ceil(alto/28)`, el
  gridding de Qwen2.5-VL). Sirve para comparar antes/después; no es lo que
  reporta el servidor.
- ⚠️ **Cuidado con el OCR clásico.** Reducir *antes* de un OCR por píxeles
  (RapidOCR/EasyOCR vía Docling, `v1/ocr_documents.py`) puede degradar la letra
  chica. Usá `--solo-medir`, subí `--lado-mayor` o no reduzcas. Para el camino
  **VLM** (la imagen viaja al modelo) reducir es lo correcto.
- **`--backend ffmpeg`** requiere un ffmpeg sano. El script hace un *preflight*
  y, si el binario no arranca (típico en macOS con dependencias de Homebrew
  rotas), falla **una vez** con un mensaje claro en vez de repetir el error de
  dyld por archivo.
