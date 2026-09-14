"""El contrato que separa «lo que hace un LLM» de «cómo lo hace este proveedor».

Todos los proveedores que nos interesan hablan el protocolo de Chat Completions
de OpenAI: OpenAI, DeepSeek (cambiando ``base_url``) y Gemini (por su capa de
compatibilidad). Eso significa que la **forma de la llamada es común** y lo que
cambia es un puñado de capacidades.

La tentación es escribir el núcleo asumiendo OpenAI y después meter ``if`` por
proveedor. Este módulo hace lo contrario: cada proveedor **declara** sus
capacidades y el núcleo se adapta a lo declarado. Así, cuando una capacidad no
está, el sistema lo **sabe** y puede compensar (validar localmente, reintentar,
declarar la limitación) en vez de fallar de forma críptica o —peor— fingir que
funcionó.

Las tres capacidades que hoy obligan a compensar algo:

* **Esquema estricto**: OpenAI lo impone en el servidor; DeepSeek no lo soporta,
  así que hay que validar localmente y reintentar con el error como feedback.
  Sin saberlo, el modelo devuelve formas que el consumidor no espera.
* **Temperatura**: los modelos de razonamiento la **ignoran**. Mandarla y creer
  que tuvo efecto hace inauditable el ajuste del prompt.
* **Esfuerzo de razonamiento**: el eje existe en todas, pero los valores válidos
  cambian por proveedor.

Lo que este módulo **no** hace: hablar con la red. Los adaptadores construyen los
parámetros de la llamada y clasifican la respuesta; quien la ejecuta es el
cliente del SDK, que en los tests es un doble.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: Clave del uso normalizado con los tokens de entrada servidos desde el caché
#: del proveedor. Es el **nombre del campo del SDK** (``usage.prompt_cache_hit_tokens``)
#: a propósito: el uso viaja de ``leer_respuesta`` al registro, al costo y al
#: reporte, y los dos extremos tienen que hablar del mismo campo.
#:
#: ⚠️ No es cosmético. El adaptador emitía ``cache_hit_tokens`` (nombre propio) y
#: ``corrida.py`` leía ``prompt_cache_hit_tokens`` (el del proveedor): al no
#: coincidir nunca, el descuento de la caché no se aplicaba **jamás** y el gasto
#: se reportaba ~3,2x de más. Como el ``dict`` no tiene forma, nada lo detectaba:
#: cada mitad estaba testeada y la costura no.
CLAVE_CACHE_HIT = "prompt_cache_hit_tokens"

#: Ídem para la parte de la entrada que **no** salió de caché (se cobra al precio
#: lleno). Se lee solo para el reporte.
CLAVE_CACHE_MISS = "prompt_cache_miss_tokens"

#: El proveedor cobra un número **fijo** de tokens por imagen, sin mirar su
#: tamaño (DeepSeek redimensiona a ~1300×1300 y agranda las chicas).
ESTRATEGIA_TOPE_FIJO = "tope_fijo"

#: El proveedor cobra por **mosaicos** de 512 px, así que la resolución decide el
#: costo (OpenAI: ``85 + 170 × mosaicos``; ``detail=low`` plano en 85).
ESTRATEGIA_MOSAICOS = "mosaicos"

#: Estrategias válidas. Se valida en :meth:`Capacidades.__post_init__` para que un
#: proveedor nuevo no entre con un valor que nadie sabe interpretar (la
#: estimación caería en el default en silencio).
ESTRATEGIAS_IMAGEN = (ESTRATEGIA_TOPE_FIJO, ESTRATEGIA_MOSAICOS)


@dataclass(frozen=True)
class Capacidades:
    """Lo que un proveedor puede y no puede hacer. Se declara, no se asume.

    El núcleo consulta estos campos para decidir si valida localmente, si manda
    la temperatura o si tiene que declarar una limitación en la trazabilidad.
    """

    #: Nombre corto del proveedor (aparece en el registro y en los reportes).
    nombre: str

    #: URL base del endpoint compatible con OpenAI. ``None`` usa el default del
    #: SDK (el de OpenAI).
    base_url: str | None = None

    #: Variable de entorno de la credencial.
    variable_api_key: str = "OPENAI_API_KEY"

    #: Modelo por defecto del proveedor. Cada uno tiene el suyo: asumir el de
    #: otro haría que una corrida contra OpenAI se estimara con los precios de
    #: DeepSeek (y el número parecería válido).
    modelo_por_defecto: str = ""

    #: ¿El servidor **impone** la forma del JSON contra el esquema? Si es
    #: ``False``, hay que validar localmente y reintentar con el error.
    esquema_estricto: bool = True

    #: ¿``temperature`` tiene efecto? Si es ``False`` (modelos de razonamiento),
    #: no se manda y se declara: mandarla y creer que influyó falsea el ajuste.
    temperatura_efectiva: bool = True

    #: Valores válidos de ``reasoning_effort``. Vacío = el proveedor no lo usa.
    esfuerzos: tuple[str, ...] = ()

    #: **Cómo** cobra el proveedor una imagen. No es un número: los proveedores
    #: no lo calculan igual, y la diferencia es grande.
    #:
    #: * ``TOPE_FIJO`` (DeepSeek): redimensiona toda imagen a ~1300×1300 y cobra
    #:   siempre ``tokens_por_imagen``. La resolución **no** cambia el costo.
    #: * ``MOSAICOS`` (OpenAI): ``85 + 170 × mosaicos`` de 512 px, con ``detail``
    #:   ``low`` plano en 85. Acá la resolución **sí** cambia el costo (hasta 12x).
    #:
    #: Tener solo un ``int`` obligaba a estimar todos los proveedores con el tope
    #: de DeepSeek: con ``-p openai`` la estimación erraba de 0,9x a 12x.
    estrategia_imagen: str = ESTRATEGIA_TOPE_FIJO

    #: Tokens de una imagen cuando la estrategia es :data:`ESTRATEGIA_TOPE_FIJO`.
    tokens_por_imagen: int = 1024

    #: ¿El proveedor expone cuántos tokens de entrada salieron de su caché?
    expone_cache: bool = False

    #: ¿Acepta ``detail`` por imagen (``low``/``high``/``auto``)?
    acepta_detalle: bool = True

    #: ¿Acepta ``max_completion_tokens``?
    acepta_max_tokens: bool = True

    #: Notas de la compensación, para la trazabilidad. Se muestran en el reporte
    #: para que una limitación no quede escondida en el código.
    notas: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Valida lo que el núcleo va a interpretar por su cuenta.

        Se valida **acá** y no en quien estima: un valor desconocido haría que la
        estimación caiga al default en silencio, y el error saldría a la luz como
        un número plausible en vez de como un error de configuración.
        """
        if self.estrategia_imagen not in ESTRATEGIAS_IMAGEN:
            raise ValueError(
                f"{self.nombre}: estrategia_imagen desconocida "
                f"{self.estrategia_imagen!r} (válidas: {', '.join(ESTRATEGIAS_IMAGEN)})"
            )

    def como_diccionario(self) -> dict[str, Any]:
        """Vista serializable, para dejarla en el registro de la corrida."""
        return {
            "proveedor": self.nombre,
            "modelo_por_defecto": self.modelo_por_defecto,
            "base_url": self.base_url,
            "esquema_estricto": self.esquema_estricto,
            "temperatura_efectiva": self.temperatura_efectiva,
            "esfuerzos": list(self.esfuerzos),
            "estrategia_imagen": self.estrategia_imagen,
            "tokens_por_imagen": self.tokens_por_imagen,
            "expone_cache": self.expone_cache,
            "notas": list(self.notas),
        }


