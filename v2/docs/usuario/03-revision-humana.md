# Flujo de revisión humana

> [← Volver a la guía del operador](README.md)

Qué hacer con los casos que el sistema manda a revisar, y por qué llegan ahí.

---

## Por qué un caso llega a revisión

El sistema **solo decide cuando puede sostener la decisión**. Cuando no puede,
prefiere decirlo antes que inventar un veredicto. Los motivos son dos, y se
distinguen por la **prioridad**:

| Prioridad | Motivo | Qué significa |
|---|---|---|
| **`alta`** | **Certeza baja** | El sistema **no pudo concluir**: el código no encontró sostén suficiente y lo resolvió (o lo intentó) un modelo. Revisión **obligatoria** |
| **`baja`** | **Muestreo de auditoría** | El caso se resolvió con certeza alta, pero entró a una **muestra** para control de calidad |

La revisión de certeza baja existe porque el sistema es honesto sobre lo que no
sabe. La de muestreo existe porque un sistema que acierta siempre deja de ser
auditable: se revisa un porcentaje de los casos buenos, al azar, para poder
detectar que las reglas aciertan *por accidente*.

## Cómo se ven los casos a revisar

```bash
voucherflow hitl list --dir salida/cases --prioridad alta
```

Cada entrada trae el documento, la prioridad y **el motivo**. Para entender el
caso:

```bash
voucherflow case show <documento_id> --dir salida/cases
```

## Cómo leer el registro de un caso

El registro tiene cuatro bloques que responden las preguntas de una revisión:

| Bloque | La pregunta que responde |
|---|---|
| `evidencia_por_fuente` | **¿Qué leyó cada fuente?** Las lecturas de la imagen y del texto, campo por campo, con el **fragmento del documento** que sostiene cada valor |
| `reglas_disparadas` | **¿Qué reglas se aplicaron?** Las de lectura, las cruzadas y las que detectaron el problema |
| `modelo_por_etapa` / `version_prompt` | **¿Con qué se leyó?** El modelo y la versión del prompt de cada etapa |
| `quien_decidio` | **¿Quién decidió?** `programa` (el código), `agente_ia` (un modelo) o `hitl` (una persona) |

Una revisión típica mira primero **qué dicen las dos fuentes**: si la imagen y el
texto leyeron cosas distintas, el problema suele estar en la lectura (imagen
borrosa, OCR flojo, formato raro) y no en las reglas.

## Qué hacer cuando se confirma un problema

Si el caso es un **error del sistema**, la corrección es información valiosa: es
lo que permite mover casuística a reglas y mejorar el sistema. **Hoy esa
corrección no se carga desde el CLI.**

| Tarea | Cómo se hace |
|---|---|
| Ver los casos pendientes | `voucherflow hitl list` (con el CLI) |
| Entender un caso | `voucherflow case show <id>` (con el CLI) |
| Ver las correcciones registradas | Requiere usar la librería desde Python |
| **Registrar una corrección** | Requiere usar la librería desde Python |

La corrección se registra con la cola de revisión de la librería
(`voucherflow.conclusion.hitl`), que guarda campo, valor anterior, valor nuevo,
motivo, revisor y fecha. Ese registro alimenta el ajuste de reglas y prompts.

```python
from voucherflow.conclusion import ColaHitl

cola = ColaHitl()
entrada = cola.registrar_correccion(
    "<documento_id>",
    campo="tipo_comprobante",
    valor_nuevo="B",
    motivo="El recuadro dice B; el sistema leyó C por el sello",
    revisor="mgarcia",
)
for c in entrada.correcciones:      # cada corrección: campo, valores, motivo…
    print(c.como_dict())
```

> **Alcance honesto — dos límites, y conviene entenderlos antes de usarlo**:
>
> 1. **El comando de terminal para cargar correcciones no existe.** Se puede
>    agregar cuando haya un flujo de revisión en producción.
> 2. **La cola es en memoria y por corrida.** `registrar_correccion` exige que el
>    caso ya esté **encolado en esa misma instancia** (si no, lanza `KeyError`:
>    no se acepta feedback de un caso que nunca se encoló). O sea: no alcanza con
>    abrir un `ColaHitl()` nuevo y pasarle un `documento_id` leído del histórico;
>    la corrección se registra **dentro del proceso que encoló el caso**.
>
> El registro **durable** de la revisión (el store persistente) todavía no tiene
> comando. Mientras tanto, la revisión desde la terminal es de **lectura**: ver
> qué quedó pendiente, con `hitl list --dir`, y entender cada caso con `case
> show`. Un caso revisado sin corrección registrada deja al sistema sin aprender
> del error.

## Las dos razones de auditar, y por qué no se mezclan

Cuando se revisa un caso, hay dos situaciones que **se ven parecidas y no lo
son**:

| Situación | Qué significa | Qué mejora |
|---|---|---|
| Se corrige un caso de **certeza baja** | El **modelo** se equivocó al decidir | Mover esa casuística a **reglas** (que el código lo resuelva) |
| Se corrige un caso del **muestreo** | Una **regla** acertó por accidente | Ajustar la **regla** |

El sistema las separa al registrar el feedback, porque mezclarlas perdería la
señal de cada una: la primera dice *"acá falta una regla"*, la segunda *"esta
regla no es confiable"*.

## Cuánto pesa la revisión

```bash
voucherflow case aggregate --dir salida/cases | python -c "
import json, sys
d = json.load(sys.stdin)
r = d['resumen']
print('documentos:', r['documentos'])
print('requieren revisión:', r['requieren_revision'])
print('  obligatoria:', r['revision_obligatoria'])
print('por estado:', r['por_estado'])
"
```

Dos cosas para mirar:

- **`requieren_revision` alta y creciendo**: al sistema le está faltando
  concluir. Es la señal de que hay casuística que todavía no está en reglas.
- **`revision_obligatoria` muy bajo**: no necesariamente es bueno — puede
  significar que el sistema está decidiendo cosas que no debería. El muestreo de
  auditoría existe justamente para detectar eso.

## Ajustar el muestreo

El muestreo de auditoría se configura en `voucherflow.yaml`:

```yaml
hitl:
  muestreo_tasa: 0.10        # 10% de los casos de certeza alta entran a la muestra
  muestreo_semilla: 0        # cambia qué casos caen en la muestra, no cuántos
  muestreo_activo: true
  revision_obligatoria_certeza_baja: true
```

El muestreo es **reproducible**: el mismo documento con la misma semilla cae
siempre igual. Eso permite responder *"¿por qué se auditó éste y no aquél?"* — con
un sorteo sin semilla, esa pregunta no tendría respuesta, y una auditoría que no
se puede explicar no es una auditoría.

`muestreo_tasa: 1.0` audita todo; `0.0` no audita nada (no recomendado: deja al
sistema sin control externo).

> **La revisión obligatoria de certeza baja se puede desactivar**
> (`revision_obligatoria_certeza_baja: false`), pero es una política riesgosa:
> deja casos sin veredicto firme **y sin que nadie los mire**. Si se desactiva, el
> registro lo declara.
