"""Demo de extracción a Markdown con la librería ``xberg`` (xberg-io/xberg).

Aplica ``xberg.extract()`` (API async) sobre archivos locales del golden set
(PDF e imagen). ``ExtractInput(kind="uri", uri=...)`` acepta rutas locales o
``file://``, además de URLs HTTP(S).

Uso:
    python wip/demo_xberg.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xberg import ExtractInput, ExtractInputKind, extract

# Raíz de v2/ (derivada del propio script → no depende del CWD).
_RAIZ = Path(__file__).resolve().parents[1]

archivos = [
    _RAIZ / "tests/fixtures/golden/9dfc597f-34c5-41ec-99ae-cf35544c7af8.pdf",
    _RAIZ / "tests/fixtures/golden/66e6e0ea-e910-41f4-9037-13f0309812c1.jpg",
]


async def main() -> None:
    for archivo in archivos:
        print(f"\n{'=' * 60}\nARCHIVO: {archivo.name}\n{'=' * 60}")
        input = ExtractInput(
            kind=ExtractInputKind("uri"),
            uri=str(archivo),
            filename=archivo.name,
        )
        result = await extract(input)
        if result.errors:
            print("ERRORES:", [str(e) for e in result.errors])
            continue
        for doc in result.results:
            print(doc.content)


if __name__ == "__main__":
    asyncio.run(main())
