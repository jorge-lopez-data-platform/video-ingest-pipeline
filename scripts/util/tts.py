"""Genera la voz en off del guion con edge-tts y deja el audio listo para montar.

Se usa solo cuando no hay grabación propia: la voz sintética no es la ideal,
pero permite ensayar el flujo completo mientras se graba la toma real.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import edge_tts

from scripts.util import common

VOZ_POR_DEFECTO = "es-MX-JorgeNeural"
BLOQUES = ("hook", "open_loop", "bloque_1", "pattern_interrupt",
           "bloque_2", "pre_cierre", "cierre", "cta")


def texto_del_guion(guion: dict) -> str:
    """Une los bloques en el orden en que se leen."""
    partes = [str(guion.get(b) or "").strip() for b in BLOQUES]
    return "\n".join(p for p in partes if p)


async def sintetizar(texto: str, salida: Path, voz: str, rate: str) -> None:
    salida.parent.mkdir(parents=True, exist_ok=True)
    comunicador = edge_tts.Communicate(texto, voz, rate=rate)
    await comunicador.save(str(salida))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guion", default="guion.json")
    parser.add_argument("--out", default="voz/tts.mp3")
    parser.add_argument("--voz", default=VOZ_POR_DEFECTO)
    parser.add_argument("--rate", default="+8%")
    args = parser.parse_args(argv)
    common.configurar_salida()

    guion = json.loads(Path(args.guion).read_text(encoding="utf-8"))
    texto = texto_del_guion(guion)
    if not texto:
        print("❌ El guion está vacío.", file=sys.stderr)
        return 2

    asyncio.run(sintetizar(texto, Path(args.out), args.voz, args.rate))
    info = common.probe(Path(args.out))
    print(f"✅ {args.out} ({info['duracion']:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