@runtime_checkable
class ProveedorLLM(Protocol):
    """Lo que el núcleo necesita de un proveedor.

    Es deliberadamente chico: **declarar** qué soporta y **traducir** la
    respuesta del proveedor a lo que el núcleo entiende. Todo lo demás (recorrer
    el corpus, reanudar, contabilizar el gasto, reportar) es común y vive en el
    núcleo.
    """

    @property
    def capacidades(self) -> Capacidades: ...

    def parametros_de_llamada(
        self,
        *,
        modelo: str,
        sistema: str,
        usuario: str,
        data_url: str,
        esquema: dict[str, Any],
        nombre_esquema: str,
        temperatura: float | None = None,
        detalle: str | None = None,
        max_tokens: int | None = None,
        esfuerzo: str | None = None,
    ) -> dict[str, Any]:
        """Los ``kwargs`` de ``chat.completions.create`` para este proveedor.

        Devolver un diccionario (y no llamar) es lo que hace testeable al
        adaptador sin red: se compara el pedido contra lo que el proveedor espera.
        """
        ...

    def leer_respuesta(self, respuesta: Any) -> dict[str, Any]:
        """Normaliza la respuesta del SDK a ``{contenido, uso, error}``.

        El SDK devuelve objetos distintos según el proveedor; el núcleo no
        debería saberlo. ``uso`` incluye los tokens de caché cuando el proveedor
        los expone (si no, simplemente no están).

        ⚠️ Las claves de ``uso`` son las del SDK (:data:`CLAVE_CACHE_HIT` y
        :data:`CLAVE_CACHE_MISS`), no nombres propios: quien consume el uso es
        ``corrida.py`` y compara contra el ``usage`` real del proveedor.
        """
        ...
