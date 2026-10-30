"""Resuelve el b-roll de cada escena: biblioteca local, Pexels o placeholder.

El orden importa: lo propio primero (cero riesgo de licencia), stock con
licencia después, y si no hay nada convincente se prefiere texto a pantalla
sobre color antes que un clip genérico que mata la retención.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from scripts.util import common

PEXELS_API = "https://api.pexels.com/videos/search"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
EXTENSIONES = (".mp4", ".mov")


def _pexels(termino: str, key: str, por_pagina: int = 5) -> list[dict]:
    """Busca clips verticales en Pexels. Devuelve [] si no hay resultados."""
    import requests

    respuesta = requests.get(
        PEXELS_API,
        headers={"Authorization": key},
        params={"query": termino, "orientation": "portrait", "per_page": por_pagina},
        timeout=30,
    )
    respuesta.raise_for_status()
    videos = respuesta.json().get("videos", [])

    resultados = []
    for video in videos:
        archivos = [
            f for f in video.get("video_files", [])
            if f.get("width") and f.get("height") and f["height"] > f["width"]
        ]
        if not archivos:
            continue
        mejor = max(archivos, key=lambda f: f.get("height", 0))
        resultados.append({"ruta": mejor["link"], "fuente": "pexels"})
    return resultados


def _descargar(url: str, cache: Path, indice: int) -> str:
    """Baja el material a disco y devuelve la ruta local.

    Un manifest con URLs remotas obliga a ffmpeg a descargar en cada render y
    a depender de que el host siga vivo; se guarda una vez y se reutiliza.
    Si la descarga falla se devuelve la URL: build_video aún puede leerla.
    """
    import requests

    cache.mkdir(parents=True, exist_ok=True)
    sufijo = Path(url.split("?")[0]).suffix or ".jpg"
    destino = cache / f"escena_{indice:03d}{sufijo}"
    if destino.is_file() and destino.stat().st_size > 0:
        return str(destino)
    try:
        respuesta = requests.get(url, timeout=60, headers={"User-Agent": "tiktok-pipeline/1.0"})
        respuesta.raise_for_status()
        destino.write_bytes(respuesta.content)
        return str(destino)
    except Exception as error:
        print(f"⚠️  No se pudo descargar {url} ({error}); se usa la URL.", file=sys.stderr)
        return url


def _openverse(termino: str, por_pagina: int = 5) -> list[dict]:
    """Busca imágenes de dominio público / CC0 en Openverse.

    Alternativa a Pexels cuando esa API no está disponible: no requiere key y
    las licencias cc0/pdm permiten usar el material sin atribución. Son fotos
    fijas, así que build_video las trata con -loop.
    """
    import requests

    respuesta = requests.get(
        OPENVERSE_API,
        headers={"User-Agent": "tiktok-pipeline/1.0"},
        params={
            # La API espera `q`; con `query` ignora el término y devuelve
            # siempre el mismo resultado por defecto (18 escenas idénticas).
            "q": termino,
            "license": "cc0,pdm",
            "page_size": por_pagina,
            "mature": "false",
        },
        timeout=30,
    )
    respuesta.raise_for_status()

    resultados = []
    for imagen in respuesta.json().get("results", []):
        url = imagen.get("url")
        if not url or url.lower().endswith((".svg", ".gif")):
            continue  # ffmpeg no puede escalar SVG ni GIF animado como foto fija
        resultados.append({"ruta": url, "fuente": "openverse"})
    return resultados


_VACIAS = {
    "cuando", "alguien", "porque", "cada", "donde", "desde", "sobre", "puedes",
    "tienes", "hacer", "sigue", "cosas", "todos", "todas", "nada", "todo",
    "para", "pero", "esta", "este", "esto", "esos", "esas", "como", "solo",
    "más", "mas", "muy", "los", "las", "del", "que", "por", "con", "una",
}


def _terminos(guion: dict) -> list[str]:
    """Términos de búsqueda derivados del guion, uno por escena.

    Sin esto todas las escenas pedían el mismo término ("abstract") y el
    manifest repetía la misma imagen 18 veces, sin relación con el texto.
    """
    terminos: list[str] = []
    for bloque in ("hook", "bloque_1", "pattern_interrupt", "bloque_2",
                   "pre_cierre", "cierre", "cta"):
        for palabra in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{5,}", str(guion.get(bloque) or "")):
            limpia = palabra.lower()
            if limpia not in _VACIAS and limpia not in terminos:
                terminos.append(limpia)
    if guion.get("tema"):
        terminos.insert(0, str(guion["tema"]))
    return terminos or ["abstract"]


def resolver_escenas(
    guion: dict,
    broll_dir: Path,
    pexels_key: str | None,
    por_escena: float = 4,
    duracion_total: float = 60,
) -> list[dict]:
    """Devuelve una escena por bloque de `por_escena` segundos."""
    n_escenas = max(1, math.ceil(duracion_total / por_escena))
    overlays = list(guion.get("texto_en_pantalla") or [])

    locales = sorted(
        str(p) for p in Path(broll_dir).glob("*")
        if p.suffix.lower() in EXTENSIONES
    ) if Path(broll_dir).is_dir() else []

    terminos = _terminos(guion)
    escenas: list[dict] = []
    for i in range(n_escenas):
        overlay = overlays[i] if i < len(overlays) else None

        if locales:
            escenas.append({
                "ruta": locales[i % len(locales)],
                "texto_overlay": overlay,
                "fuente": "local",
            })
            continue

        termino = terminos[i % len(terminos)]
        resultados = []
        if pexels_key:
            try:
                resultados = _pexels(termino, pexels_key)
            except Exception as error:  # la API caída no debe romper el video
                print(f"⚠️  Pexels falló ({error}); se prueba Openverse.", file=sys.stderr)
        if not resultados:
            try:
                resultados = _openverse(termino)
            except Exception as error:
                print(f"⚠️  Openverse falló ({error}); se usa placeholder.", file=sys.stderr)
        if resultados:
            elegido = resultados[0]
            ruta = _descargar(elegido["ruta"], Path(broll_dir) / "cache", i)
            # Una URL remota en el manifest rompe el render: ffmpeg no puede
            # hacer -loop sobre http y el clip sale vacío. Si no se pudo bajar,
            # se trata como escena sin material.
            if ruta.startswith("http"):
                escenas.append({"ruta": None, "texto_overlay": overlay, "fuente": "placeholder"})
                continue
            escenas.append({
                "ruta": ruta,
                "texto_overlay": overlay,
                "fuente": elegido["fuente"],
            })
            continue

        escenas.append({"ruta": None, "texto_overlay": overlay, "fuente": "placeholder"})

    return escenas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guion", required=True)
    parser.add_argument("--broll-dir", default="broll")
    parser.add_argument("--out", default="broll/manifest.json")
    parser.add_argument("--por-escena", type=float, default=4)
    parser.add_argument("--duracion-total", type=float, default=60)
    args = parser.parse_args(argv)
    common.configurar_salida()

    guion = json.loads(Path(args.guion).read_text(encoding="utf-8"))
    key = os.environ.get("PEXELS_API_KEY")

    escenas = resolver_escenas(
        guion, Path(args.broll_dir), key,
        por_escena=args.por_escena, duracion_total=args.duracion_total,
    )

    salida = Path(args.out)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(escenas, ensure_ascii=False, indent=2), encoding="utf-8")

    cuenta: dict[str, int] = {}
    for escena in escenas:
        cuenta[escena["fuente"]] = cuenta.get(escena["fuente"], 0) + 1
    resumen = ", ".join(f"{k}: {v}" for k, v in cuenta.items())
    print(f"✅ {len(escenas)} escenas ({resumen}) → {salida}")
    if not key and cuenta.get("placeholder"):
        print("ℹ️  Sin PEXELS_API_KEY: las escenas no locales usan placeholder con texto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
