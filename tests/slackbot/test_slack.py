from slackbot.slack import (MAX_LISTA, tarjeta_confirmacion, texto_estado_videos,
                            texto_resultado)


def _textos(bloques):
    return " ".join(str(b) for b in bloques)


def test_la_tarjeta_muestra_lo_entendido():
    bloques = tarjeta_confirmacion(
        "lanzar_lote",
        {"cantidad": 3, "hashtags": ["motivacion"], "min_likes": 100000,
         "canales": ["tiktok", "youtube"]},
        {"top_n": "30", "min_likes": "20000"},
        valor_boton="abc123",
    )
    texto = _textos(bloques)
    assert "Cantidad: 3" in texto
    assert "motivacion" in texto
    assert "100000" in texto


def test_lo_no_mencionado_dice_por_defecto():
    bloques = tarjeta_confirmacion(
        "lanzar_lote", {"cantidad": 1, "hashtags": None, "min_likes": None,
                        "canales": ["tiktok"]},
        {"hashtags": "motivacional,fyp", "top_n": "30"}, valor_boton="x")
    assert "por defecto" in _textos(bloques).lower()


def test_la_tarjeta_lleva_el_valor_firmado_en_el_boton():
    bloques = tarjeta_confirmacion("lanzar_lote", {"cantidad": 1, "hashtags": None,
                                                   "min_likes": None,
                                                   "canales": ["tiktok"]}, {},
                                   valor_boton="firma-123")
    botones = [e for b in bloques if b.get("type") == "actions" for e in b["elements"]]
    assert any(e.get("value") == "firma-123" for e in botones)
    assert {e["text"]["text"] for e in botones} == {"Ejecutar", "Cancelar"}


def test_botones_tienen_action_id_y_cancelar_lleva_el_mismo_valor():
    bloques = tarjeta_confirmacion("lanzar_lote", {"cantidad": 1, "canales": ["tiktok"]},
                                   {}, valor_boton="v")
    botones = {e["action_id"]: e for b in bloques if b.get("type") == "actions"
               for e in b["elements"]}
    assert botones["ejecutar"]["value"] == "v"
    assert botones["cancelar"]["value"] == "v"


def test_none_muestra_el_valor_por_defecto_real_y_no_none():
    bloques = tarjeta_confirmacion(
        "lanzar_lote", {"cantidad": 1, "hashtags": None, "min_likes": None,
                        "canales": ["tiktok"]},
        {"hashtags": "motivacional,fyp"}, valor_boton="x")
    texto = _textos(bloques)
    assert "motivacional,fyp" in texto
    assert "None" not in texto


def test_lista_larga_se_recorta_y_avisa():
    clasificacion = {"en_inbox": [f"v{i}.mp4" for i in range(30)], "pendientes": []}
    texto = texto_estado_videos(clasificacion, {})
    assert f"v{MAX_LISTA - 1}.mp4" in texto
    assert "v29.mp4" not in texto
    assert "y 15 más" in texto


def test_canal_sin_registro_se_dice_explicitamente():
    texto = texto_estado_videos({"en_inbox": [], "pendientes": []},
                                {"youtube": []})
    assert "sin registro" in texto.lower()


def test_estado_muestra_pendientes_con_motivo_y_filas_de_canal():
    clasificacion = {"en_inbox": ["a.mp4"],
                     "pendientes": [{"video_name": "b.mp4", "motivo": "sin subir"}]}
    por_canal = {"facebook": [{"video_name": "c.mp4", "estado": "OK", "url": "https://f/c"}]}
    texto = texto_estado_videos(clasificacion, por_canal)
    for esperado in ("a.mp4", "b.mp4", "sin subir", "c.mp4", "OK", "https://f/c"):
        assert esperado in texto


def test_estado_con_todo_vacio_devuelve_texto_util():
    texto = texto_estado_videos({"en_inbox": [], "pendientes": []}, {})
    assert texto == "No hay videos en el inbox, ni pendientes, ni registros por canal."


