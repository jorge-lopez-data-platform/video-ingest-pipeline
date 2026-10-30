"""Genera el JSON que consume la composición de Remotion.

Remotion necesita los tiempos en un solo archivo: la duración, los rangos de
cada bloque del guion y las palabras con su inicio/fin. Este script une el
guion, el manifiesto de escenas y la transcripción alineada, y deja todo listo
para `remotion render` sin que la composición tenga que calcular nada.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.util import common

BLOQUES = (
    "hook", "open_loop", "bloque_1", "pattern_interrupt",
    "bloque_2", "pre_cierre", "cierre", "cta",
)

# Kicker: etiqueta de dos o tres palabras arriba. Va corta para que no compita
# con el subtítulo, que ya dice lo mismo con las palabras exactas del audio.
_KICKERS = {
    "hook": "amor propio",
    "open_loop": "la frase",
    "bloque_1": "el daño",
    "pattern_interrupt": "el giro",
    "bloque_2": "la verdad",
    "pre_cierre": "la frase",
    "cierre": "suelta",
    "cta": "compártelo",
}

# Declaración grande en el centro. Solo en tres bloques: si aparece en todos,
# se vuelve ruido y tapa el subtítulo, que es lo que realmente se lee.
_DECLARACIONES = {
    "pattern_interrupt": "¿Y SI EL PROBLEMA NO ES ESA PERSONA?",
    "pre_cierre": "YA NO TE NECESITO",
    "cierre": "EL QUE SE VA, QUE SE VAYA",
}


def _limpiar(texto: str) -> str:
    """Quita emojis: la fuente del video no trae esos glifos."""
    return "".join(c for c in texto if not 0x1F000 <= ord(c) <= 0x1FAFF).strip()


def _rangos(guion: dict, palabras: list[dict]) -> list[dict]:
    """Reparte las palabras transcritas entre los bloques del guion.

    Se reparte por número relativo de palabras en vez de buscar coincidencias
    exactas: la transcripción agrupa signos y contracciones distinto que el
    guion, y una búsqueda literal dejaría bloques sin rango.
    """
    totales = [len(guion[b].split()) for b in BLOQUES]
    suma = sum(totales)
    if suma == 0 or not palabras:
        raise ValueError("El guion no tiene palabras o falta la transcripción.")

    rangos, acumulado = [], 0
    for nombre, cuenta in zip(BLOQUES, totales):
        inicio = acumulado
        acumulado += cuenta
        primera = min(int(len(palabras) * inicio / suma), len(palabras) - 1)
        ultima = min(int(len(palabras) * acumulado / suma), len(palabras)) - 1
        ultima = max(primera, ultima)
        rangos.append({
            "nombre": nombre,
            "inicio": palabras[primera]["inicio"],
            "fin": palabras[ultima]["fin"],
            "kicker": _limpiar(_KICKERS[nombre]),
            "declaracion": _limpiar(_DECLARACIONES.get(nombre, "")),
            "texto": guion[nombre],
        })

    # El primer bloque arranca en 0 y el último cierra con el final del audio,
    # para que no quede un hueco sin subtítulo al principio ni al final.
    rangos[0]["inicio"] = 0.0
    return rangos


def exportar(guion: Path, palabras_json: Path, salida: Path,
             audio: Path | None = None) -> Path:
    datos = json.loads(guion.read_text(encoding="utf-8-sig"))
    palabras = json.loads(palabras_json.read_text(encoding="utf-8-sig"))

    if not palabras:
        raise ValueError("La transcripción no tiene palabras.")

    bloques = _rangos(datos, palabras)
    duracion = palabras[-1]["fin"]

    # La última palabra suele terminar antes que el audio (silencio final).
    # Si el video dura menos, Remotion corta la voz antes de que acabe.
    if audio is not None:
        audio_segundos = common.probe(audio)["duracion"]
        if audio_segundos:
            duracion = max(duracion, audio_segundos)
    # El último bloque debe cubrir hasta el final o el overlay desaparece antes
    # de tiempo y el fondo se queda sin tono asignado.
    bloques[-1]["fin"] = round(duracion, 3)

    video = {
        "duracionSegundos": round(duracion, 3),
        "bloques": bloques,
        "palabras": [
            {"inicio": p["inicio"], "fin": p["fin"], "texto": p["texto"]}
            for p in palabras
        ],
        "hashtags": datos.get("hashtags", []),
        "slug": datos.get("slug", ""),
    }

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(
        json.dumps(video, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return salida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guion", required=True)
    parser.add_argument("--palabras", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--audio", default=None,
                        help="Voz: la composición no debe cortarla al final")
    args = parser.parse_args(argv)
    # La consola de Windows usa cp1252 y revienta al imprimir emojis.
    common.configurar_salida()

    try:
        salida = exportar(
            Path(args.guion), Path(args.palabras), Path(args.out),
            Path(args.audio) if args.audio else None,
        )
    except (ValueError, KeyError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    print(f"✅ {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
