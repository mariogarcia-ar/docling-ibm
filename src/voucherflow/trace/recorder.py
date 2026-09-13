"""Módulo ``trace`` — trazabilidad ``CaseRecord`` y persistencia (F5/T-506).

**Fase**: F5 · **Tarea**: T-506 · **Épica**: E-CONC-5 · **ADR-005 / ADR-009**.

F0 congeló el contrato ``CaseRecord`` (``schemas/result.py``) y dejó acá el
esqueleto del registrador. **T-506 lo implementa**: la persistencia como **JSON
sidecar** por documento y un **índice** de una línea por caso para consultar el
histórico sin leer todos los sidecars.

El Gherkin de E-CONC-5 que se implementa acá::

    Dado un caso procesado
    Cuando se consulta su trazabilidad
    Entonces incluye versión de prompt, modelo usado, evidencia de cada fuente,
    reglas disparadas y origen de la decisión (programa | agente_ia | hitl)

    Regla: persistencia
      Dado cualquier caso consolidado
      Cuando se guarda el resultado
      Entonces la trazabilidad se persiste junto al resultado (JSON sidecar o
      store)

Qué se persiste y por qué
-------------------------
Se guardan **dos** cosas, con roles distintos:

1. **El sidecar** (``<documento>.case.json``) — la trazabilidad **completa** del
   caso: el ``CaseRecord`` entero, con la evidencia de cada fuente, las etapas,
   los prompts, los modelos y el resultado. Es el documento que se le muestra a
   un auditor: se abre **un** archivo y está todo.
2. **El índice** (``index.jsonl``) — **una línea por caso** con lo mínimo para
   *encontrar y filtrar* casos (documento, timestamp, quién decidió, estado,
   certeza, tipo, reglas, HITL y el nombre del sidecar). Es lo que permite
   responder "listame los rechazados de agosto" o calcular métricas sin abrir
   miles de sidecars.

Sin el índice, consultar el histórico obliga a leer y parsear **todos** los
sidecars — es exactamente el problema que ADR-009 anticipa. El índice es JSONL
(una línea por caso) y no un solo JSON: agregar un caso es **append** puro, así
que dos procesos que escriben a la vez no se pisan el archivo entero, y una línea
corrupta no invalida el resto del índice. La versión "store SQLite" del ADR-009
llega cuando haga falta consultar por más dimensiones (es de una fase posterior).

Decisiones de diseño (T-506)
----------------------------
1. **Escritura atómica de verdad**: se escribe en un archivo temporal en el
   **mismo directorio** y se hace ``os.replace`` (atómico en POSIX y Windows). Si
   el proceso muere a mitad de la escritura, el sidecar anterior queda intacto —
   nunca se lee un JSON truncado. Este es el patrón de la v1 que el §4 de los ADR
   manda respetar, y la base de la reanudación por checkpoint (F6/T-602).
2. **El índice va *después* del sidecar, y es best-effort.** El sidecar es el
   dato; el índice es un derivado. Si el índice no se puede escribir (disco lleno,
   permisos), el caso **igual quedó persistido** y el registrador lo reporta en
   lugar de tirar el trabajo hecho. Lo contrario (escribir el índice y perder el
   sidecar) dejaría una fila apuntando a un archivo que no existe.
3. **El índice se puede reconstruir desde los sidecars** (``reindexar()``). Como
   el índice es un **derivado**, tiene que poder regenerarse: si alguien borra o
   corrompe ``index.jsonl``, el histórico no se pierde. Además, así se puede
   indexar un directorio que ya tenía sidecars escritos por otra corrida.
4. **Un archivo por caso, no un archivo por lote.** El ``CaseRecord`` es por
   documento (alcance de ADR-005) y el sidecar también: re-procesar un documento
   **sobrescribe su** trazabilidad, no la de los demás. Es lo que hace la
   escritura atómica segura en concurrencia (dos documentos distintos nunca
   comparten archivo).
5. **Los campos del índice se derivan, no se piden.** ``quien_decidio``,
   ``estado``, ``certeza``, ``tipo_comprobante`` y el estado HITL salen del
   ``resultado`` y del ``CaseRecord`` cuando están; si no hay resultado consolidado,
   se indexa el caso igual (con esos campos en ``None``) en lugar de saltarlo: un
   caso sin resultado también es parte del histórico — de hecho es el caso más
   interesante de auditar.
6. **El índice es append-only, pero lógico: una fila por documento.** Agregar un
   caso es un ``append`` (barato y seguro en concurrencia), y la deduplicación se
   hace **al leer**, donde la última fila de cada documento gana. El ``CaseRecord``
   es por documento (ADR-005): re-procesar un documento actualiza **su** entrada,
   no agrega una nueva. Si el índice devolviera una fila por corrida, cualquier
   agregado sobre él (las métricas de T-507) contaría dos veces el mismo caso.
7. **Sin dependencias nuevas**: ``json`` + ``os`` + ``pathlib`` de la stdlib.

Alcance honesto
---------------
- Se persiste lo que las etapas **produjeron**. Si una etapa no expuso su versión
  de prompt o su modelo, el campo viaja vacío: **no se inventa** un hash ni un
  nombre de modelo (sería una trazabilidad falsa, peor que una incompleta).
- La auditoría de "reconstruir el caso desde el sidecar" se puede verificar
  siempre: el sidecar es el ``model_dump()`` del contrato congelado, así que vuelve
  a ``CaseRecord`` sin pérdida.

Referencias: ADR-005 (qué y cómo se persiste), ADR-009 (índice SQLite como fase
posterior), Gherkin E-CONC-5, doc 03 §9/§11 (`TRACE.md`), F5-subplan §3.6.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..schemas.result import CaseRecord

#: Versión del formato de persistencia (sidecar + índice). Va en el sidecar para
#: poder migrar el layout sin confundir archivos viejos con archivos rotos.
VERSION_TRAZA = "trace-sidecar@1"

#: Nombre del archivo de índice dentro del directorio de salida.
NOMBRE_INDICE = "index.jsonl"

#: Campos del índice (una línea por caso). El orden es el de la línea que se
#: escribe, para que el JSONL sea legible a ojo.
CAMPOS_INDICE = (
    "documento_id",
    "timestamp",
    "schema_version",
    "version_traza",
    "archivo",
    "estado",
    "certeza",
    "origen",
    "quien_decidio",
    "tipo_comprobante",
    "hitl_requerido",
    "hitl_prioridad",
    "hitl_estado",
    "reglas_disparadas",
    "n_etapas",
    "sidecar",
)


@dataclass(frozen=True)
class ResultadoPersistencia:
    """Lo que dejó una persistencia (direcciones + la verdad sobre el índice).

    Campos:
        sidecar: ruta del sidecar escrito (la trazabilidad completa).
        indice: ruta del índice.
        indexado: si la fila del índice se pudo escribir. ``False`` **no**
            significa que el caso se perdió: el sidecar está escrito y esto se
            reporta para que el llamador lo sepa en lugar de tirar el trabajo.
    """

    sidecar: Path
    indice: Path
    indexado: bool

    @property
    def ok(self) -> bool:
        """True si el caso quedó persistido por completo (sidecar + índice)."""
        return self.indexado

    def como_dict(self) -> dict[str, Any]:
        return {
            "sidecar": str(self.sidecar),
            "indice": str(self.indice),
            "indexado": self.indexado,
        }


# ---------------------------------------------------------------------------
# Helpers de rutas y de escritura atómica
# ---------------------------------------------------------------------------


def sidecar_para(documento_id: str) -> str:
    """Nombre de archivo sidecar canónico para un documento (helper).

    Patrón heredado de v1 (``_pipeline.json`` / ``.case.json``), útil para
    checkpoints y reanudación (F6/T-602).
    """
    return f"{documento_id}.case.json"


def _nombre_seguro(documento_id: str) -> str:
    """Nombre de archivo seguro para el id de un documento.

    Un ``documento_id`` real es ``sha256:abc…`` (los dos puntos son válidos en
    POSIX pero ilegales en Windows) o el nombre de archivo del documento. No se
    "corrige" el id —se conserva tal cual en el contenido del sidecar— pero el
    **nombre del archivo** se sanea para que el sidecar se pueda escribir en
    cualquier sistema y no genere directorios inesperados.
    """
    saneado = documento_id
    for caracter in ("/", "\\", ":", "\x00"):
        saneado = saneado.replace(caracter, "_")
    saneado = saneado.strip() or "documento"
    # Un id tipo ``..`` no debe resolver a un directorio padre.
    if set(saneado) <= {"."}:
        saneado = "documento"
    return saneado


def _escribir_atomico(destino: Path, contenido: str) -> None:
    """Escribe ``contenido`` en ``destino`` de forma atómica.

    Se escribe a un temporal **en el mismo directorio** (mismo filesystem, que es
    lo que hace atómico al ``os.replace``) y recién después se reemplaza. Si algo
    falla antes del reemplazo, se limpia el temporal y ``destino`` queda como
    estaba: **nunca** se lee un archivo a medio escribir.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())  # que el contenido esté en disco
        os.replace(temporal, destino)
    except BaseException:
        # Se limpia el temporal para no dejar basura si la escritura falló.
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# El registrador
# ---------------------------------------------------------------------------


