"""API HTTP de ``voucherflow`` — F6/T-606.

Expone las capacidades de la fachada (:mod:`voucherflow.api`) por HTTP. Es la
**segunda superficie** después del CLI, y comparte con ella el principio de
E-LIB-1: es una capa de transporte, no una segunda implementación.

Ver :mod:`voucherflow.http.server` para el contrato de rutas, el mapeo de errores
a códigos de estado y las decisiones de alcance (incluida la de no sumar
dependencias: ``http.server`` de la stdlib).
"""

from .server import (
    HOST_DEFAULT,
    MAX_CUERPO,
    PUERTO_DEFAULT,
    RUTAS,
    VERSION_HTTP,
    EntornoHTTP,
    ErrorPeticion,
    Respuesta,
    Ruta,
    ServidorVoucherflow,
    construir_servidor,
    manejar,
    servir,
)

__all__ = [
    "HOST_DEFAULT",
    "MAX_CUERPO",
    "PUERTO_DEFAULT",
    "RUTAS",
    "VERSION_HTTP",
    "EntornoHTTP",
    "ErrorPeticion",
    "Respuesta",
    "Ruta",
    "ServidorVoucherflow",
    "construir_servidor",
    "manejar",
    "servir",
]
