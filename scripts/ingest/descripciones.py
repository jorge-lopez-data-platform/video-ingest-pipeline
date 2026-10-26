"""Compone el paquete de publicación de un lote de videos.

El resultado es un único `.md` con, por cada video, los cinco bloques del
formulario de YouTube: archivo original, título, descripción, tags y hashtags.
Sigue el formato de `descripciones_reels_v2.md`.

No llama a ningún LLM. Todo sale de los bloques del guion que el pipeline ya
generó: reescribir el texto con un modelo costaría una llamada por video y
añadiría una fuente de deriva entre lo que dice el video y lo que dice su
descripción.

La descripción se arma sin hashtags dentro, a propósito: así se pega el bloque
de hashtags al final sin duplicarlos.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

MARCA = "@alguienqueteescucha"
# YouTube solo cuenta los 3 primeros; se emiten todos igualmente como señal
# temática, con #Shorts siempre delante.
MAX_TITULO = 100
MAX_TAGS = 500
# Etiquetas genéricas que en YouTube no posicionan y delatan reciclaje desde
# TikTok. Se filtran del bloque de tags, no de los hashtags del video.
BASURA_TIKTOK = {"fyp", "parati", "viral", "foryou", "foryoupage", "grupoduelo",
                 "motivacion", "motivacional"}


def _limpio(valor) -> str:
    return str(valor or "").strip()


def _sin_hashtags(texto: str) -> str:
    """Quita `#etiqueta` del texto libre para que no se cuele en la descripción."""
    return " ".join(p for p in _limpio(texto).split() if not p.startswith("#"))


def titulo_para(guion: dict, destino: str = MAX_TITULO) -> str:
    """Título del formulario de YouTube, recortado a `MAX_TITULO` caracteres.

    Prefiere `texto_pantalla_1`, que es el titular corto que ya se quema en el
    video; si el guion no lo trae, cae al `hook`.
    """
    # Databricks a veces devuelve las filas como dict y a veces como Row: se
    # prueba la clave sin asumir el tipo para no reventar con una de las dos.
    try:
        base = _sin_hashtags(guion.get("texto_pantalla_1") or guion.get("hook"))
    except (AttributeError, TypeError):
        base = ""
    base = " ".join(base.split())
    if len(base) <= destino:
        return base
    # Cortar a mitad de palabra queda peor que cortar en el último espacio.
    recorte = base[:destino].rsplit(" ", 1)[0]
    return recorte.rstrip(" ,.;:") or base[:destino]


def descripcion_para(guion: dict) -> str:
    """Gancho + 2-3 líneas del cuerpo + CTA + canal, sin hashtags."""
    gancho = _sin_hashtags(guion.get("hook")) or _sin_hashtags(guion.get("texto_pantalla_1"))
    cuerpo = [
        _sin_hashtags(guion.get(b))
        for b in ("open_loop", "bloque_1", "bloque_2")
    ]
    cuerpo = [c for c in cuerpo if c]
    cta = _sin_hashtags(guion.get("cta"))

    partes = []
    if gancho:
        partes.append(gancho)
    if cuerpo:
        partes.append("\n\n".join(cuerpo[:3]))
    if cta:
        partes.append(f"💬 {cta}")
    partes.append(f"▶ {MARCA}")
    return "\n\n".join(partes)


def tags_para(hashtags: str, destino: int = 500) -> str:
    """Etiquetas del campo «Etiquetas» de YouTube: separadas por coma, sin `#`.

    Se descartan las etiquetas de TikTok que no posicionan en YouTube y se
    recorta a `MAX_TAGS` caracteres, que es el límite del formulario.
    """
    vistas, salida = set(), []
    for bruto in (hashtags or "").split(","):
        limpio = bruto.strip().lstrip("#").strip()
        if not limpio:
            continue
        clave = limpio.casefold()
        if clave in BASURA_TIKTOK or clave in vistas:
            continue
        vistas.add(clave)
        candidato = ", ".join(salida + [limpio])
        if len(candidato) > destino:
            break
        salida.append(limpio)
    return ", ".join(salida)


def hashtags_para(hashtags: str) -> str:
    """Bloque final de hashtags, con `#Shorts` siempre en primer lugar."""
    etiquetas = [
        h.strip().lstrip("#").strip()
        for h in (hashtags or "").split(",")
        if h.strip()
    ]
    # Se descartan los hashtags de TikTok que en YouTube no posicionan y
    # delatan que el video viene de otra red.
    etiquetas = [
        e for e in etiquetas
        if e.casefold() not in BASURA_TIKTOK and e.casefold() != "shorts"
    ]
    return " ".join(["#Shorts"] + [f"#{e}" for e in etiquetas])


def _bloque(valor: str) -> str:
    if not valor:
        return "(vacío)"
    return valor


def paquete(videos: list[dict], fecha: datetime | None = None,
            marca: str = MARCA) -> str:
    """Devuelve el `.md` completo del lote.

    `videos` son dicts con `video_name`, `titulo`, `hashtags` y el `guion`
    original (cualquier dict con las claves de `BLOQUES`). Se toleran los que
    vengan sin guion: salen con los bloques vacíos en vez de romper el paquete,
    porque perder el lote entero por un video sin texto es peor que un hueco.
    """
    momento = fecha or datetime.now(timezone.utc)
    dia = f"{momento:%Y-%m-%d}"
    n = len(videos)

    lineas = [
        f"# Paquete de publicación — {n} reel{'s' if n != 1 else ''} ({marca})",
        "",
        f"Lote generado el {dia}. Cada video está en `videos/{momento:%Y/%m/%d}/`.",
        "",
        "**El nombre del archivo mp4 NUNCA se cambia.** Es el identificador que ya usa el",
        "pipeline de Databricks (`gold.guiones_usados`, `bronze.tiktok_uploads`) y el que",
        "aparece en el borrador de TikTok. El «título» es un campo aparte del formulario de",
        "YouTube, no un renombrado del mp4.",
        "",
        "Cada video trae cinco bloques listos para copiar:",
        "1. **Archivo original** — el nombre exacto del mp4, tal cual está en disco.",
        "2. **Título** — ≤100 caracteres; va al campo «Título» de YouTube.",
        "3. **Descripción** — gancho + cuerpo + CTA + canal. **Sin hashtags dentro.**",
        "4. **Tags** — el campo «Etiquetas» de YouTube, sin `#`, ≤500 caracteres.",
        "5. **Hashtags** — al final de la descripción. YouTube solo cuenta los 3 primeros;",
        "   `#Shorts` va siempre en primer lugar.",
        "",
        "> Regla vigente: el video usa **voz sintética** → al subir, marcar «contenido",
        "> alterado o sintético». Publicar solo en cuenta propia.",
        "",
        "---",
        "",
    ]

    for i, video in enumerate(videos, 1):
        guion = video.get("guion") or {}
        nombre = video.get("video_name", "")
        titulo = video.get("titulo") or titulo_para(guion)
        descripcion = descripcion_para(guion) if guion else ""
        hashes = video.get("hashtags", "")

        lineas += [
            f"## {i}. `{nombre}`",
            "",
            "**Archivo original (no renombrar)**",
            "```",
            nombre,
            "```",
            "",
            "**Título**",
            "```",
            _bloque(titulo),
            "```",
            "",
            "**Descripción**",
            "```",
            _bloque(descripcion),
            "```",
            "",
            "**Tags**",
            "```",
            _bloque(tags_para(hashes)),
            "```",
            "",
            "**Hashtags**",
            "```",
            hashtags_para(hashes),
            "```",
            "",
        ]

    return "\n".join(lineas)


def escribir(videos: list[dict], carpeta: str, fecha: datetime | None = None,
             nombre: str = "descripciones_reels.md") -> str:
    """Escribe el paquete en `carpeta` y devuelve la ruta.

    La carpeta se crea si no existe. El archivo es uno por lote: no se anexa,
    porque dos corridas del mismo día dejarían un documento con videos
    duplicados y sin forma de saber cuál es el vigente.
    """
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(paquete(videos, fecha))
    return ruta