class CaseRecorder:
    """Registra y persiste la trazabilidad de un caso (``CaseRecord``, ADR-005).

    Implementación de T-506: escribe el **sidecar** (``<documento>.case.json``) y
    una línea en el **índice** (``index.jsonl``). La escritura del sidecar es
    atómica; el índice es un derivado best-effort y se puede reconstruir con
    :meth:`reindexar`.

    Ejemplo::

        recorder = CaseRecorder("salida/cases")
        resultado = recorder.registrar(caso)   # sidecar + índice
        resultado.sidecar                       # .../doc-1.case.json
        recorder.buscar(estado="rechazado")     # consulta sobre el índice
    """

    def __init__(self, dir_salida: str | Path = ".") -> None:
        self.dir_salida = Path(dir_salida)
        self.indice = self.dir_salida / NOMBRE_INDICE

    # -- persistencia ----------------------------------------------------

    def registrar(self, caso: CaseRecord) -> ResultadoPersistencia:
        """Persiste un ``CaseRecord`` como JSON sidecar + una línea de índice.

        Es la implementación de la firma que dejó el esqueleto de F0 (``registrar``
        devolvía un ``Path``): ahora devuelve un ``ResultadoPersistencia`` con la
        ruta del sidecar, la del índice y si el índice se pudo escribir — el
        sidecar es el dato, el índice un derivado, y la diferencia importa.

        El **documento** del sidecar es el ``CaseRecord`` completo (contrato de F0)
        más un bloque ``_persistencia`` con la versión del formato, para que un
        archivo viejo se reconozca como viejo y no como roto.

        Devuelve:
            ``ResultadoPersistencia``. Lanza ``ValueError`` si el caso no tiene
            ``documento_id`` (lo impide el contrato, pero se declara acá).
        """
        if not caso.documento_id or not caso.documento_id.strip():
            raise ValueError("No se persiste un CaseRecord sin documento_id.")

        ruta_sidecar = self.ruta_sidecar(caso.documento_id)
        documento = caso.model_dump(mode="json")
        documento["_persistencia"] = {
            "version": VERSION_TRAZA,
            "sidecar": ruta_sidecar.name,
        }
        _escribir_atomico(
            ruta_sidecar,
            json.dumps(documento, ensure_ascii=False, indent=2, sort_keys=False),
        )

        # El índice va después del sidecar: si fallara, el dato ya está en disco.
        indexado = self._indexar(caso, ruta_sidecar.name)
        return ResultadoPersistencia(
            sidecar=ruta_sidecar, indice=self.indice, indexado=indexado
        )

    def registrar_lote(self, casos: Iterable[CaseRecord]) -> list[ResultadoPersistencia]:
        """Persiste varios casos (uno por sidecar) y devuelve sus resultados.

        El índice se escribe una vez por caso (append), así que el lote no
        reescribe el archivo entero.
        """
        return [self.registrar(caso) for caso in casos]

    # -- rutas -----------------------------------------------------------

    def ruta_sidecar(self, documento_id: str) -> Path:
        """Ruta del sidecar de un documento dentro del directorio de salida."""
        return self.dir_salida / sidecar_para(_nombre_seguro(documento_id))

    def leer(self, documento_id: str) -> CaseRecord:
        """Reconstruye el ``CaseRecord`` de un documento desde su sidecar.

        Es la verificación de auditoría: lo persistido tiene que poder volver al
        contrato congelado **sin pérdida** (el sidecar es el ``model_dump()`` del
        contrato). El bloque ``_persistencia`` es metadata del formato y no forma
        parte del contrato, así que se descarta al reconstruir.

        Lanza ``FileNotFoundError`` si el documento no tiene sidecar.
        """
        ruta = self.ruta_sidecar(documento_id)
        if not ruta.is_file():
            raise FileNotFoundError(
                f"No hay sidecar para {documento_id!r} en {self.dir_salida}. "
                "El caso no se persistió o el id no corresponde."
            )
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        datos.pop("_persistencia", None)
        return CaseRecord.model_validate(datos)

    # -- índice ----------------------------------------------------------

    def fila_indice(self, caso: CaseRecord, sidecar: str | None = None) -> dict[str, Any]:
        """La fila de índice de un caso: lo mínimo para **encontrarlo** y filtrarlo.

        Se **deriva** del ``CaseRecord`` (y de su ``resultado`` cuando existe): no
        se le pide al llamador que repita datos que el contrato ya tiene. Un caso
        sin resultado consolidado se indexa igual —con esos campos en ``None``—
        porque también es parte del histórico (y de los más interesantes de
        auditar).
        """
        resultado = caso.resultado
        hitl = resultado.hitl if resultado is not None else None
        fila: dict[str, Any] = {
            "documento_id": caso.documento_id,
            "timestamp": caso.timestamp,
            "schema_version": caso.schema_version,
            "version_traza": VERSION_TRAZA,
            "archivo": caso.archivo,
            "estado": resultado.estado.value if resultado is not None else None,
            "certeza": (
                resultado.certeza.value
                if resultado is not None and resultado.certeza
                else None
            ),
            "origen": (
                resultado.origen.value
                if resultado is not None and resultado.origen
                else None
            ),
            "quien_decidio": caso.quien_decidio.value if caso.quien_decidio else None,
            "tipo_comprobante": resultado.tipo_comprobante if resultado is not None else None,
            "hitl_requerido": hitl.requerido if hitl is not None else None,
            "hitl_prioridad": hitl.prioridad if hitl is not None else None,
            "hitl_estado": hitl.estado if hitl is not None else None,
            "reglas_disparadas": list(caso.reglas_disparadas),
            "n_etapas": len(caso.etapas),
            "sidecar": sidecar or sidecar_para(_nombre_seguro(caso.documento_id)),
        }
        return {campo: fila[campo] for campo in CAMPOS_INDICE}

    def _indexar(self, caso: CaseRecord, sidecar: str) -> bool:
        """Agrega la fila del caso al índice (best-effort).

        Es un **append**: no reescribe el archivo, así que dos procesos que
        escriben a la vez no se pisan y el costo no crece con el histórico. La
        deduplicación por documento se hace **al leer**
        (:meth:`leer_indice`), donde el índice lógico queda con una fila por
        caso y no una por corrida.

        Devuelve ``False`` si el índice no se pudo escribir. **No** levanta: el
        sidecar ya está persistido y perder el índice no invalida el caso — se
        reporta para que una corrida pueda reconstruirlo con :meth:`reindexar`.
        """
        linea = json.dumps(self.fila_indice(caso, sidecar), ensure_ascii=False)
        try:
            self.dir_salida.mkdir(parents=True, exist_ok=True)
            with self.indice.open("a", encoding="utf-8") as archivo:
                archivo.write(linea + "\n")
        except OSError:
            return False
        return True

    def leer_indice(self, *, unico: bool = True) -> list[dict[str, Any]]:
        """Lee el índice como lista de filas (ignora líneas vacías o corruptas).

        Una línea corrupta **no** invalida el resto del índice: JSONL permite
        saltearla. Se saltea en silencio porque el índice es un derivado y se
        puede reconstruir con :meth:`reindexar` — pero las líneas buenas se
        conservan, así que un índice a medio romper sigue siendo consultable.

        Argumentos:
            unico: si es ``True`` (default) devuelve **una fila por documento** —
                la **última** escrita. El archivo es append-only (barato y
                seguro en concurrencia), así que re-procesar un documento deja
                dos líneas físicas; pero el ``CaseRecord`` es por documento
                (ADR-005), así que el **índice lógico** tiene una sola fila por
                caso. Sin esto, re-procesar inflaría cualquier agregado sobre el
                índice (las métricas de T-507, por ejemplo). Con ``unico=False``
                se devuelven las líneas físicas, que es útil para diagnosticar
                el archivo en sí.
        """
        if not self.indice.is_file():
            return []
        filas: list[dict[str, Any]] = []
        for linea in self.indice.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                filas.append(json.loads(linea))
            except json.JSONDecodeError:
                continue
        if not unico:
            return filas
        # Última aparición gana: el índice refleja el estado más reciente de cada
        # documento, no cada corrida.
        por_documento: dict[str, dict[str, Any]] = {}
        for fila in filas:
            documento = fila.get("documento_id")
            if documento is None:
                continue
            por_documento[documento] = fila
        return list(por_documento.values())

    def buscar(self, **filtros: Any) -> list[dict[str, Any]]:
        """Consulta el índice por igualdad de campos (``estado="rechazado"``).

        Es la consulta que el histórico necesita ("listame los rechazados", "los
        que quedaron en HITL de prioridad alta"). Un campo que no existe en el
        índice no matchea nada — no se inventa una coincidencia. Para consultas
        por más dimensiones, el store SQLite del ADR-009 es la fase siguiente.
        """
        desconocidos = set(filtros) - set(CAMPOS_INDICE)
        if desconocidos:
            raise KeyError(
                f"El índice no tiene esos campos: {sorted(desconocidos)}. "
                f"Consultables: {list(CAMPOS_INDICE)}."
            )
        return [
            fila
            for fila in self.leer_indice()
            if all(fila.get(campo) == valor for campo, valor in filtros.items())
        ]

    def reindexar(self, *, purgar: bool = True) -> list[dict[str, Any]]:
        """Reconstruye el índice desde los **sidecars** del directorio.

        El índice es un derivado: tiene que poder regenerarse sin pérdida (si se
        borra o se corrompe, el histórico no se pierde). Se recorren los sidecars
        en orden de nombre y se reescribe el índice de una vez (atómico).

        Argumentos:
            purgar: si es ``True`` (default) el índice reconstruido contiene
                exactamente los sidecars presentes. Si es ``False``, se conservan
                las filas de documentos que ya no tienen sidecar.

        Devuelve las filas del índice resultante.
        """
        filas: list[dict[str, Any]] = []
        for ruta in self.sidecars():
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # un sidecar ilegible no debe tumbar la reconstrucción
            datos.pop("_persistencia", None)
            try:
                caso = CaseRecord.model_validate(datos)
            except Exception:  # noqa: BLE001 - un sidecar ajeno al contrato se saltea
                continue
            filas.append(self.fila_indice(caso, ruta.name))

        if not purgar:
            presentes = {fila["documento_id"] for fila in filas}
            filas = [
                fila
                for fila in self.leer_indice()
                if fila.get("documento_id") not in presentes
            ] + filas

        contenido = "".join(
            json.dumps(fila, ensure_ascii=False) + "\n" for fila in filas
        )
        self.dir_salida.mkdir(parents=True, exist_ok=True)
        _escribir_atomico(self.indice, contenido)
        return filas

    # -- navegación ------------------------------------------------------

    def sidecars(self) -> list[Path]:
        """Los sidecars presentes en el directorio de salida (orden estable)."""
        return sorted(self.dir_salida.glob("*.case.json"))

    def casos(self) -> list[CaseRecord]:
        """Todos los ``CaseRecord`` persistidos en el directorio (lectura directa).

        A diferencia de :meth:`buscar` (que consulta el índice derivado), esto lee
        los sidecars: es el camino de auditoría cuando no se quiere depender del
        índice.
        """
        casos: list[CaseRecord] = []
        for ruta in self.sidecars():
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            datos.pop("_persistencia", None)
            try:
                casos.append(CaseRecord.model_validate(datos))
            except Exception:  # noqa: BLE001
                continue
        return casos

    def __repr__(self) -> str:  # pragma: no cover - cosmético
        return f"CaseRecorder({str(self.dir_salida)!r})"


__all__ = [
    "VERSION_TRAZA",
    "NOMBRE_INDICE",
    "CAMPOS_INDICE",
    "ResultadoPersistencia",
    "CaseRecorder",
    "sidecar_para",
]
