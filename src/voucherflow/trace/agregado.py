"""Salida agregada del lote (F6 / T-603, E-CLI-2).

**Fase**: F6 · **Tarea**: T-603 · **Épica**: E-CLI-2 · **ADR-005 / ADR-009**.

El Gherkin de E-CLI-2 es de dos mitades::

    Dado un documento procesado por el CLI
    Cuando se guarda el resultado
    Entonces se genera un JSON con resultado + evidencia + trazabilidad (sidecar)
    Y en modo lote se puede consolidar en un único JSON agregado

La **primera** mitad ya estaba hecha en F5/T-506 (`CaseRecorder`: el sidecar
`<doc>.case.json` con el `CaseRecord` completo + el índice `index.jsonl`); T-601
la expuso por el CLI (`--cases DIR`, `case show/list`). Lo que faltaba es la
**segunda**: el **único JSON consolidado** del lote.

Qué es (y qué no es) el agregado
--------------------------------
Es un **índice de la corrida**: por cada documento, el veredicto y un puntero a
la evidencia completa, más una síntesis del lote (*cuántos y de qué tipo*). **No**
es una copia de los `CaseRecord`.

Esa decisión es la importante y conviene explicarla, porque la alternativa
—embeber los `CaseRecord` enteros— parece más completa y es peor:

- **No duplica el dato.** El `CaseRecord` completo puede pesar cientos de KB
  (todas las lecturas, por fuente, con su sostén). Un lote de mil documentos
  produciría un agregado de cientos de MB: un archivo que nadie puede abrir y
  que además **duplica** información que ya está en el sidecar. El Gherkin pide
  *un* JSON consolidado, no *el* JSON con todo.
- **No crea una segunda fuente de verdad.** El sidecar es el registro auditable
  (ADR-005). Si el agregado lo copiara, tendríamos dos lugares que dicen lo mismo
  y que pueden **divergir**: corregir un caso dejaría el agregado viejo mintiendo
  sin que nadie lo note. Con punteros, el agregado no puede contradecir al
  sidecar — solo puede apuntar a él.
- **La evidencia se sigue leyendo donde corresponde.** Para auditar un caso se
  abre su sidecar (`voucherflow case show <id>`), que es el flujo que ya existe.

Así, el agregado responde *"¿qué pasó en el lote?"* en un archivo, y el sidecar
responde *"¿por qué se decidió así?"* en otro. Cada pregunta en su lugar.

Acumulación entre corridas
--------------------------
El agregado se **acumula** (``agregar_a_archivo``): cada corrida suma los
documentos que procesó, con **una entrada por documento** (el mismo documento
reprocesado actualiza su entrada, no agrega otra). Es lo que hace que el archivo
sirva como estado de la carpeta y no como el log de la última corrida: un lote
interrumpido y reanudado termina con **un** agregado coherente.

La escritura es **atómica** (mismo patrón que el sidecar y el checkpoint): un
agregado a medio escribir sería peor que no tenerlo, porque es el archivo que se
mira para saber si el lote terminó.

Las métricas no se inventan
---------------------------
``metricas`` se calcula con el módulo de F5/T-507 (``metricas_de``), sobre los
``CaseRecord`` **persistidos**. Si no hay histórico persistido del cual
derivarlas, el agregado **no las incluye y lo declara** en lugar de mostrar
números que no puede sostener. Igual criterio que el resto del repo: "no saber" no
es "saber que es cero".

Alcance de T-603 (lo que **no** hace)
-------------------------------------
- **No** reimplementa la persistencia por caso (F5/T-506) ni el runner de lotes
  (T-602): consume los ``PipelineResult`` y los ``CaseRecord`` que ya existen.
- **No** embebe la evidencia: apunta al sidecar (§ arriba).
- **No** recalcula el pipeline ni las métricas: proyecta y agrega lo que las
  etapas produjeron.

Referencias: Gherkin E-CLI-2, ADR-005 (qué se persiste) y ADR-009 (sidecar +
índice), doc 03 §8.2/§9, `TRACE.md`, F6-subplan §3.3.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..schemas.result import CaseRecord, SCHEMA_VERSION
from .metricas import VERSION_METRICAS, metricas_de
from .recorder import VERSION_TRAZA, CaseRecorder, sidecar_para

#: Versión del formato del agregado. Va en el archivo para poder distinguir un
#: agregado viejo de uno roto (mismo criterio que ``VERSION_TRAZA``).
VERSION_AGREGADO = "agregado-lote@1"

#: Campos del ``CaseRecord`` que **no** se copian al agregado. Se declaran para
#: que la intención sea explícita: el agregado es un índice, no una copia.
NO_AGREGADOS: tuple[str, ...] = (
    "evidencia_por_fuente",
    "etapas",
)


@dataclass
class EntradaDocumento:
    """Un documento dentro del agregado (T-603): veredicto + puntero.

    Campos:
        documento_id: id del documento (``sha256`` del contenido).
        archivo: ruta del documento de origen (referencial).
        ok: si la corrida terminó con veredicto (un rechazo firme es ``ok``).
        estado: ``aprobado`` | ``rechazado`` | ``revision``.
        tipo_comprobante / certeza / origen: el veredicto consolidado.
        hitl_requerido / hitl_prioridad: la expectativa de revisión humana.
        campos_extraidos: cuántos campos quedaron con valor vigente.
        clasificacion_contable: la clasificación de F3, si se corrió.
        etapas: las etapas completadas (trazabilidad corta de la corrida).
        sidecar: nombre del sidecar del ``CaseRecord`` (el puntero a la
            evidencia completa), si se persistió.
        error: el motivo, cuando la corrida no pudo completarse.
    """

    documento_id: str
    archivo: str | None = None
    ok: bool = False
    estado: str | None = None
    tipo_comprobante: str | None = None
    certeza: str | None = None
    origen: str | None = None
    hitl_requerido: bool | None = None
    hitl_prioridad: str | None = None
    campos_extraidos: int = 0
    clasificacion_contable: bool = False
    etapas: tuple[str, ...] = ()
    sidecar: str | None = None
    error: str | None = None

    def como_dict(self) -> dict[str, Any]:
        """Vista serializable, sin claves nulas (el agregado se lee a ojo)."""
        datos: dict[str, Any] = {
            "documento_id": self.documento_id,
            "archivo": self.archivo,
            "ok": self.ok,
            "estado": self.estado,
            "tipo_comprobante": self.tipo_comprobante,
            "certeza": self.certeza,
            "origen": self.origen,
            "hitl_requerido": self.hitl_requerido,
            "hitl_prioridad": self.hitl_prioridad,
            "campos_extraidos": self.campos_extraidos,
            "clasificacion_contable": self.clasificacion_contable,
            "etapas": list(self.etapas),
            "sidecar": self.sidecar,
        }
        if self.error:
            datos["error"] = self.error
        return {clave: valor for clave, valor in datos.items() if valor is not None}

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "EntradaDocumento":
        campos = EntradaDocumento.__dataclass_fields__
        valores = {k: datos.get(k) for k in campos if k in datos}
        valores["etapas"] = tuple(valores.get("etapas") or ())
        valores.pop("version", None)
        return cls(**valores)  # type: ignore[arg-type]


def entrada_de_resultado(resultado: Any) -> EntradaDocumento:
    """Proyecta un ``PipelineResult`` a la entrada del agregado (T-603).

    Es una **proyección**: no ejecuta nada, solo lee lo que la corrida dejó. El
    nombre del sidecar es lo que convierte la entrada en un puntero: sin él, la
    entrada dice *qué* pasó pero no dónde está el *por qué*.

    Lee el veredicto del ``VoucherResult`` y, si no está, del ``resumen``. Las dos
    fuentes llevan el mismo dato (el orquestador publica las dos), así que caer al
    ``resumen`` no inventa nada — y evita perder el veredicto cuando el resultado
    llegó sin el objeto tipado. Que la entrada diga ``sin_estado`` cuando el
    veredicto **sí** existía sería afirmar que no se resolvió, que es peor que no
    tener la proyección.
    """
    resumen = resultado.resumen or {}
    resultado_voucher = resultado.resultado

    def _de_voucher(campo: str, directo: Any) -> Any:
        return directo if directo is not None else resumen.get(campo)

    sidecar = None
    persistencia = (resultado.detalle or {}).get("persistencia") or {}
    if persistencia.get("sidecar"):
        sidecar = os.path.basename(str(persistencia["sidecar"]))
    elif resultado.caso is not None:
        sidecar = sidecar_para(resultado.caso.documento_id)

    return EntradaDocumento(
        documento_id=resultado.documento_id or "",
        archivo=resultado.archivo,
        ok=bool(resultado.ok),
        # ``PipelineResult.estado`` ya cae al resumen cuando no hay resultado; si
        # tampoco hay resumen, es ``None`` (y se reporta como ``sin_estado``).
        estado=resultado.estado or resumen.get("estado"),
        tipo_comprobante=_de_voucher("tipo_comprobante", resumen.get("tipo_comprobante")),
        certeza=_de_voucher("certeza", resumen.get("certeza")),
        origen=_de_voucher("origen", resumen.get("origen")),
        hitl_requerido=_de_voucher("hitl_requerido", resumen.get("hitl_requerido")),
        hitl_prioridad=(
            resultado_voucher.hitl.prioridad
            if resultado_voucher is not None
            else resumen.get("hitl_prioridad")
        ),
        campos_extraidos=int(resumen.get("campos_extraidos") or 0),
        clasificacion_contable=bool(resumen.get("clasificacion_contable")),
        etapas=tuple(resultado.etapas_completadas or ()),
        sidecar=sidecar,
        error=resultado.error,
    )


def entrada_de_caso(caso: CaseRecord) -> EntradaDocumento:
    """Proyecta un ``CaseRecord`` persistido a la entrada del agregado.

    Es el camino para armar el agregado **desde el histórico** (los sidecars de
    T-506) en lugar de desde los resultados de la corrida: sirve para un lote
    procesado en varias sesiones o para reconstruir el agregado de una carpeta
    que ya se procesó.
    """
    resultado = caso.resultado
    hitl = resultado.hitl if resultado is not None else None
    return EntradaDocumento(
        documento_id=caso.documento_id,
        archivo=caso.archivo,
        ok=resultado is not None,
        estado=resultado.estado.value if resultado is not None else None,
        tipo_comprobante=resultado.tipo_comprobante if resultado is not None else None,
        certeza=(
            resultado.certeza.value
            if resultado is not None and resultado.certeza
            else None
        ),
        origen=(
            resultado.origen.value
            if resultado is not None and resultado.origen
            else None
        ),
        hitl_requerido=hitl.requerido if hitl is not None else None,
        hitl_prioridad=hitl.prioridad if hitl is not None else None,
        campos_extraidos=(
            len(resultado.campos_extraidos) if resultado is not None else 0
        ),
        clasificacion_contable=(
            resultado.clasificacion_contable is not None if resultado is not None else False
        ),
        etapas=tuple(etapa.etapa for etapa in caso.etapas),
        sidecar=sidecar_para(caso.documento_id),
    )


@dataclass
class Agregado:
    """El agregado del lote (T-603): la síntesis + una entrada por documento.

    Es el contrato del JSON consolidado. Se construye con
    :func:`construir_agregado` (desde resultados o desde el histórico) y se
    persiste con :func:`escribir_agregado` / :func:`agregar_a_archivo`.
    """

    raiz: str | None = None
    documentos: list[EntradaDocumento] = field(default_factory=list)
    metricas: dict[str, Any] | None = None
    lote: dict[str, Any] | None = None
    version: str = VERSION_AGREGADO
    generado: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # -- síntesis --------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.documentos)

    def por_estado(self) -> dict[str, int]:
        """Cuántos documentos hay en cada estado (``sin_estado`` para los que no
        llegaron a un veredicto: un archivo ilegible no tiene estado, y contarlo
        como ``revision`` afirmaría algo que no pasó)."""
        conteo: dict[str, int] = {}
        for documento in self.documentos:
            clave = documento.estado or "sin_estado"
            conteo[clave] = conteo.get(clave, 0) + 1
        return dict(sorted(conteo.items()))

    def resumen(self) -> dict[str, Any]:
        """La síntesis del lote: cuántos, cómo salieron y cuántos van a revisión."""
        errores = [d for d in self.documentos if not d.ok]
        hitl = [d for d in self.documentos if d.hitl_requerido]
        return {
            "documentos": self.total,
            "ok": self.total - len(errores),
            "errores": len(errores),
            "por_estado": self.por_estado(),
            "requieren_revision": len(hitl),
            "revision_obligatoria": sum(
                1 for d in hitl if d.hitl_prioridad == "alta"
            ),
            "con_sidecar": sum(1 for d in self.documentos if d.sidecar),
        }

    def como_dict(self) -> dict[str, Any]:
        datos: dict[str, Any] = {
            "version": self.version,
            "generado": self.generado,
            "raiz": self.raiz,
            "contrato": {
                "schema_version": SCHEMA_VERSION,
                "version_traza": VERSION_TRAZA,
                "version_metricas": VERSION_METRICAS,
                "nota": (
                    "El agregado es un ÍNDICE de la corrida (veredicto + puntero "
                    "al sidecar), no una copia de los CaseRecord: el registro "
                    "auditable completo vive en `<doc>.case.json` (ADR-005). Para "
                    "auditar un caso: `voucherflow case show <documento_id>`."
                ),
            },
            "resumen": self.resumen(),
            "documentos": [d.como_dict() for d in self.documentos],
        }
        if self.metricas is not None:
            datos["metricas"] = self.metricas
        else:
            # Honestidad: si no hay histórico del cual derivarlas, se dice — no se
            # omite en silencio (parecería que el lote no tiene métricas).
            datos["metricas"] = None
            datos["metricas_no_disponibles"] = (
                "Las métricas se calculan sobre los CaseRecord persistidos "
                "(F5/T-506/T-507). Esta corrida no persistió casos (falta "
                "`--cases DIR`), así que no hay de dónde derivarlas: ver "
                "`voucherflow batch --cases DIR`."
            )
        if self.lote is not None:
            datos["lote"] = self.lote
        return datos

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "Agregado":
        return cls(
            raiz=datos.get("raiz"),
            documentos=[
                EntradaDocumento.desde_dict(d) for d in (datos.get("documentos") or [])
            ],
            metricas=datos.get("metricas"),
            lote=datos.get("lote"),
            version=str(datos.get("version") or VERSION_AGREGADO),
            generado=str(datos.get("generado") or ""),
        )


def construir_agregado(
    *,
    resultados: Iterable[Any] | None = None,
    casos: Iterable[CaseRecord] | None = None,
    raiz: str | Path | None = None,
    lote: dict[str, Any] | None = None,
    informe_metricas: bool = True,
) -> Agregado:
    """Arma el agregado del lote desde los resultados **o** desde el histórico.

    Las dos vías existen porque responden a dos momentos distintos:

    - ``resultados``: la corrida que acaba de terminar.
    - ``casos``: el histórico persistido (F5/T-506), para reconstruir el agregado
      de una carpeta procesada en varias sesiones.

    Si se pasan las dos, mandan los ``casos`` (son el dato durable) y los
    ``resultados`` aportan los que no tengan ``CaseRecord``.

    ``informe_metricas`` calcula el bloque ``metricas`` con F5/T-507 sobre los
    ``CaseRecord`` disponibles. Si no hay ninguno, el agregado lo **declara**
    (``metricas_no_disponibles``) en vez de mostrar un número vacío.

    Devuelve un :class:`Agregado` con **una entrada por documento**: un mismo
    ``documento_id`` que aparezca dos veces se colapsa en una sola entrada (la
    última gana), igual que el índice de T-506.
    """
    por_documento: dict[str, EntradaDocumento] = {}
    casos_lista: list[CaseRecord] = []

    for caso in casos or ():
        casos_lista.append(caso)
        por_documento[caso.documento_id] = entrada_de_caso(caso)

    for resultado in resultados or ():
        documento_id = resultado.documento_id or ""
        if documento_id in por_documento:
            continue  # el histórico es el dato durable: no se pisa con la corrida
        por_documento[documento_id] = entrada_de_resultado(resultado)

    agregado = Agregado(
        raiz=str(raiz) if raiz is not None else None,
        documentos=list(por_documento.values()),
        lote=lote,
    )
    if informe_metricas and casos_lista:
        agregado.metricas = metricas_de(casos_lista)
    return agregado


def agregado_del_recorder(
    recorder: CaseRecorder,
    *,
    raiz: str | Path | None = None,
) -> Agregado:
    """Arma el agregado desde un ``CaseRecorder`` (el histórico de T-506).

    Es la vía para reconstruir el agregado de una carpeta ya procesada, sin volver
    a correr nada: se leen los sidecars y se proyectan. Incluye las métricas de
    F5/T-507, porque el histórico es exactamente de donde se derivan.
    """
    return construir_agregado(
        casos=recorder.casos(),
        raiz=raiz if raiz is not None else recorder.dir_salida,
    )


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def _escribir_atomico(destino: Path, contenido: str) -> None:
    """Escribe de forma atómica (mismo patrón que el sidecar y el checkpoint).

    Temporal en el mismo directorio + ``os.replace``: si el proceso muere a mitad,
    el agregado anterior queda intacto. Importa más que en otros archivos: el
    agregado es lo que se mira para saber si el lote terminó.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, destino)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def escribir_agregado(ruta: str | Path, agregado: Agregado) -> Path:
    """Escribe el agregado completo en ``ruta`` (atómico). Devuelve la ruta."""
    destino = Path(ruta)
    _escribir_atomico(
        destino, json.dumps(agregado.como_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    return destino


def leer_agregado(ruta: str | Path) -> Agregado:
    """Lee un agregado del disco.

    Un archivo ilegible **no** se confunde con un lote vacío: lanza
    ``FileNotFoundError`` si no existe y ``ValueError`` si no es un agregado. La
    diferencia importa porque ``agregar_a_archivo`` usa el contenido previo: tratar
    un archivo roto como vacío **borraría** el histórico del lote.
    """
    destino = Path(ruta)
    if not destino.is_file():
        raise FileNotFoundError(f"No existe el agregado: {destino}")
    try:
        datos = json.loads(destino.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"El agregado {destino} no es JSON válido: {exc}. No se sobreescribe "
            "un archivo que no se pudo leer (se perdería el lote anterior)."
        ) from exc
    if not isinstance(datos, dict) or "documentos" not in datos:
        raise ValueError(
            f"El archivo {destino} no tiene la forma de un agregado (falta "
            "'documentos'): no se sobreescribe."
        )
    return Agregado.desde_dict(datos)


def agregar_a_archivo(
    ruta: str | Path,
    *,
    resultados: Iterable[Any] | None = None,
    casos: Iterable[CaseRecord] | None = None,
    raiz: str | Path | None = None,
    lote: dict[str, Any] | None = None,
) -> Agregado:
    """Suma documentos al agregado de ``ruta`` y lo reescribe (T-603).

    Es lo que hace que el agregado sea el **estado de la carpeta** y no el reporte
    de la última corrida: cada corrida suma lo suyo y el mismo documento
    reprocesado **actualiza** su entrada (una entrada por documento, igual que el
    índice de T-506).

    Si el archivo no existe, se crea. Si existe pero está roto, **propaga** el
    error en vez de empezar de cero: sobreescribirlo borraría el lote anterior sin
    avisar.

    Devuelve el agregado resultante (lo que quedó escrito).
    """
    destino = Path(ruta)
    if destino.is_file():
        previo = leer_agregado(destino)
    else:
        previo = Agregado(raiz=str(raiz) if raiz is not None else None)

    por_documento: dict[str, EntradaDocumento] = {
        entrada.documento_id: entrada for entrada in previo.documentos
    }

    casos_lista = list(casos or ())
    for caso in casos_lista:
        por_documento[caso.documento_id] = entrada_de_caso(caso)
    for resultado in resultados or ():
        documento_id = resultado.documento_id or ""
        if any(caso.documento_id == documento_id for caso in casos_lista):
            continue  # ya entró (con más datos) desde el CaseRecord
        entrada = entrada_de_resultado(resultado)
        anterior = por_documento.get(documento_id)
        # Si la entrada previa venía de un sidecar (tiene puntero a la evidencia),
        # no se degrada a la proyección de la corrida: el sidecar es más rico.
        if anterior is not None and anterior.sidecar:
            continue
        por_documento[documento_id] = entrada

    actualizado = Agregado(
        raiz=str(raiz) if raiz is not None else previo.raiz,
        documentos=list(por_documento.values()),
        lote=lote if lote is not None else previo.lote,
    )
    # Las métricas se recalculan **solo si hay casos nuevos**; si no, se hereda lo
    # que ya estaba. Una corrida que no persistió casos no aportó información sobre
    # el lote, así que borrar el cálculo previo sería perder trabajo, no ser honesto
    # (la honestidad, acá, es no inventar métricas cuando nunca hubo datos: eso lo
    # cubre el `metricas_no_disponibles` de `como_dict`).
    if casos_lista:
        actualizado.metricas = metricas_de(casos_lista)
    elif previo.metricas is not None:
        actualizado.metricas = previo.metricas

    escribir_agregado(destino, actualizado)
    return actualizado


__all__ = [
    "VERSION_AGREGADO",
    "NO_AGREGADOS",
    "EntradaDocumento",
    "Agregado",
    "entrada_de_resultado",
    "entrada_de_caso",
    "construir_agregado",
    "agregado_del_recorder",
    "escribir_agregado",
    "leer_agregado",
    "agregar_a_archivo",
]
