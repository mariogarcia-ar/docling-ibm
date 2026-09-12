"""Orquestador del pipeline ``voucherflow`` (F6 / T-601).

**Fase**: F6 · **Tarea**: T-601 · **Épica**: E-CLI / E-LIB-1 · **ADR-007**.

F0 dejó el esqueleto (``PipelineOrchestrator.ejecutar`` lanzaba
``NotImplementedError``). **T-601 lo implementa**: el orquestador encadena las
etapas que las fases F1–F5 dejaron implementadas y devuelve el
``PipelineResult`` con el ``VoucherResult`` consolidado, la evidencia combinada
y el ``CaseRecord``. Este módulo no se acopla a scripts de ``v1/``.

Qué encadena (doc 03 §5.1) y qué **no** hace
--------------------------------------------
1. ``processing`` (F1/T-105): documento → ``ProcessedDocument``.
2. ``validation`` (F2/T-203): gate "¿es comprobante?" (doble paso qween). Un
   documento que no pasa el gate se resuelve como ``rechazado`` con certeza
   ``alta`` y origen ``programa`` — es el fast-fail del diseño (doc 03 §4.5): el
   código sí concluyó, y concluyó que no. No se gasta una extracción.
3. ``extraction`` (F4/T-401): los flujos VLM y LLM en paralelo → ``SourceEvidence``
   por fuente, con la vista fiel que dejó el gate (E-QWE-2).
4. ``rules``/``extraction`` (F4/T-404): combinación con resolución por campo.
5. ``conclusion`` (F5/T-501 + T-504 + T-503): pasada 2, escalado al agente (si el
   código no concluyó) y consolidación en el ``VoucherResult``.
6. ``trace`` (F5/T-506): ``CaseRecord`` armado desde los artefactos de la corrida.

La **clasificación contable** (F3/T-304) es opcional en la corrida: la cadena
01→02→03 llama al modelo tres veces, así que se pide explícitamente
(``clasificar_contable=True``) y un fallo de la cadena **no** tumba el caso: se
registra en la traza y el resultado viaja sin clasificación (no se inventa una,
T-503). Es la misma política que v1, que anotaba los errores por paso y seguía.

Determinismo sin red (regla dura del repo)
------------------------------------------
Todas las colaboraciones (lector de modelos, agente, convertidor, buscador) se
**inyectan**. Con dobles, la corrida completa es determinista y sin red: es lo
que permite que la suite default ejercite el pipeline entero sin Ollama ni
Docling. En producción la CLI construye el ``OllamaClient`` real.

Alcance de T-601 (lo que **no** hace)
-------------------------------------
- **No** implementa el modo batch con workers/checkpoints/enfriamiento (T-602):
  ``ejecutar_lote`` descubre y recorre la carpeta de forma **secuencial** y
  determinista, y ``max_workers`` viaja para que T-602 lo use (no se simula un
  paralelismo que no existe: el detalle declara lo aplicado).
- **No** define la política de sidecars ni la salida agregada (T-603): expone el
  ``CaseRecord`` y reutiliza el ``CaseRecorder`` de F5/T-506 cuando se pide
  persistir.
- **No** llama a ARCA por su cuenta: el hook de búsqueda es inyectable y
  desactivado por defecto (ADR-003).
- **No** escribe el ``CaseRecord`` por defecto: persistir es opt-in (``persistir``)
  para que una corrida de inspección no ensucie el directorio.

Referencias: doc 03 §5.1 (secuencia general), §8.1 (CLI), §10 (NFR);
`ORCH-CLI.md` §3; `06-estrategia-calidad.md` §4 "Fase 6 — Cliente"; F6.md §3
T-601; ADR-007.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models.docling import EXTENSIONES_SOPORTADAS, ProcessedDocument
from .schemas.evidence import (
    Certeza,
    CombinedEvidence,
    EstadoResultado,
    Origen,
    SourceEvidence,
)
from .schemas.result import (
    CaseRecord,
    ClasificacionContable,
    HitlDecision,
    VoucherResult,
)
from .settings.config import Settings, cargar_settings

logger = logging.getLogger("voucherflow.orchestrator")

#: Versión del orquestador (T-601). Se registra en la traza del resultado: si
#: cambia el encadenamiento, la auditoría distingue los casos.
VERSION_ORQUESTADOR = "orquestador-pipeline@1"

#: Nombre de cada etapa en ``PipelineResult.etapas_completadas`` (el vocabulario
#: que publica la corrida; coincide con ``RegistroEtapa.etapa`` de F0).
ETAPA_PROCESSING = "processing"
ETAPA_VALIDATION = "validation"
ETAPA_EXTRACTION = "extraction"
ETAPA_COMBINACION = "combinacion"
ETAPA_CONCLUSION = "conclusion"
ETAPA_TRAZABILIDAD = "trace"

#: Artefactos derivados que la búsqueda recursiva **no** debe reprocesar como si
#: fueran documentos de entrada: los escriben las corridas (sidecars, checkpoints
#: de v1) o son salidas ya derivadas del pipeline.
EXCLUIDAS_POR_NOMBRE: tuple[str, ...] = (
    "*.case.json",
    "*_pipeline.json",
    "*_classification.json",
    "*.raw.md",
)


def identificador_de_archivo(origen: str | Path, *, bloque: int = 1 << 20) -> str:
    """``documento_id`` estable de un archivo: sha256 de su contenido (glosario §2).

    Se lee en bloques para no cargar el archivo entero en memoria: un PDF
    escaneado puede pesar decenas de MB. El prefijo ``sha256:`` deja explícito que
    el id es un hash y no la ruta (la ruta viaja aparte en el ``CaseRecord``,
    donde es información referencial y no identidad).
    """
    digest = hashlib.sha256()
    with Path(origen).open("rb") as archivo:
        for fragmento in iter(lambda: archivo.read(bloque), b""):
            digest.update(fragmento)
    return f"sha256:{digest.hexdigest()}"


def iterar_documentos(
    raiz: str | Path,
    *,
    extensiones: Iterable[str] | None = None,
) -> list[Path]:
    """Descubre los documentos de una ruta: un archivo o una carpeta recursiva.

    Es el equivalente v2 del ``find_source_files`` de ``v1/full_pipeline.py``: si
    ``raiz`` es un archivo se devuelve tal cual; si es un directorio se recorren
    las subcarpetas y se devuelven los archivos soportados, **en orden
    determinista** (el resultado no debe depender del orden del filesystem: una
    corrida reproducible es un requisito de auditoría).

    Se excluyen los artefactos **derivados** (``EXCLUIDAS_POR_NOMBRE``): los
    sidecars y checkpoints que escriben las corridas no son documentos de entrada.
    Una carpeta de ``files/`` con salidas de v1 dentro no debe reprocesar sus
    propios sidecars.

    Argumentos:
        raiz: archivo o directorio a recorrer.
        extensiones: extensiones a considerar (default:
            ``models.docling.EXTENSIONES_SOPORTADAS``).

    Devuelve:
        Lista de ``Path`` ordenada. Si la raíz no existe, devuelve una lista vacía
        (el llamador decide cómo reportarlo: la CLI lo trata como error y sale con
        código ≠ 0).
    """
    ruta = Path(raiz).expanduser()
    if not ruta.exists():
        return []

    permitidas = {
        (ext.lower() if ext.startswith(".") else f".{ext.lower()}")
        for ext in (extensiones if extensiones is not None else EXTENSIONES_SOPORTADAS)
    }

    if ruta.is_file():
        return [ruta] if ruta.suffix.lower() in permitidas else []

    encontrados: set[Path] = set()
    for extension in sorted(permitidas):
        for archivo in ruta.rglob(f"*{extension}"):
            if not archivo.is_file():
                continue
            if any(archivo.match(patron) for patron in EXCLUIDAS_POR_NOMBRE):
                continue
            encontrados.add(archivo)
    return sorted(encontrados)


@dataclass
class PipelineResult:
    """Resultado de una corrida del pipeline (resumen para el cliente).

    Es el contrato de salida que F0 esbozó (``documento_id``/``ok``/
    ``etapas_completadas``/``resumen``/``error``) y que T-601 **puebla**. Los
    campos agregados en T-601 son aditivos (todos con default): el contrato de F0
    sigue siendo construible tal cual.

    Campos:
        documento_id: id del documento (``sha256`` del archivo).
        ok: ``True`` si la corrida terminó con un ``VoucherResult`` (incluido un
            rechazo firme: rechazar es concluir). ``False`` solo si hubo un error
            no resuelto (archivo inexistente, formato no soportado…).
        etapas_completadas: etapas efectivamente corridas, en orden.
        resumen: dict corto con estado/certeza/origen/letra (para reportes).
        error: mensaje del error no resuelto, si lo hubo.
        archivo: ruta del documento de origen (referencial).
        resultado: el ``VoucherResult`` consolidado (F5/T-503), si se concluyó.
        evidencia: la ``CombinedEvidence`` final (F4/T-404 + F5).
        caso: el ``CaseRecord`` armado (F5/T-506), listo para persistir.
        detalle: trazabilidad de la corrida (gate, contable, orientación…).
    """

    documento_id: str | None = None
    ok: bool = False
    etapas_completadas: list[str] = field(default_factory=list)
    resumen: dict = field(default_factory=dict)
    error: str | None = None
    # --- T-601 (aditivo) ---
    archivo: str | None = None
    resultado: VoucherResult | None = None
    evidencia: CombinedEvidence | None = None
    caso: CaseRecord | None = None
    detalle: dict = field(default_factory=dict)

    @property
    def estado(self) -> str | None:
        """Estado consolidado del caso (``aprobado``/``rechazado``/``revision``)."""
        return self.resultado.estado.value if self.resultado is not None else None

    def como_dict(self) -> dict[str, Any]:
        """Vista serializable de la corrida (la que publica la CLI)."""
        return {
            "version": VERSION_ORQUESTADOR,
            "documento_id": self.documento_id,
            "archivo": self.archivo,
            "ok": self.ok,
            "estado": self.estado,
            "error": self.error,
            "etapas_completadas": list(self.etapas_completadas),
            "resumen": dict(self.resumen),
            "detalle": dict(self.detalle),
            "resultado": (
                self.resultado.model_dump(mode="json")
                if self.resultado is not None
                else None
            ),
        }


class PipelineOrchestrator:
    """Orquesta las etapas del pipeline para un documento o un lote (T-601).

    El orquestador **no** conoce la lógica de los módulos: los invoca por su
    interfaz pública (regla dura de F0, ``ORCH-CLI.md`` §3). Las colaboraciones se
    inyectan para que la suite default corra sin Ollama ni Docling.

    Ejemplo::

        orch = PipelineOrchestrator(cliente=doble, converter=doble)
        resultado = orch.ejecutar("factura.jpg")
        resultado.resultado.estado          # aprobado | rechazado | revision
    """

    def __init__(
        self,
        *,
        cliente: Any = None,
        agente: Any = None,
        converter: Any = None,
        buscador: Any = None,
        settings: Settings | None = None,
        cola: Any = None,
    ) -> None:
        """
        Argumentos:
            cliente: el lector de modelos (``OllamaClient`` en producción; un doble
                en la suite). ``None`` construye el real de forma diferida al
                ejecutar (así importar el orquestador no toca la red).
            agente: el agente IA de la conclusión
                (``AgenteOllama(OllamaClient())`` en producción). ``None`` deja el
                caso ambiguo **sin** escalar: el pipeline queda determinista.
            converter: el convertidor Docling de F1 (inyectable en tests).
            buscador: el hook de evidencia adicional (``ArcaClient``); ``None`` lo
                deja desactivado (ADR-003: no bloquea el MVP).
            settings: ``Settings`` de la corrida (modelos por rol, HITL…).
            cola: la ``ColaHitl`` en memoria donde registrar los casos que van a
                revisión (T-505). ``None`` calcula la decisión sin registrarla.
        """
        self.cliente = cliente
        self.agente = agente
        self.converter = converter
        self.buscador = buscador
        self.settings = settings
        self.cola = cola
        self._etapas: list[str] = []

    # ------------------------------------------------------------------
    # Registro de etapas (contrato de F0, se conserva)
    # ------------------------------------------------------------------

    def registrar_etapa(self, nombre: str) -> None:
        """Registra una etapa en el orden de ejecución (contrato de F0)."""
        if nombre not in self._etapas:
            self._etapas.append(nombre)

    @property
    def etapas(self) -> list[str]:
        """Etapas registradas, en orden (contrato de F0)."""
        return list(self._etapas)

    # ------------------------------------------------------------------
    # Etapas sueltas (cada una delega en el módulo de su fase)
    # ------------------------------------------------------------------

    def _settings(self) -> Settings:
        """``Settings`` de la corrida (carga diferida la primera vez)."""
        if self.settings is None:
            self.settings = cargar_settings()
        return self.settings

    def settings_efectivos(self) -> Settings:
        """``Settings`` con los que la corrida resuelve modelos y políticas.

        Es la vía **pública** para que un consumidor (la fachada ``api.ask``, la
        CLI) consulte los roles configurados sin depender del atributo privado ni
        volver a cargar la configuración por su cuenta (que daría otra instancia
        con otros defaults).
        """
        return self._settings()

    def _cliente(self) -> Any:
        """Lector de modelos (``OllamaClient`` real si no se inyectó uno)."""
        if self.cliente is None:
            from .models.ollama import OllamaClient

            self.cliente = OllamaClient()
        return self.cliente

    def procesar(
        self,
        origen: str | Path,
        *,
        docling_raw: bool = False,
        modo_motor: str = "auto",
        orientation: str = "auto",
    ) -> tuple[ProcessedDocument, dict[str, Any]]:
        """Etapa ``processing`` (F1/T-105): documento → ``ProcessedDocument``.

        ``orientation`` (equivalente al ``--orientation`` de la CLI y de
        ``v1/run.py``): ``auto`` conserva la política combinada del exportador
        (E-DOC-3, la dominante). ``horizontal``/``vertical`` **re-exporta** el texto
        de los ``Box`` filtrando por esa orientación — es una decisión de la
        corrida, no del contrato de F1 (``ProcessedDocument`` no la conoce), así
        que se aplica acá y se deja anotada en el detalle.

        Devuelve ``(documento, detalle)``. Lanza ``FileNotFoundError`` /
        ``DocumentoNoProcesableError`` (F1) tal cual: el llamador decide.
        """
        from .processing.orquestacion import procesar_documento

        documento = procesar_documento(
            origen,
            converter=self.converter,
            modo_motor=modo_motor,
            docling_raw=docling_raw,
        )
        detalle: dict[str, Any] = {
            "tipo_entrada": documento.tipo_entrada,
            "orientacion": documento.orientacion,
            "motor": documento.motor,
            "n_items": documento.n_items,
            "docling_raw": docling_raw,
        }
        if orientation != "auto":
            aplicado, motivo = _aplicar_orientacion(documento, orientation)
            detalle["orientacion_solicitada"] = orientation
            detalle["orientacion_aplicada"] = aplicado
            detalle["orientacion_motivo"] = motivo
        return documento, detalle

    def validar(
        self,
        documento: ProcessedDocument,
        *,
        modelo: str | None = None,
    ) -> Any:
        """Etapa ``validation`` (F2/T-203): gate "¿es comprobante?" doble paso.

        Recibe el ``ProcessedDocument`` **ya procesado** para no repetir la
        conversión (``validar_y_procesar`` acepta el contrato de F1 como entrada).
        Devuelve el ``ResultadoValidacion``: veredicto final, las pasadas y —si es
        comprobante— la **vista fiel** que consume la extracción (E-QWE-2).
        """
        from .validation import validar_y_procesar

        return validar_y_procesar(
            documento,
            self._cliente(),
            modelo=modelo,
            settings=self._settings(),
            converter=self.converter,
        )

    def extraer(
        self,
        documento: ProcessedDocument,
        vista: Any = None,
        *,
        documento_id: str | None = None,
        modelo: str | None = None,
        fuentes: Iterable[str] | None = None,
        normalizar: bool = True,
    ) -> Any:
        """Etapa ``extraction`` (F4/T-401): flujos VLM+LLM en paralelo.

        Devuelve la ``ExtraccionEvidencia`` (una ``SourceEvidence`` por fuente,
        **sin colapsar**: ADR-001). El insumo de la fuente textual es el
        ``markdown`` de F1; el de la visual, la **vista fiel** que dejó el gate.
        """
        from .extraction.flows import extraer as _extraer

        kwargs: dict[str, Any] = {}
        if fuentes is not None:
            kwargs["fuentes"] = fuentes
        return _extraer(
            self._cliente(),
            markdown=documento.markdown,
            vista=vista,
            documento_id=documento_id or "",
            modelo=modelo,
            settings=self._settings(),
            normalizar=normalizar,
            **kwargs,
        )

    def combinar(
        self, documento_id: str, fuentes: Iterable[SourceEvidence]
    ) -> CombinedEvidence:
        """Etapa de combinación (F4/T-404): resolución por campo (ADR-002)."""
        from .extraction.flows import combinar_evidencia

        return combinar_evidencia(documento_id, list(fuentes))

    def concluir(
        self,
        evidencia: CombinedEvidence,
        *,
        contexto_tipo: Any = None,
        clasificacion: ClasificacionContable | None = None,
        modelo: str | None = None,
        escalar: bool = True,
    ) -> Any:
        """Etapa ``conclusion`` (F5/T-501 + T-504 + T-503).

        Corre la pasada 2, escala al agente **solo si el código no concluyó** y
        consolida el ``VoucherResult``. Con ``escalar=False`` (o sin agente
        inyectado) el caso ambiguo queda en revisión sin llamar al modelo:
        ``concluir_con_agente`` ya trata ``agente=None`` como "no escalar".
        """
        from .conclusion.engine import concluir_con_agente

        return concluir_con_agente(
            evidencia,
            contexto_tipo=contexto_tipo,
            agente=self.agente if escalar else None,
            modelo=modelo,
            clasificacion=clasificacion,
        )

    def clasificar_contable(
        self,
        markdown: str,
        *,
        condicion_impositiva: str | None = None,
        documento: str | Path | None = None,
        modelo: str | None = None,
    ) -> tuple[ClasificacionContable | None, dict[str, Any]]:
        """Cadena contable 01→02→03 (F3/T-304), **opcional** en la corrida.

        Devuelve ``(clasificacion, detalle)``. Un fallo de la cadena **no**
        propaga la excepción: se registra en el detalle y la clasificación vuelve
        ``None``. La justificación es la de v1 (``full_pipeline`` anotaba los
        errores por paso y seguía) y la de F5/T-503 ("un caso sin clasificar no es
        un caso mal clasificado"): tumbar el caso entero porque el paso contable
        falló sería perder el veredicto que ya se tiene.
        """
        from .classification.contable import ErrorCadenaContable, ejecutar_cadena
        from .classification.prompts_contable import CONDICION_IMPOSITIVA_DEFAULT

        condicion = condicion_impositiva or CONDICION_IMPOSITIVA_DEFAULT
        try:
            resultado = ejecutar_cadena(
                self._cliente(),
                descripcion=markdown,
                condicion_impositiva=condicion,
                documento=documento,
                modelo=modelo,
                settings=self._settings(),
            )
        except ErrorCadenaContable as exc:
            return None, {
                "ok": False,
                "condicion_impositiva": condicion,
                "error": str(exc),
                "pasos": dict(getattr(exc, "pasos", {}) or {}),
                "nota": (
                    "La cadena contable falló y el caso sigue sin clasificación "
                    "(no se inventa una): el veredicto ya obtenido no se descarta "
                    "por un error de una etapa posterior (F3/T-304 + T-503)."
                ),
            }
        return (
            ClasificacionContable(**resultado.como_clasificacion()),
            {
                "ok": True,
                "condicion_impositiva": condicion,
                "reglas_aplicadas": list(resultado.reglas_aplicadas),
                "requiere_revision_humana": resultado.requiere_revision_humana,
                "detalle": dict(resultado.detalle),
            },
        )

    # ------------------------------------------------------------------
    # La corrida completa
    # ------------------------------------------------------------------

    def ejecutar(
        self,
        origen: str | Path,
        *,
        documento_id: str | None = None,
        condicion_impositiva: str | None = None,
        modelo: str | None = None,
        orientation: str = "auto",
        docling_raw: bool = False,
        usar_gate: bool = True,
        clasificar_contable: bool = False,
        escalar: bool = True,
        persistir: bool = False,
        dir_salida: str | Path | None = None,
    ) -> PipelineResult:
        """Corre el pipeline completo sobre **un** documento (F6/T-601).

        Secuencia (doc 03 §5.1): processing → validation → extraction →
        combinación → conclusión (pasada 2 + agente + consolidación) →
        trazabilidad, con la clasificación contable opcional.

        El documento **no se pierde nunca**: un gate negativo se resuelve como
        ``rechazado`` con certeza alta (fast-fail) y un archivo ilegible se
        resuelve con ``ok=False`` y el error declarado. Un caso rechazado es una
        conclusión (T-503), no un fallo del orquestador.

        Argumentos:
            origen: ruta del documento (pdf/imagen/office/texto).
            documento_id: identidad del documento ya calculada por el llamador.
                El runner de lotes (F6/T-602) lo precalcula **una vez** por
                documento y lo pasa al worker, para que el hash no se calcule dos
                veces ni pueda discrepar entre el padre y el proceso del worker. Si
                es ``None`` (default) se calcula acá desde el contenido.
            condicion_impositiva: ``21`` (default) | ``10_5`` | ``27`` | ``2_5`` |
                ``exento_no_gravado`` para la cadena contable.
            modelo: modelo a usar en las etapas que hablan con el modelo (si
                ``None``, el rol de cada etapa de ``Settings``).
            orientation: ``auto`` | ``horizontal`` | ``vertical`` (``--orientation``).
            docling_raw: si ``True``, el markdown es el crudo de Docling
                (equivalente a ``v1/run.py`` sin reordenar; ver T-105/ORQ).
            usar_gate: si ``True`` (default), corre el gate de F2 antes de extraer.
                Desactivarlo **no** evita el fast-fail: el caso sin comprobante se
                resolverá más adelante por sus propias reglas.
            clasificar_contable: si ``True``, corre la cadena 01→02→03 (tres
                llamadas al modelo). Default ``False``: es opt-in por costo y
                porque el veredicto del comprobante no depende de ella.
            escalar: si ``True``, un caso ambiguo escala al agente (si hay agente).
                Con ``False`` queda en revisión: corrida determinista.
            persistir: si ``True``, persiste el ``CaseRecord`` (sidecar + índice) en
                ``dir_salida`` usando el ``CaseRecorder`` de F5/T-506.
            dir_salida: directorio de persistencia (default ``.``).

        Devuelve:
            :class:`PipelineResult` con el ``VoucherResult``, la evidencia, el
            ``CaseRecord`` y la traza de la corrida.
        """
        from .api import DocumentoNoProcesableError

        ruta = Path(origen)
        etapas: list[str] = []
        detalle: dict[str, Any] = {"version": VERSION_ORQUESTADOR}

        # --- 0. Identidad del documento (hash del archivo) ----------------
        if documento_id is not None:
            # El llamador (el runner de lotes) ya lo calculó: se respeta para que
            # el hash no se compute dos veces ni discrepe entre el padre y el
            # worker (T-602).
            identificador = documento_id
        else:
            try:
                identificador = identificador_de_archivo(ruta)
            except OSError as exc:
                return PipelineResult(
                    archivo=str(ruta),
                    ok=False,
                    error=f"No se pudo leer el archivo {ruta}: {exc}",
                    detalle=detalle,
                )
        documento_id = identificador

        # --- 1. processing (F1) ------------------------------------------
        try:
            documento, detalle_proc = self.procesar(
                ruta, docling_raw=docling_raw, orientation=orientation
            )
        except (FileNotFoundError, ValueError, DocumentoNoProcesableError) as exc:
            logger.warning("Documento no procesable %s: %s", ruta, exc)
            return PipelineResult(
                documento_id=documento_id,
                archivo=str(ruta),
                ok=False,
                error=str(exc),
                detalle={**detalle, "processing": {"error": str(exc)}},
            )
        etapas.append(ETAPA_PROCESSING)
        detalle["processing"] = detalle_proc

        # --- 2. validation (F2) ------------------------------------------
        vista_fiel: Any = None
        gate: Any = None
        if usar_gate:
            gate = self.validar(documento, modelo=modelo)
            etapas.append(ETAPA_VALIDATION)
            pasadas = [
                {
                    "vista": pasada.vista_usada,
                    "veredicto": pasada.veredicto.value,
                    "confianza": pasada.confianza_fuente,
                }
                for pasada in gate.pasadas
            ]
            detalle["validation"] = {
                "veredicto_final": gate.veredicto_final.value,
                "pasadas": pasadas,
                "vista_fiel_preparada": gate.vista_fiel is not None,
            }
            vista_fiel = gate.vista_fiel

            if gate.veredicto_final.value != "comprobante":
                return self._rechazo_por_gate(
                    documento_id=documento_id,
                    ruta=ruta,
                    gate=gate,
                    etapas=etapas,
                    detalle=detalle,
                    persistir=persistir,
                    dir_salida=dir_salida,
                )

        # --- 3. extraction (F4) ------------------------------------------
        extraccion = self.extraer(
            documento, vista=vista_fiel, documento_id=documento_id, modelo=modelo
        )
        etapas.append(ETAPA_EXTRACTION)
        detalle["extraction"] = {
            "fuentes": list(extraccion.resultados),
            "fallos": dict(extraccion.fallos),
            "debilidades": list(extraccion.debilidades),
            "detalle": dict(extraccion.detalle),
        }

        # --- 4. combinación por campo (F4/T-404) --------------------------
        evidencia = self.combinar(documento_id, extraccion.evidencias)
        etapas.append(ETAPA_COMBINACION)

        # --- 5. clasificación contable opcional (F3/T-304) ----------------
        clasificacion: ClasificacionContable | None = None
        if clasificar_contable:
            clasificacion, detalle["clasificacion_contable"] = self.clasificar_contable(
                documento.markdown,
                condicion_impositiva=condicion_impositiva,
                documento=ruta,
                modelo=modelo,
            )

        # --- 6. conclusión (F5/T-501 + T-504 + T-503) ---------------------
        conclusion = self.concluir(
            evidencia, clasificacion=clasificacion, modelo=modelo, escalar=escalar
        )
        etapas.append(ETAPA_CONCLUSION)
        evidencia_final = conclusion.evidencia
        resultado = conclusion.resultado
        detalle["conclusion"] = conclusion.como_dict()

        # La política HITL (T-505) materializa la expectativa del veredicto. La
        # cola es en memoria; el store durable es el sidecar/índice de T-506.
        from .conclusion.hitl import encolar_hitl

        resultado = encolar_hitl(resultado, cola=self.cola, settings=self._settings())

        # --- 7. trazabilidad (F5/T-506) -----------------------------------
        from .trace.construccion import construir_case_record

        caso = construir_case_record(
            evidencia_final,
            resultado=resultado,
            archivo=str(ruta),
            fuentes=extraccion.evidencias,
        )
        etapas.append(ETAPA_TRAZABILIDAD)

        persistencia: dict[str, Any] | None = None
        if persistir:
            persistencia = self._persistir(caso, dir_salida)

        return PipelineResult(
            documento_id=documento_id,
            archivo=str(ruta),
            ok=True,
            etapas_completadas=etapas,
            resumen=_resumen(resultado),
            resultado=resultado,
            evidencia=evidencia_final,
            caso=caso,
            detalle={**detalle, "persistencia": persistencia},
        )

    # ------------------------------------------------------------------
    # Piezas internas
    # ------------------------------------------------------------------

    def _rechazo_por_gate(
        self,
        *,
        documento_id: str,
        ruta: Path,
        gate: Any,
        etapas: list[str],
        detalle: dict[str, Any],
        persistir: bool,
        dir_salida: str | Path | None,
    ) -> PipelineResult:
        """Resuelve el fast-fail del gate: ``rechazado``, certeza alta (T-601).

        Es el primer fast-fail del diseño (doc 03 §4.5 y F5-subplan §3.1): el
        documento **no es un comprobante**, así que no hay nada que extraer ni
        concluir. El código sí concluyó —y concluyó que no—, de modo que la certeza
        es ``alta`` y el origen ``programa`` (glosario §2): es la misma regla que
        aplica T-503 a un rechazo firme.

        No se extrae ni se combina: fabricar una evidencia vacía para "poder"
        concluir sería inventar el insumo de una decisión. El ``VoucherResult`` se
        arma con la traza del gate, que es la evidencia real de este desenlace.
        """
        veredicto = gate.veredicto_final.value
        motivo = (
            f"El documento no superó el gate de procesabilidad (F2/T-203): veredicto "
            f"final {veredicto!r}. No es un comprobante, así que no hay extracción "
            "ni conclusión que hacer (fast-fail del diseño, doc 03 §4.5)."
        )
        resultado = VoucherResult(
            documento_id=documento_id,
            estado=EstadoResultado.rechazado,
            tipo_comprobante=None,
            certeza=Certeza.alta,
            origen=Origen.programa,
            campos_extraidos={},
            hitl=HitlDecision(requerido=False, prioridad="baja", estado="no_aplica"),
            trazabilidad={
                "etapa": VERSION_ORQUESTADOR,
                "gate": dict(detalle.get("validation") or {}),
                "motivo": motivo,
                "nota": (
                    "Fast-fail por gate T-601: la certeza es alta porque el código "
                    "concluyó (y concluyó que no). Un rechazo firme es una "
                    "conclusión, no una duda (T-503)."
                ),
            },
        )
        detalle["gate_rechazo"] = {"veredicto": veredicto, "motivo": motivo}

        # El CaseRecord también existe para un rechazo: es el caso más interesante
        # de auditar (T-506). Se arma con la evidencia del gate (la única que hay)
        # para no inventar campos de extracción.
        caso: CaseRecord | None = None
        etapas_finales = list(etapas)
        try:
            fuentes = _fuentes_del_gate(gate)
            evidencia_gate = (
                self.combinar(documento_id, fuentes)
                if fuentes
                else CombinedEvidence(
                    documento_id=documento_id,
                    campos={},
                    trazabilidad={
                        "etapa": "gate",
                        "nota": (
                            "Rechazo por gate (T-601): el documento no es un "
                            "comprobante, así que no hay evidencia de extracción."
                        ),
                    },
                )
            )
            from .trace.construccion import construir_case_record

            caso = construir_case_record(
                evidencia_gate,
                resultado=resultado,
                archivo=str(ruta),
                fuentes=fuentes or None,
            )
            etapas_finales.append(ETAPA_TRAZABILIDAD)
        except Exception as exc:  # noqa: BLE001 - el rechazo no se pierde por la traza
            logger.warning("No se pudo armar el CaseRecord del rechazo: %s", exc)

        persistencia: dict[str, Any] | None = None
        if persistir and caso is not None:
            persistencia = self._persistir(caso, dir_salida)

        return PipelineResult(
            documento_id=documento_id,
            archivo=str(ruta),
            ok=True,
            etapas_completadas=etapas_finales,
            resumen=_resumen(resultado),
            resultado=resultado,
            evidencia=None,
            caso=caso,
            detalle={**detalle, "persistencia": persistencia},
        )

    def _persistir(
        self, caso: CaseRecord, dir_salida: str | Path | None
    ) -> dict[str, Any]:
        """Persiste el ``CaseRecord`` con el ``CaseRecorder`` de F5/T-506.

        Reutiliza la persistencia ya construida (sidecar atómico + índice) en lugar
        de reimplementar escritura de archivos. La política de sidecars y la salida
        agregada del lote son de T-603: acá solo se ofrece el gancho.
        """
        from .trace.recorder import CaseRecorder

        recorder = CaseRecorder(dir_salida or ".")
        persistencia = recorder.registrar(caso)
        return {"ok": persistencia.indexado, **persistencia.como_dict()}

    def ejecutar_lote(
        self,
        raiz: str | Path,
        *,
        max_workers: int = 1,
        force: bool = False,
        persistir: bool = False,
        dir_salida: str | Path | None = None,
        cooling: Any = None,
        ejecutor: Any = None,
        reloj: Any = None,
        **kwargs: Any,
    ) -> list[PipelineResult]:
        """Corre el lote con workers, checkpoints y enfriamiento (F6/T-602).

        Delega en :func:`voucherflow.batch.ejecutar_lote`, que implementa el DoD de
        T-602 (ADR-010): cada worker con **su** convertidor de Docling,
        reanudación por checkpoint (``<doc>.batch.json``, con el **hash del
        contenido** para que un documento cambiado no se saltee) y la política de
        enfriamiento —cuya cuenta arranca cuando el pool está detenido, es decir
        cuando **todos** los workers pararon.

        Devuelve la lista de ``PipelineResult`` **en orden de descubrimiento** (los
        reanudados no producen resultado: no se procesan). La traza completa del
        lote queda en ``detalle['lote']`` de cada resultado y se puede leer con
        :func:`voucherflow.batch.traza_del_lote`.
        """
        from .batch import ejecutar_lote as _ejecutar_lote

        return _ejecutar_lote(
            raiz,
            orquestador=self,
            max_workers=max_workers,
            force=force,
            persistir=persistir,
            dir_salida=dir_salida,
            cooling=cooling,
            ejecutor=ejecutor,
            reloj=reloj,
            settings=self.settings,
            **kwargs,
        ).resultados


def _fuentes_del_gate(gate: Any) -> list[SourceEvidence]:
    """Reconstruye las ``SourceEvidence`` que el gate dejó en su detalle (T-202).

    T-202 publica la evidencia serializada en ``detalle['evidencia']`` y es
    reconstruible con ``SourceEvidence.model_validate`` (contrato explícito del
    módulo). Una evidencia que no valide se **omite**: no se inventa la que falta.
    """
    fuentes: list[SourceEvidence] = []
    for pasada in getattr(gate, "pasadas", []) or []:
        crudo = (getattr(pasada, "detalle", None) or {}).get("evidencia")
        if not crudo:
            continue
        try:
            fuentes.append(SourceEvidence.model_validate(crudo))
        except Exception:  # noqa: BLE001 - evidencia ilegible: se omite, no se inventa
            continue
    return fuentes


def _aplicar_orientacion(
    documento: ProcessedDocument, orientation: str
) -> tuple[str, str]:
    """Re-exporta el markdown por la orientación pedida (``--orientation``).

    Devuelve ``(orientacion_aplicada, motivo)``. Solo aplica cuando el documento
    trae ``Box`` (imágenes y PDFs escaneados): en un documento de texto nativo no
    hay boxes que filtrar y la orientación no tiene sentido (``auto`` es el único
    valor honesto). Un valor fuera del vocabulario no revienta la corrida: se
    declara el motivo y se conserva el markdown.
    """
    from .processing.markdown_exporter import exportar_por_posicion
    from .processing.orientation import ORIENTACIONES_VALIDAS

    if orientation not in ORIENTACIONES_VALIDAS:
        return (
            documento.orientacion,
            f"Orientación {orientation!r} fuera del vocabulario "
            f"{sorted(ORIENTACIONES_VALIDAS)}: se conservó el markdown de F1.",
        )
    if not documento.boxes:
        return (
            documento.orientacion,
            "El documento no trae boxes (texto nativo): la orientación no aplica y "
            "se conserva el markdown de F1.",
        )
    documento.markdown = exportar_por_posicion(documento.boxes, orientation)
    documento.orientacion = orientation
    return orientation, f"Markdown re-exportado filtrando la orientación {orientation!r}."


def _resumen(resultado: VoucherResult) -> dict[str, Any]:
    """Resumen corto del ``VoucherResult`` para el reporte de la corrida."""
    return {
        "estado": resultado.estado.value,
        "tipo_comprobante": resultado.tipo_comprobante,
        "certeza": resultado.certeza.value if resultado.certeza else None,
        "origen": resultado.origen.value if resultado.origen else None,
        "campos_extraidos": len(resultado.campos_extraidos),
        "hitl_requerido": resultado.hitl.requerido,
        "clasificacion_contable": resultado.clasificacion_contable is not None,
    }


__all__ = [
    "VERSION_ORQUESTADOR",
    "ETAPA_PROCESSING",
    "ETAPA_VALIDATION",
    "ETAPA_EXTRACTION",
    "ETAPA_COMBINACION",
    "ETAPA_CONCLUSION",
    "ETAPA_TRAZABILIDAD",
    "EXCLUIDAS_POR_NOMBRE",
    "identificador_de_archivo",
    "iterar_documentos",
    "PipelineResult",
    "PipelineOrchestrator",
]


__all__ = ["PipelineResult", "PipelineOrchestrator"]
