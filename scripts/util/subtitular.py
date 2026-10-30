"""Quema subtítulos legibles sobre un video vertical ya renderizado.

Diferencia con `transcribe.agrupar_chunks`: ese agrupador corta solo por número
de palabras y el resultado parte frases por la mitad ("decides respirar. Así
que cierra"). Aquí el corte respeta el final de frase y el tamaño real que
cabe en 1080 px, que es lo que hace legible un subtítulo en TikTok.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.util import common

ANCHO, ALTO = 1080, 1920
MAX_PALABRAS = 4
MAX_CARACTERES = 20
PAUSA_CORTE = 0.30

# Los 420 px de abajo los tapa la interfaz de TikTok, así que el texto sube.
MARGEN_VERTICAL = 500
TAMANO_FUENTE = 76
CONTORNO = 5
# Ver el comentario de `jesucitos.PRESET`: este es el segundo encode completo que
# sufre cada video (el de `quemar`), y baja igual de barato con `veryfast`.
PRESET = "veryfast"


def _cierra_frase(texto: str) -> bool:
    return texto.endswith((".", "!", "?", "…"))


def _cierra_idea(texto: str) -> bool:
    return texto.endswith((",", ";", ":"))


def agrupar(palabras: list[dict]) -> list[dict]:
    """Junta palabras en subtítulos que se leen de un golpe.

    Cierra antes si el texto ya termina una frase, para no arrastrar el inicio
    de la siguiente. Una coma solo corta cuando el bloque ya tiene dos
    palabras: cortar en "Dios," dejaría un subtítulo de una sola palabra.
    """
    chunks: list[dict] = []
    actual: list[dict] = []

    def cerrar() -> None:
        if actual:
            chunks.append({
                "inicio": actual[0]["inicio"],
                "fin": actual[-1]["fin"],
                "texto": " ".join(p["texto"] for p in actual),
            })
            actual.clear()

    for palabra in palabras:
        if actual:
            pausa = palabra["inicio"] - actual[-1]["fin"]
            previo = actual[-1]["texto"]
            caracteres = sum(len(p["texto"]) + 1 for p in actual) + len(palabra["texto"])
            if (
                _cierra_frase(previo)
                or pausa > PAUSA_CORTE
                or len(actual) >= MAX_PALABRAS
                or caracteres > MAX_CARACTERES
                or (_cierra_idea(previo) and len(actual) >= 2)
            ):
                cerrar()
        actual.append(palabra)
    cerrar()
    return chunks


def _partir_en_dos_lineas(texto: str, maximo: int = MAX_CARACTERES) -> str:
    """Parte por el espacio más cercano al centro si no cabe en una línea."""
    if len(texto) <= maximo:
        return texto
    palabras = texto.split()
    mejor, mejor_desvio = len(palabras), None
    for i in range(1, len(palabras)):
        arriba = len(" ".join(palabras[:i]))
        abajo = len(" ".join(palabras[i:]))
        desvio = abs(arriba - abajo)
        if mejor_desvio is None or desvio < mejor_desvio:
            mejor, mejor_desvio = i, desvio
    return " ".join(palabras[:mejor]) + r"\N" + " ".join(palabras[mejor:])


def _timestamp(segundos: float) -> str:
    """Redondea en centésimas antes de partir, para no escribir "0:00:60.00"."""
    total = int(round(max(segundos, 0.0) * 100))
    horas, total = divmod(total, 360000)
    minutos, resto = divmod(total, 6000)
    return f"{horas}:{minutos:02d}:{resto // 100:02d}.{resto % 100:02d}"


def generar_ass(chunks: list[dict], ancho: int = ANCHO, alto: int = ALTO) -> str:
    cabecera = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ancho}
PlayResY: {alto}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,Arial,{TAMANO_FUENTE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{CONTORNO},2,2,50,50,{MARGEN_VERTICAL},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lineas = []
    for chunk in chunks:
        texto = _partir_en_dos_lineas(chunk["texto"].replace("\n", " "))
        lineas.append(
            f"Dialogue: 0,{_timestamp(chunk['inicio'])},{_timestamp(chunk['fin'])},"
            f"TikTok,,0,0,0,,{texto}"
        )
    return cabecera + "\n".join(lineas) + "\n"


# --- Marca de agua ---------------------------------------------------------
# Viaja quemada en los píxeles, así que sobrevive a la re-codificación de
# TikTok (los metadatos del mp4 no).
#
# Va fija en la cintura del personaje —el borde entre la camisa y el pantalón—
# y centrada en el encuadre. Antes recorría el video en diagonal para que no se
# pudiera recortar, pero el usuario pidió que quedara quieta: con un solo
# personaje en escena, una marca estática sobre su ropa se lee como parte de él
# y no tapa nada de la interfaz de TikTok (que empieza ~420 px más abajo).
MARCA_OPACIDAD = "40"  # alpha ASS: 00 = opaco, FF = invisible
MARCA_TAMANO = 38
# Cintura = `PJ_Y` (780) + y de la cintura en el render (760x1000). El x es el
# centro del encuadre. Si cambia `PJ_Y` en jesucitos.py, esto se recalcula.
MARCA_PUNTO = (540, 1591)


def agregar_marca(ass_texto: str, duracion: float, usuario: str) -> str:
    """Pinta la marca del canal, fija, en la cintura del personaje.

    Un solo diálogo que cubre todo el video: sin `\\move` ni tramos, así que no
    hay forma de que la marca salte si la duración cambia.

    Sin usuario no se quema nada: es preferible un video sin marca a uno con
    un `@tu_usuario` de relleno.
    """
    usuario = (usuario or "").strip()
    if not usuario:
        return ass_texto
    if not usuario.startswith("@"):
        usuario = "@" + usuario

    estilo = (
        f"Style: Marca,Arial,{MARCA_TAMANO},&H{MARCA_OPACIDAD}FFFFFF,"
        "&H000000FF,&H80000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1\n"
    )
    x, y = MARCA_PUNTO
    evento = (
        f"Dialogue: 2,{_timestamp(0)},{_timestamp(duracion)},"
        f"Marca,,0,0,0,,"
        f"{{\\an5\\pos({x},{y})\\alpha&H{MARCA_OPACIDAD}&}}"
        f"{usuario}"
    )

    con_estilo = ass_texto.replace("[Events]", estilo + "\n[Events]", 1)
    return con_estilo.rstrip("\n") + "\n" + evento + "\n"


def _escapar_ruta_filtro(ruta: str) -> str:
    """Escapa la ruta para `ass=`; sin esto el ':' de `C:` parte el filtro."""
    return "'" + ruta.replace("\\", "/").replace(":", "\\:").replace("'", "\\'") + "'"


def quemar(video: Path, subs: Path, salida: Path) -> Path:
    """Re-encoda el video con los subtítulos incrustados en la imagen."""
    # Mismo motivo que en `jesucitos.animar`: el volumen no acepta la
    # reescritura del trailer del mp4, así que se escribe en /tmp y se copia.
    destino, copiar = common.renderizar_en_local(salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-i", str(video),
        "-vf", f"ass={_escapar_ruta_filtro(subs.as_posix())}",
        "-c:v", "libx264", "-crf", "20", "-preset", PRESET, "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-y", str(destino),
    ]
    with tempfile.TemporaryFile() as log:
        # stderr a archivo y no a PIPE: un PIPE sin lector bloquea ffmpeg y el
        # mp4 queda sin átomo `moov`, que parece un render lento.
        codigo = subprocess.call([common.FFMPEG, *args],
                                 stdout=subprocess.DEVNULL, stderr=log)
        if codigo != 0:
            log.seek(0)
            error = log.read().decode("utf-8", "replace")
            raise RuntimeError(f"ffmpeg falló ({codigo}): {error[-1500:]}")
    if copiar:
        common.copiar_a_destino(destino, salida)
    return salida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--palabras", required=True,
                        help="palabras.json de scripts.transcribe")
    parser.add_argument("--out", default="voz/jesucitos_subs.mp4")
    args = parser.parse_args(argv)
    common.configurar_salida()

    palabras = json.loads(Path(args.palabras).read_text(encoding="utf-8"))
    chunks = agrupar(palabras)
    subs = Path(args.out).with_suffix(".ass")
    subs.parent.mkdir(parents=True, exist_ok=True)
    subs.write_text(generar_ass(chunks), encoding="utf-8")

    salida = quemar(Path(args.video), subs, Path(args.out))
    info = common.probe(salida)
    print(f"✅ {salida} ({len(chunks)} subtítulos, {info['duracion']:.1f}s, "
          f"{info['ancho']}x{info['alto']}, audio={info['tiene_audio']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
