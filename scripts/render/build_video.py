"""Ensambla el video vertical final a partir de la voz, los subtítulos y el b-roll."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from scripts.util import common

ANCHO, ALTO = 1080, 1920
FPS = 30
CRF = 20
TOLERANCIA_DURACION = 3.0
CODIGO_VALIDACION = 4

# Fuente sans con escapes para el filtro de ffmpeg. Arial existe en Windows;
# si falta, ffmpeg cae a su fuente por defecto sin romper el render.
_FUENTE = "C\\:/Windows/Fonts/arialbd.ttf"


def emparejar_escenas(chunks: list[str], escenas: list[dict]) -> list[dict]:
    """Asigna una escena a cada chunk, reutilizando la última si faltan.

    Reutilizar evita huecos negros cuando el manifest trae menos clips que
    bloques: el video se construye igual, con el último clip repitiéndose.
    """
    if not escenas:
        raise ValueError("No hay escenas de b-roll: no se puede construir el video.")
    return [escenas[min(i, len(escenas) - 1)] for i, _ in enumerate(chunks)]


def validar_salida(path: Path, duracion_esperada: float | None) -> None:
    """Comprueba que el mp4 tiene el formato que TikTok espera."""
    info = common.probe(path)

    if not info["tiene_audio"]:
        raise ValueError(f"{path} no tiene pista de audio.")
    if (info["ancho"], info["alto"]) != (ANCHO, ALTO):
        raise ValueError(
            f"{path} mide {info['ancho']}x{info['alto']}, se esperaba {ANCHO}x{ALTO}."
        )
    if duracion_esperada is not None and info["duracion"] is not None:
        diferencia = abs(info["duracion"] - duracion_esperada)
        if diferencia > TOLERANCIA_DURACION:
            raise ValueError(
                f"{path} dura {info['duracion']:.1f}s y se esperaban "
                f"{duracion_esperada:.1f}s (diferencia {diferencia:.1f}s)."
            )


MAX_CARACTERES_LINEA = 16

# Pares (arriba, abajo) del degradado de cada escena sin material.
_TONOS = [
    ("0x1a1030", "0x05060d"),
    ("0x2a0f1e", "0x0a0509"),
    ("0x0f2230", "0x040a10"),
    ("0x241a0a", "0x0a0704"),
]


def _escapar_drawtext(texto: str) -> str:
    """Escapa lo que ffmpeg interpreta dentro de drawtext.

    Los saltos de línea se preservan: drawtext no entiende la secuencia `\\n`
    literal, y al escapar la barra se dibujaba "Ya no tennecesito" en una sola
    línea. Se escapa línea por línea y se unen con un salto real.
    """
    return "\n".join(
        linea.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
             .replace("%", "\\%")
        for linea in texto.replace("\r\n", "\n").split("\n")
    )


def _sin_emojis(texto: str) -> str:
    """Quita emojis: Arial no trae esos glifos y los dibuja como cajas."""
    return "".join(c for c in texto if not 0x1F000 <= ord(c) <= 0x1FAFF)


def _escapar_ruta_filtro(ruta: str) -> str:
    """Escapa una ruta para usarla como valor de un filtro.

    Sin esto, `ass=C:/x/subs.ass` parte el ':' como separador de filtro y
    ffmpeg falla con "Error applying option 'original_size'".
    """
    return "'" + ruta.replace("\\", "/").replace(":", "\\:").replace("'", "\\'") + "'"


def _partir_texto(texto: str, max_caracteres: int = MAX_CARACTERES_LINEA) -> list[str]:
    """Parte el texto en líneas para que no se salga del frame.

    Arial Bold 96 mide ~50 px por carácter, así que más de ~20 caracteres
    rebasa los 1080 px de ancho; drawtext no ajusta solo.
    """
    palabras = texto.split()
    lineas: list[str] = []
    actual = ""
    for palabra in palabras:
        propuesta = f"{actual} {palabra}".strip()
        if actual and len(propuesta) > max_caracteres:
            lineas.append(actual)
            actual = palabra
        else:
            actual = propuesta
    if actual:
        lineas.append(actual)
    return lineas


def _args_escena(escena: dict, duracion: float, indice: int, salida: Path) -> list[str]:
    """Argumentos de ffmpeg que convierten una escena en un clip 1080x1920.

    Los placeholders (sin `ruta`) se generan como color plano con el texto
    de overlay encima: un bloque de color sin nada que leer mata la retención,
    que es justo lo que la spec prohíbe.
    """
    filtro = (
        f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,"
        f"crop={ANCHO}:{ALTO},setsar=1,fps={FPS},"
        f"tpad=stop_mode=clone:stop_duration={duracion}"
    )

    if escena.get("ruta"):
        ruta = str(escena["ruta"])
        # Una imagen fija necesita -loop: sin eso ffmpeg produce un solo frame
        # y el tramo queda congelado o vacío.
        if ruta.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            origen = ["-loop", "1", "-i", ruta]
        else:
            origen = ["-i", ruta]
    else:
        # Degradado vertical en vez de color plano: un fondo liso se ve muerto
        # y el gradiente da profundidad sin depender de material de terceros.
        # El tono varía por escena para que el corte se note.
        tono = _TONOS[indice % len(_TONOS)]
        origen = ["-f", "lavfi", "-i",
                  f"gradients=s={ANCHO}x{ALTO}:r={FPS}:c0={tono[0]}:c1={tono[1]}:"
                  f"x0=0:y0=0:x1=0:y1={ALTO}:d=1"]

    # El overlay se dibuja también sobre clips: si no, el texto de una escena
    # con b-roll desaparece sin aviso.
    texto = _sin_emojis((escena.get("texto_overlay") or "").strip())
    if texto:
        lineas = "\n".join(_partir_texto(texto))
        filtro += (
            f",drawtext=text='{_escapar_drawtext(lineas)}'"
            f":fontfile='{_FUENTE}':fontsize=96:fontcolor=white"
            f":borderw=6:bordercolor=black"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
            f":line_spacing=16"
        )

    return [
        *origen,
        "-t", f"{duracion}",
        "-vf", filtro,
        "-an", "-c:v", "libx264", "-crf", str(CRF), "-pix_fmt", "yuv420p",
        "-y", str(salida),
    ]


def _normalizar_escena(escena: dict, duracion: float, indice: int, tmp: Path) -> Path:
    """Convierte una escena en un clip 1080x1920 de la duración pedida."""
    salida = tmp / f"escena_{indice:03d}.mp4"
    common.run_ffmpeg(_args_escena(escena, duracion, indice, salida))
    return salida


def construir_video(audio: Path, subs: Path | None, escenas: list[dict], salida: Path) -> Path:
    """Concatena el b-roll con la voz normalizada y quema los subtítulos."""
    info_audio = common.probe(audio)
    duracion_audio = info_audio["duracion"]
    if not duracion_audio:
        raise ValueError(f"No se pudo leer la duración de {audio}")

    if not escenas:
        raise ValueError("No hay escenas que montar")

    por_escena = duracion_audio / len(escenas)
    salida.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        clips = [
            _normalizar_escena(escena, por_escena, i, tmp)
            for i, escena in enumerate(escenas)
        ]
        lista = tmp / "clips.txt"
        lista.write_text(
            "".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8"
        )

        filtro = f"[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        args = [
            "-f", "concat", "-safe", "0", "-i", str(lista),
            "-i", str(audio),
            "-filter_complex", filtro,
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-crf", str(CRF), "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-shortest",
        ]
        if subs is not None:
            # Pedir subtítulos y no tenerlos no debe producir un video mudo
            # de texto sin que nadie se entere.
            if not Path(subs).is_file():
                raise FileNotFoundError(f"No existe el archivo de subtítulos: {subs}")
            args += ["-vf", f"ass={_escapar_ruta_filtro(Path(subs).as_posix())}"]
        args += ["-y", str(salida)]
        common.run_ffmpeg(args)

    return salida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--subs", default=None)
    parser.add_argument("--escenas", required=True, help="manifest.json del b-roll")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    common.configurar_salida()

    escenas = json.loads(Path(args.escenas).read_text(encoding="utf-8"))
    salida = construir_video(Path(args.audio), Path(args.subs) if args.subs else None,
                             escenas, Path(args.out))

    esperada = common.probe(Path(args.audio))["duracion"]
    try:
        validar_salida(salida, esperada)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return CODIGO_VALIDACION

    print(f"✅ {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