def test_resultado_incluye_el_enlace():
    texto = texto_resultado({"run_id": 42, "estado": "RUNNING",
                             "run_page_url": "https://x/run/42"})
    assert "https://x/run/42" in texto
    assert "42" in texto


def test_resultado_nombra_las_tareas_fallidas():
    texto = texto_resultado({"run_id": 1, "estado": "FAILED",
                             "run_page_url": "u",
                             "tareas_fallidas": ["publicar_facebook"]})
    assert "publicar_facebook" in texto


def test_resultado_sin_enlace_no_revienta_ni_dice_none():
    texto = texto_resultado({"run_id": 1, "estado": "FAILED", "run_page_url": None})
    assert isinstance(texto, str)
    assert "None" not in texto
    assert "FAILED" in texto


def test_fila_de_canal_sin_url_no_pinta_none():
    texto = texto_estado_videos(
        {"en_inbox": [], "pendientes": []},
        {"youtube": [{"video_name": "a.mp4", "estado": "ERROR", "url": None}]})
    assert "a.mp4" in texto
    assert "None" not in texto


def test_pendiente_sin_motivo_no_revienta():
    texto = texto_estado_videos(
        {"en_inbox": [], "pendientes": [{"video_name": "b.mp4"}]}, {})
    assert "b.mp4" in texto
    assert "None" not in texto


def test_lista_o_cadena_vacia_cuenta_como_no_mencionada():
    bloques = tarjeta_confirmacion(
        "lanzar_lote", {"cantidad": 1, "hashtags": [], "canales": [], "min_likes": ""},
        {"hashtags": "fyp", "canales": "tiktok"}, valor_boton="x")
    texto = " ".join(b["text"]["text"] for b in bloques if b["type"] == "section")
    assert "Hashtags:" not in texto and "Canales:" not in texto
    lineas = [l for b in bloques if b["type"] == "section"
              for l in b["text"]["text"].splitlines() if l.startswith("•")]
    assert lineas and not any(l.rstrip().endswith(":") for l in lineas)
    assert "hashtags: fyp (por defecto)" in texto
    assert "canales: tiktok (por defecto)" in texto
    assert "min_likes" in texto


def test_parametro_desconocido_se_muestra():
    bloques = tarjeta_confirmacion(
        "lanzar_lote", {"cantidad": 1, "dias": 7, "canales": ["tiktok"]}, {},
        valor_boton="x")
    assert "dias: 7" in _textos(bloques)


def test_recorte_exactamente_max_lista_no_avisa():
    texto = texto_estado_videos(
        {"en_inbox": [f"v{i}.mp4" for i in range(MAX_LISTA)], "pendientes": []}, {})
    assert f"v{MAX_LISTA - 1}.mp4" in texto
    assert "más" not in texto


def test_recorte_con_un_elemento_de_mas_dice_y_1_mas():
    texto = texto_estado_videos(
        {"en_inbox": [f"v{i}.mp4" for i in range(MAX_LISTA + 1)], "pendientes": []}, {})
    assert "y 1 más" in texto
    assert f"v{MAX_LISTA}.mp4" not in texto


def test_pendientes_y_filas_usan_el_mismo_recorte():
    pendientes = [{"video_name": f"p{i}.mp4", "motivo": "m"} for i in range(20)]
    filas = [{"video_name": f"f{i}.mp4", "estado": "OK", "url": "u"} for i in range(20)]
    texto = texto_estado_videos({"en_inbox": [], "pendientes": pendientes},
                                {"youtube": filas})
    assert f"p{MAX_LISTA - 1}.mp4" in texto and f"p{MAX_LISTA}.mp4" not in texto
    assert f"f{MAX_LISTA - 1}.mp4" in texto and f"f{MAX_LISTA}.mp4" not in texto
    assert texto.count("y 5 más") == 2
