# API HTTP

> [← Volver a la guía del operador](README.md)

La **segunda forma** de usar el sistema, además de la terminal: para cuando un
**programa** (otro servicio, una página, un script en otra máquina) necesita
procesar documentos sin ejecutar el CLI.

> **Es opcional.** Si trabajás desde la terminal, el CLI alcanza y esta página no
> te hace falta. La API cubre tres operaciones (`run`, `extract`, `ask`); el lote
> largo, la trazabilidad y la revisión humana se manejan por CLI.

---

## 1. Levantarla

```bash
voucherflow-http --puerto 8000
# o, equivalente:
python -m voucherflow.http --puerto 8000
```

Queda escuchando en `http://127.0.0.1:8000` hasta que la interrumpas con `Ctrl-C`.

| Bandera | Qué hace |
|---|---|
| `--host HOST` | Interfaz donde escuchar (default: `127.0.0.1`, solo tu máquina) |
| `--puerto N` | Puerto (default: `8000`; con `0` el sistema elige uno libre) |
| `--raiz DIR` | Directorio desde el que se resuelven las rutas de los documentos |

Comprobar que responde:

```bash
curl -s localhost:8000/salud
```

---

## 2. Las rutas

| Ruta | Equivale a | Qué hace |
|---|---|---|
| `GET /` | — | Lista las rutas disponibles y la versión |
| `GET /salud` | — | Responde si el servidor está vivo |
| `POST /run` | `voucherflow run` | Pipeline completo → veredicto |
| `POST /extract` | `voucherflow extract` | Extracción → evidencia por campo |
| `POST /ask` | `voucherflow ask` | Pregunta puntual |
| `GET /version` | `voucherflow --version` | Versiones del servidor y del extractor |

---

## 3. Ejemplos

### Procesar un documento

```bash
curl -s -X POST localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"origen": "factura.pdf"}'
```

Campos aceptados (los mismos conceptos que las banderas del CLI):

| Campo | Para qué |
|---|---|
| `origen` | **Obligatorio**: la ruta del documento |
| `condicion_impositiva` | Igual que `--condicion-impositiva` |
| `modelo` | Igual que `--model` |
| `orientation` | Igual que `--orientation` |
| `clasificar_contable` | Igual que `--clasificar-contable` |

### Extraer sin concluir

```bash
curl -s -X POST localhost:8000/extract \
  -H 'Content-Type: application/json' \
  -d '{"origen": "factura.pdf"}'
```

### Preguntar

```bash
curl -s -X POST localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"origen": "factura.pdf", "pregunta": "¿cuál es el total?"}'
```

> Como en el CLI, `ask` **no** produce evidencia ni queda auditado: si la
> respuesta tiene que poder justificarse después, usar `/run`.

### Qué devuelve

```json
{
  "resultado": {
    "documento_id": "sha256:…",
    "estado": "aprobado",
    "tipo_comprobante": "A",
    "certeza": "alta",
    "origen": "programa"
  }
}
```

El objeto es el **mismo contrato** que devuelve la librería y el CLI: no hay un
esquema paralelo que aprender.

---

## 4. Códigos de respuesta

| Código | Qué significa | Qué hacer |
|---|---|---|
| `200` | Salió bien | — |
| `400` | La petición está mal (falta un campo, JSON inválido) | Corregir lo que se envía |
| `404` | La ruta no existe | Verificar la dirección |
| `405` | La ruta no acepta ese método | Usar el método de la lista de la respuesta |
| `422` | El documento se leyó pero **no se pudo procesar** | Revisar el archivo (formato, legibilidad) |
| `503` | **Un modelo no responde** | **Reintentar**: es transitorio |
| `500` | Algo inesperado | Ver el log del servidor |

**La diferencia entre `422` y `503` importa**: el primero dice que el documento no
sirve (reintentar no cambia nada) y el segundo que el modelo no respondió
(reintentar es exactamente lo que hay que hacer).

> **Un rechazo no es un error.** Un documento que no es comprobante se responde
> `200` con `"estado": "rechazado"`: el sistema **sí** concluyó (concluyó que no).
> Tratarlo como error haría que un programa descartara una conclusión válida.

---

## 5. Alcance y seguridad

**Esto es la versión básica y es importante saberlo antes de exponerla.**

- **No trae autenticación, ni cifrado (TLS), ni control de CORS.** Cualquiera que
  llegue al puerto puede pedir procesamiento de documentos.
- Por eso el servidor **escucha solo en tu máquina** (`127.0.0.1`) por defecto.
  Para publicarlo hay que pasar `--host` explícitamente, y el arranque **avisa**.
- Si hace falta exponerlo a una red, va **detrás de un proxy** que ponga
  autenticación y TLS (nginx, Caddy, el ingress de tu plataforma).
- El cuerpo de cada petición tiene un **tope de tamaño**, para que una petición
  gigante no consuma la memoria del servidor.

**Lo que esta API no hace** (y no es un olvido, es el alcance de la fase):

| No hace | Por qué |
|---|---|
| `batch` (lotes largos) | Un lote puede tardar horas: no encaja en una petición que espera respuesta. Se usa el CLI |
| `case` / `hitl` | La consulta de trazabilidad y la revisión humana se manejan por CLI |
| Documentos adjuntos en la petición | El servidor lee archivos **de su máquina** (el campo `origen` es una ruta). Enviar bytes requiere resolver antes el almacenamiento temporal |
| Progreso en vivo | Cada ruta responde cuando termina |

---

## 6. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `no se pudo escuchar en …: Address already in use` | El puerto está ocupado | Usar otro puerto (`--puerto 8001`) |
| `curl: connection refused` | El servidor no está corriendo, o no en ese host/puerto | Verificar el arranque y el puerto |
| Siempre `422` | La ruta `origen` no existe **desde el servidor** | Recordar: la ruta la resuelve el servidor. Usar `--raiz` para fijar la base |
| `503` repetido | Ollama no está corriendo o falta el modelo | Ver [instalación](01-instalacion.md) §3 |
| El puerto queda tomado tras `Ctrl-C` | Un proceso anterior quedó vivo | Buscar y detener el proceso, o usar otro puerto |

---

## 7. Para quien desarrolla

- El servidor es un **transporte**, no una segunda implementación: cada ruta
  delega en `voucherflow.api`. Si una ruta necesitara lógica propia, iría en la
  librería, no acá.
- Está hecho con `http.server` de la **biblioteca estándar**: no agrega
  dependencias. Si en el futuro hacen falta OpenAPI, streaming o auth, entra un
  framework (FastAPI) como **decisión explícita**, conservando las rutas.
- El arranque es un binario **aparte** (`voucherflow-http`) y **no** un subcomando
  del CLI: el contrato de los once subcomandos está verificado por los tests de
  paridad y de documentación.
