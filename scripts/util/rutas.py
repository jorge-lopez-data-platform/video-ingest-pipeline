"""Resuelve dónde vive cada video dentro del volumen.

Los renders nuevos se guardan agrupados por fecha:

    /Volumes/tiktok/default/videos/2026/10/24/motivacional_123.mp4

Los renders anteriores a este cambio están planos en la raíz del volumen. Las
dos eras conviven, así que todo el que lea un video debe resolverlo con
`resolver()` en lugar de concatenar `VIDEOS_DIR + nombre`: si no, los ~17
videos viejos se vuelven invisibles de golpe.

El nombre del mp4 NO cambia nunca (es el identificador que comparten
`gold.guiones_usados`, `bronze.tiktok_uploads` y el borrador de TikTok). Lo que
cambia es solo la carpeta que lo contiene, y por eso la búsqueda se hace por
nombre.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

VIDEOS_DIR = "/Volumes/tiktok/default/videos"


def carpeta_del_dia(base: str = VIDEOS_DIR, fecha: datetime | None = None) -> str:
    """Carpeta `AAAA/MM/DD` del día indicado (UTC por defecto).

    No la crea: solo dice cuál es, para que el llamador decida si escribir.
    """
    momento = fecha or datetime.now(timezone.utc)
    return os.path.join(base, f"{momento:%Y}", f"{momento:%m}", f"{momento:%d}")


def resolver(nombre: str, base: str = VIDEOS_DIR) -> str | None:
    """Ruta absoluta del video `nombre`, buscando en la raíz y en las fechas.

    Devuelve `None` si no está en ninguna parte. Se busca primero en la raíz
    porque es donde vive todo lo antiguo y así no se recorre nada en el caso
    común de los renders viejos; después en las carpetas `AAAA/MM/DD`, de la
    más reciente a la más antigua, para que una regeneración gane a la copia
    anterior.
    """
    if not nombre:
        return None
    nombre = os.path.basename(nombre)

    plano = os.path.join(base, nombre)
    if os.path.isfile(plano):
        return plano

    for anio in _ordenados(base, invertir=True):
        ruta_anio = os.path.join(base, anio)
        for mes in _ordenados(ruta_anio, invertir=True):
            ruta_mes = os.path.join(ruta_anio, mes)
            for dia in _ordenados(ruta_mes, invertir=True):
                candidato = os.path.join(ruta_mes, dia, nombre)
                if os.path.isfile(candidato):
                    return candidato
    return None


def companero(nombre: str, sufijo: str, base: str = VIDEOS_DIR) -> str | None:
    """Ruta del fichero hermano del video (`_publicacion.txt`, `_publicacion.json`).

    Usa la misma carpeta en la que se encontró el mp4: buscar por separado
    podría mezclar el `.txt` de un render viejo con el mp4 de uno nuevo si
    ambos nombres coincidieran.
    """
    del_video = resolver(nombre, base)
    if not del_video:
        return None
    stem = os.path.splitext(os.path.basename(del_video))[0]
    candidato = os.path.join(os.path.dirname(del_video), stem + sufijo)
    return candidato if os.path.isfile(candidato) else None


def _ordenados(ruta: str, invertir: bool = False) -> list[str]:
    """Subdirectorios de `ruta` que parecen parte de una fecha AAAA/MM/DD."""
    try:
        entradas = [
            e for e in os.listdir(ruta)
            if len(e) in (2, 4) and e.isdigit()
            and os.path.isdir(os.path.join(ruta, e))
        ]
    except OSError:
        return []
    return sorted(entradas, reverse=invertir)
