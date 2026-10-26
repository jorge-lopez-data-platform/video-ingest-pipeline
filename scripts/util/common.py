"""Rutas y utilidades compartidas para manejar ffmpeg.

En esta máquina ffmpeg no está en PATH y no existe ffprobe, así que las rutas
salen de imageio-ffmpeg y toda inspección se hace parseando `ffmpeg -i`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

_RE_DURACION = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_RE_STREAM = re.compile(r"Stream #\d+:\d+.*?:\s*(Video|Audio)")
# 3-4 dígitos por lado: evita que etiquetas como "0x31637661" (codec tag)
# se confundan con una resolución.
_RE_RESOLUCION = re.compile(r"\b(\d{3,4})x(\d{3,4})\b")


def configurar_salida() -> None:
    """Fuerza UTF-8 en stdout/stderr.

    La consola de Windows usa cp1252 y revienta al imprimir emojis o tildes
    fuera de ese juego de caracteres.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def run_ffmpeg(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta ffmpeg con los argumentos dados, capturando la salida."""
    return subprocess.run(
        [FFMPEG, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def probe(path: Path) -> dict:
    """Devuelve duración, resolución y presencia de audio de un archivo.

    No lanza cuando falta un dato: devuelve None/False. Solo propaga el fallo
    si ffmpeg no puede ejecutarse.
    """
    resultado = run_ffmpeg(["-i", str(path)], check=False)
    salida = resultado.stderr or ""

    duracion = None
    m = _RE_DURACION.search(salida)
    if m:
        duracion = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    ancho = alto = None
    tiene_video = False
    tiene_audio = False
    for linea in salida.splitlines():
        m_stream = _RE_STREAM.search(linea)
        if not m_stream:
            continue
        tipo = m_stream.group(1)
        if tipo == "Audio":
            tiene_audio = True
        elif tipo == "Video":
            tiene_video = True
            # La resolución aparece tras "Video:"; antes hay etiquetas como
            # "[0x1]" cuyo "0x1" también encaja con el patrón NxM.
            tras_tipo = linea.split("Video:", 1)[-1]
            m_res = _RE_RESOLUCION.search(tras_tipo)
            if m_res:
                ancho, alto = int(m_res.group(1)), int(m_res.group(2))

    if not tiene_video:
        ancho = alto = None

    return {
        "duracion": duracion,
        "ancho": ancho,
        "alto": alto,
        "tiene_audio": tiene_audio,
    }


def es_volumen(ruta: Path) -> bool:
    """True si la ruta vive en un volumen montado de Unity Catalog."""
    # `as_posix()` y no `str()`: en Windows `str()` devuelve barras invertidas y
    # la comprobación fallaría con las mismas rutas que en Databricks.
    texto = Path(ruta).as_posix()
    return texto.startswith("/Volumes/") or texto.startswith("/dbfs/")


def renderizar_en_local(salida: Path):
    """Destino real donde ffmpeg debe escribir, y si hay que copiarlo después.

    ffmpeg escribe el mp4 en dos pasadas: los frames y, al final, el átomo
    `moov` retrocediendo al principio del archivo. El FUSE de los volúmenes de
    Unity Catalog no soporta esa reescritura y ffmpeg muere con código 251
    («Error writing trailer: Input/output error») *después* de haber codificado
    todo. Comprobado con `probe_render`: mismo render, mismo código, /tmp
    termina en 0 y el volumen en 251.

    Se devuelve una ruta en /tmp y True cuando `salida` está en un volumen; el
    llamador debe copiar el resultado con `copiar_a_destino`.
    """
    if not es_volumen(salida):
        return salida, False
    temporal = Path(tempfile.mkdtemp(prefix="render_")) / salida.name
    return temporal, True


def copiar_a_destino(archivo: Path, salida: Path) -> Path:
    """Copia el render terminado a su ruta final y limpia el temporal.

    Copia simple, sin reescritura del archivo abierto: eso sí lo soporta el
    volumen.
    """
    salida.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archivo, salida)
    shutil.rmtree(archivo.parent, ignore_errors=True)
    return salida
