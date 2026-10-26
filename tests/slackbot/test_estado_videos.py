from slackbot.acciones import ESTADOS_OK, agrupar_por_canal, clasificar_tiktok


def test_video_subido_va_a_en_inbox():
    out = clasificar_tiktok(
        [{"video_name": "a.mp4"}],
        [{"video_name": "a.mp4", "status": "SEND_TO_USER_INBOX", "fail_reason": ""}],
    )
    assert out["en_inbox"] == ["a.mp4"]
    assert out["pendientes"] == []


def test_nombre_con_sufijo_v2_cuenta_como_subido():
    # Review Focus #4: sin normalizar, se reportaria como pendiente para siempre.
    out = clasificar_tiktok(
        [{"video_name": "a.mp4"}],
        [{"video_name": "a_v2.mp4", "status": "PUBLISH_COMPLETE", "fail_reason": ""}],
    )
    assert out["en_inbox"] == ["a.mp4"]
    assert out["pendientes"] == []


def test_skipped_inbox_full_es_pendiente_con_motivo():
    out = clasificar_tiktok(
        [{"video_name": "a.mp4"}],
        [{"video_name": "a.mp4", "status": "SKIPPED_INBOX_FULL", "fail_reason": ""}],
    )
    assert out["en_inbox"] == []
    assert out["pendientes"] == [
        {"video_name": "a.mp4", "motivo": "SKIPPED_INBOX_FULL"}
    ]


def test_video_sin_fila_en_uploads_es_pendiente_sin_registro():
    out = clasificar_tiktok([{"video_name": "a.mp4"}], [])
    assert out["pendientes"] == [{"video_name": "a.mp4", "motivo": "sin registro"}]


def test_fail_reason_se_usa_como_motivo():
    out = clasificar_tiktok(
        [{"video_name": "a.mp4"}],
        [{"video_name": "a.mp4", "status": "FAILED", "fail_reason": "http_403"}],
    )
    assert out["pendientes"][0]["motivo"] == "http_403"


def test_varias_filas_con_un_estado_ok_cuentan_como_subido():
    # Un SKIPPED de ayer y un PUBLISH_COMPLETE de hoy: el video esta subido,
    # da igual el orden en que lleguen las filas.
    generados = [{"video_name": "a.mp4"}]
    ayer = {"video_name": "a.mp4", "status": "SKIPPED_INBOX_FULL", "fail_reason": ""}
    hoy = {"video_name": "a.mp4", "status": "PUBLISH_COMPLETE", "fail_reason": ""}
    for subidas in ([ayer, hoy], [hoy, ayer]):
        out = clasificar_tiktok(generados, subidas)
        assert out["en_inbox"] == ["a.mp4"]
        assert out["pendientes"] == []


def test_estados_ok_es_lo_que_dice_el_esquema():
    assert ESTADOS_OK == frozenset({"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"})


def test_agrupar_por_canal_separa_y_ordena():
    filas = [
        {"video_name": "a.mp4", "canal": "youtube", "estado": "PUBLICADO_PRIVADO"},
        {"video_name": "b.mp4", "canal": "facebook", "estado": "FALLIDO"},
        {"video_name": "c.mp4", "canal": "youtube", "estado": "FALLIDO"},
    ]
    out = agrupar_por_canal(filas)
    assert [f["video_name"] for f in out["youtube"]] == ["a.mp4", "c.mp4"]
    assert [f["video_name"] for f in out["facebook"]] == ["b.mp4"]


def test_distribucion_video_no_aporta_tiktok():
    # La tabla no tiene filas de TikTok; el clasificador no debe inventarlas.
    filas = [{"video_name": "a.mp4", "canal": "youtube", "estado": "PUBLICADO_PRIVADO"}]
    assert "tiktok" not in agrupar_por_canal(filas)
