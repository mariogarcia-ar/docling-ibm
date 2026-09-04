#!/usr/bin/env python3
"""
Constata comprobantes de proveedores contra ARCA/AFIP (WSCDC) usando Afip SDK.

Requisitos:
    pip install afip.py python-dotenv
    Un ACCESS_TOKEN gratuito de https://app.afipsdk.com
    (Opcional) Certificado propio de Homologación o Producción. Sin certificado,
    Afip SDK permite probar en modo desarrollo con el CUIT público 20409378472.

Configuración (.env, ver .env.example):
    AFIP_ACCESS_TOKEN=...
    AFIP_CUIT=20247454072
    AFIP_CERT_PATH=./certs/homologacion.crt   # opcional
    AFIP_KEY_PATH=./certs/homologacion.key    # opcional

Uso:
    python consultar_arca.py --cuit-emisor 30500000000 --tipo-cbte A \
        --pto-vta 2 --nro-cbte 1234 --cae 75082223003046 \
        --fecha 20260617 --importe 1500.00

    # o cargando los datos ya extraídos por ask.py (resultado.json)
    python consultar_arca.py --json resultado.json --cae 75082223003046 --cuit-emisor 30500000000
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Mapeo de letra de comprobante -> código CbteTipo de ARCA
TIPO_CBTE = {"A": 1, "B": 6, "C": 11}


def build_afip():
    """Crea la instancia de Afip SDK a partir de las variables de entorno."""
    load_dotenv()

    access_token = os.environ.get("AFIP_ACCESS_TOKEN")
    if not access_token:
        print(
            "Error: falta AFIP_ACCESS_TOKEN. Generá uno gratis en https://app.afipsdk.com "
            "y guardalo en un archivo .env (ver .env.example)",
            file=sys.stderr,
        )
        sys.exit(1)

    cuit = int(os.environ.get("AFIP_CUIT", "20409378472"))  # CUIT público de pruebas por defecto
    config = {"CUIT": cuit, "access_token": access_token}

    cert_path = os.environ.get("AFIP_CERT_PATH")
    key_path = os.environ.get("AFIP_KEY_PATH")
    if cert_path and key_path:
        config["cert"] = Path(cert_path).read_text(encoding="utf-8")
        config["key"] = Path(key_path).read_text(encoding="utf-8")

    from afip import Afip

    return Afip(config)


def parse_pto_vta_nro(nro_factura: str) -> tuple[int, int]:
    """Separa un número de factura tipo '00001-00000031' en (pto_vta, nro_cbte)."""
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", nro_factura)
    if not match:
        raise ValueError(f"No se pudo interpretar el número de factura: '{nro_factura}'")
    return int(match.group(1)), int(match.group(2))


def constatar_comprobante(afip, *, cuit_emisor, tipo_cbte, pto_vta, nro_cbte, cae, fecha, importe, cuit_receptor):
    """Consulta el WSCDC de ARCA para validar un comprobante emitido por un tercero."""
    ws = afip.web_service("wscdc")
    ta = ws.get_token_authorization()

    data = {
        "Auth": {"Token": ta["token"], "Sign": ta["sign"], "Cuit": afip.CUIT},
        "CmpReq": {
            "CbteModo": "CAE",
            "CuitEmisor": cuit_emisor,
            "PtoVta": pto_vta,
            "CbteTipo": tipo_cbte,
            "CbteNro": nro_cbte,
            "CbteFch": fecha,
            "ImpTotal": importe,
            "CodAutorizacion": cae,
            "DocTipoReceptor": 80,  # 80 = CUIT
            "DocNroReceptor": cuit_receptor,
        },
    }
    return ws.execute_request("ComprobanteConstatar", data)


def main():
    parser = argparse.ArgumentParser(description="Constata un comprobante contra ARCA/AFIP (WSCDC)")
    parser.add_argument("--json", type=Path, help="JSON con datos extraídos (ej. resultado.json de ask.py)")
    parser.add_argument("--cuit-emisor", type=int, help="CUIT de quien emitió el comprobante")
    parser.add_argument("--tipo-cbte", help="Tipo de comprobante: A, B, C o código numérico ARCA")
    parser.add_argument("--pto-vta", type=int, help="Punto de venta")
    parser.add_argument("--nro-cbte", type=int, help="Número de comprobante")
    parser.add_argument("--nro-factura", help="Alternativa a --pto-vta/--nro-cbte, formato '0001-00000031'")
    parser.add_argument("--cae", required=True, help="CAE de 14 dígitos del comprobante")
    parser.add_argument("--fecha", help="Fecha de emisión en formato AAAAMMDD")
    parser.add_argument("--importe", type=float, help="Importe total del comprobante")
    parser.add_argument("--cuit-receptor", type=int, help="Tu CUIT (receptor). Default: AFIP_CUIT del .env")
    args = parser.parse_args()

    datos = {}
    if args.json:
        datos = json.loads(args.json.read_text(encoding="utf-8"))

    cuit_emisor = args.cuit_emisor or datos.get("cuit_emisor")
    fecha = args.fecha or datos.get("fecha_emision")
    importe = args.importe if args.importe is not None else datos.get("importe_total_facturado")
    tipo_cbte_raw = args.tipo_cbte or datos.get("tipo_factura")
    nro_factura = args.nro_factura or datos.get("nro_factura")

    if not all([cuit_emisor, fecha, importe, tipo_cbte_raw, args.cae]):
        print("Error: faltan datos obligatorios (cuit_emisor, fecha, importe, tipo_cbte, cae)", file=sys.stderr)
        sys.exit(1)

    tipo_cbte = TIPO_CBTE.get(str(tipo_cbte_raw).upper(), tipo_cbte_raw)
    try:
        tipo_cbte = int(tipo_cbte)
    except (TypeError, ValueError):
        print(f"Error: tipo de comprobante inválido '{tipo_cbte_raw}'", file=sys.stderr)
        sys.exit(1)

    if args.pto_vta is not None and args.nro_cbte is not None:
        pto_vta, nro_cbte = args.pto_vta, args.nro_cbte
    elif nro_factura:
        pto_vta, nro_cbte = parse_pto_vta_nro(str(nro_factura))
    else:
        print("Error: especificá --pto-vta y --nro-cbte, o --nro-factura", file=sys.stderr)
        sys.exit(1)

    # Convertir fecha DD/MM/AAAA (formato usado en ask.py) a AAAAMMDD si hace falta
    if "/" in str(fecha):
        dia, mes, anio = str(fecha).split("/")
        fecha = f"{anio}{mes}{dia}"

    afip = build_afip()
    cuit_receptor = args.cuit_receptor or int(os.environ.get("AFIP_CUIT", afip.CUIT))

    resultado = constatar_comprobante(
        afip,
        cuit_emisor=int(cuit_emisor),
        tipo_cbte=tipo_cbte,
        pto_vta=pto_vta,
        nro_cbte=nro_cbte,
        cae=args.cae,
        fecha=int(fecha),
        importe=float(importe),
        cuit_receptor=cuit_receptor,
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
