from datetime import datetime

from ingest_lib.parse import COLUMNS, parse_item, parse_items


def item_valido(**overrides):
    base = {
        "id": "7429675110409260310",
        "createTimeISO": "2026-10-23T18:04:05.000Z",
        "textLanguage": "es",
        "diggCount": 1200,
        "shareCount": 80,
        "playCount": 45000,
        "collectCount": 40,
        "commentCount": 12,
        "repostCount": 3,
        "authorMeta": {
            "id": "6812345678901234567",
            "name": "cuenta_propia",
            "verified": False,
            "fans": 3000,
            "heart": 90000,
            "video": 42,
        },
        "videoMeta": {"duration": 24},
        "musicMeta": {"musicId": "7000"},
        "webVideoUrl": "https://www.tiktok.com/@cuenta_propia/video/7429675110409260310",
        "input": "databricks",
    }
    base.update(overrides)
    return base


def test_columnas_coinciden_con_la_tupla():
    fila = parse_item(item_valido(), "run-1")
    assert len(fila) == len(COLUMNS)


def test_mapea_campos_anidados():
    fila = dict(zip(COLUMNS, parse_item(item_valido(), "run-1")))
    assert fila["video_id"] == "7429675110409260310"
    assert fila["author_name"] == "cuenta_propia"
    assert fila["author_fans"] == 3000
    assert fila["duration_sec"] == 24
    assert fila["music_id"] == "7000"
    assert fila["created_at"] == datetime(2026, 10, 23, 18, 4, 5)
    assert fila["apify_run_id"] == "run-1"
    assert fila["source_hashtag"] == "databricks"


def test_descarta_item_sin_id():
    assert parse_item(item_valido(id=None), "run-1") is None


def test_descarta_item_sin_autor():
    assert parse_item(item_valido(authorMeta={}), "run-1") is None


def test_tolera_metricas_ausentes():
    fila = dict(zip(COLUMNS, parse_item(item_valido(playCount=None, diggCount=""), "run-1")))
    assert fila["play_count"] is None
    assert fila["digg_count"] is None


def test_acepta_metricas_como_string():
    fila = dict(zip(COLUMNS, parse_item(item_valido(playCount="45000"), "run-1")))
    assert fila["play_count"] == 45000


def test_cuenta_los_descartados():
    items = [item_valido(), {"id": None}, item_valido(id="99")]
    filas, descartados = parse_items(items, "run-1")
    assert len(filas) == 2
    assert descartados == 1


def test_music_id_ausente_queda_en_none():
    fila = dict(zip(COLUMNS, parse_item(item_valido(musicMeta={}), "run-1")))
    assert fila["music_id"] is None
