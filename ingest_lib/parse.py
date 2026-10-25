"""Normalización de los items crudos del actor de Apify.

Este módulo no importa pyspark ni dbutils: recibe dicts y devuelve tuplas, de
forma que el notebook solo tenga que construir el DataFrame y los tests puedan
ejercitar el parseo sin un cluster.
"""

from datetime import datetime

# Orden exacto de las columnas que emite `parse_items`. El esquema del notebook
# tiene que declararse en este mismo orden.
COLUMNS = (
    "video_id", "created_at", "text_language", "is_ad", "is_sponsored",
    "has_shop_product", "digg_count", "share_count", "play_count",
    "collect_count", "comment_count", "repost_count", "author_id",
    "author_name", "author_verified", "author_fans", "author_hearts",
    "author_videos", "duration_sec", "music_id", "video_url",
    "source_hashtag", "apify_run_id",
)


def _int(value):
    """Convierte a int o devuelve None. El actor mezcla enteros, strings y nulls."""
    if value is None or value == "":
        return None
    return int(value)


def _timestamp(value):
    """Parsea el formato ISO que usa el actor (`2026-10-23T18:04:05.000Z`)."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def parse_item(item, run_id):
    """Convierte un item del actor en una tupla con el orden de `COLUMNS`.

    Devuelve None si el item no tiene los identificadores mínimos, que es la
    señal de que el actor devolvió algo distinto de un video.
    """
    video_id = item.get("id")
    author = item.get("authorMeta") or {}
    if not video_id or not author.get("id"):
        return None

    video = item.get("videoMeta") or {}
    music = item.get("musicMeta") or {}
    music_id = music.get("musicId")

    return (
        str(video_id),
        _timestamp(item.get("createTimeISO")),
        item.get("textLanguage"),
        bool(item.get("isAd", False)),
        bool(item.get("isSponsored", False)),
        bool(item.get("hasTikTokShopProduct", False)),
        _int(item.get("diggCount")),
        _int(item.get("shareCount")),
        _int(item.get("playCount")),
        _int(item.get("collectCount")),
        _int(item.get("commentCount")),
        _int(item.get("repostCount")),
        str(author["id"]),
        author.get("name"),
        bool(author.get("verified", False)),
        _int(author.get("fans")),
        _int(author.get("heart")),
        _int(author.get("video")),
        _int(video.get("duration")),
        str(music_id) if music_id else None,
        item.get("webVideoUrl"),
        item.get("input") or (item.get("searchHashtag") or {}).get("name"),
        run_id,
    )


def parse_items(items, run_id):
    """Aplica `parse_item` a una lista y devuelve (filas válidas, cuántas se descartaron)."""
    rows, skipped = [], 0
    for item in items:
        row = parse_item(item, run_id)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, skipped
