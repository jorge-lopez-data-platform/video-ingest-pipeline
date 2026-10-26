import logging

import pytest

from slackbot.interpretar import ESQUEMA, _motivo, interpretar, validar


def test_orden_completa_se_extrae():
    crudo = {
        "accion": "lanzar_lote",
        "parametros": {"cantidad": 3, "hashtags": ["motivacion"],
                       "min_likes": 100000, "top_n": None,
                       "usar_apify": None, "canales": ["tiktok", "youtube"]},
    }
    out = interpretar("lanza 3 videos con #motivacion y 100000 likes",
                      llamar_llm=lambda _t, _e: crudo)
    assert out["parametros"]["cantidad"] == 3
    assert out["parametros"]["min_likes"] == 100000
    # Review Focus: lo no mencionado queda en None, nunca inventado.
    assert out["parametros"]["top_n"] is None


def test_cantidad_como_string_se_rechaza():
    # Review Focus #2: no se trunca ni se convierte en silencio.
    with pytest.raises(ValueError, match="cantidad"):
        validar("lanzar_lote", {"cantidad": "10", "hashtags": ["a"], "canales": ["tiktok"]})


def test_cantidad_como_float_se_rechaza():
    with pytest.raises(ValueError, match="cantidad"):
        validar("lanzar_lote", {"cantidad": 10.0, "hashtags": ["a"], "canales": ["tiktok"]})


def test_cantidad_fuera_de_rango_se_rechaza():
    with pytest.raises(ValueError, match="cantidad"):
        validar("lanzar_lote", {"cantidad": 0, "hashtags": ["a"], "canales": ["tiktok"]})
    with pytest.raises(ValueError, match="cantidad"):
        validar("lanzar_lote", {"cantidad": 21, "hashtags": ["a"], "canales": ["tiktok"]})


def test_cantidad_booleana_se_rechaza():
    # isinstance(True, int) es True: sin este caso, un `true` del LLM se
    # convertiría en cantidad=1 sin que nadie lo pidiera.
    with pytest.raises(ValueError, match="cantidad"):
        validar("lanzar_lote", {"cantidad": True, "hashtags": ["a"], "canales": ["tiktok"]})


def test_min_likes_negativo_se_rechaza():
    with pytest.raises(ValueError, match="min_likes"):
        validar("lanzar_lote", {"cantidad": 1, "hashtags": ["a"],
                                "min_likes": -1, "canales": ["tiktok"]})


def test_hashtags_vacio_es_none_no_lista_vacia():
    # Review Focus #1: has_vacío no debe traducirse a "vaciar los del job".
    out = validar("lanzar_lote", {"cantidad": 1, "hashtags": [],
                                  "canales": ["tiktok"]})
    assert out["hashtags"] is None


def test_hashtags_se_normalizan_sin_almohadilla():
    out = validar("lanzar_lote", {"cantidad": 1, "hashtags": ["#motivacion ", "amor"],
                                  "canales": ["tiktok"]})
    assert out["hashtags"] == ["motivacion", "amor"]


def test_canal_invalido_se_rechaza():
    with pytest.raises(ValueError, match="canal"):
        validar("lanzar_lote", {"cantidad": 1, "hashtags": ["a"], "canales": ["instagram"]})


def test_sin_canales_se_rechaza():
    with pytest.raises(ValueError, match="canal"):
        validar("lanzar_lote", {"cantidad": 1, "hashtags": ["a"], "canales": []})


def test_respuesta_fuera_de_esquema_pide_aclaracion():
    out = interpretar("hola", llamar_llm=lambda _t, _e: {"accion": "inventada"})
    assert out["accion"] == "no_entendido"


def test_llm_caido_pide_aclaracion():
    def revienta(_t, _e):
        raise RuntimeError("timeout")
    out = interpretar("lanza videos", llamar_llm=revienta)
    assert out["accion"] == "no_entendido"


def test_el_esquema_declara_las_cuatro_acciones():
    nombres = set(ESQUEMA["properties"]["accion"]["enum"])
    assert {"lanzar_lote", "consultar_estado", "listar_pendientes",
            "estado_videos", "no_entendido"} == nombres


def test_valueerror_de_validar_se_propaga():
    # Requisito de la spec: un parametro fuera de rango no se convierte en
    # "no_entendido"; el llamador decide que responder.
    crudo = {"accion": "lanzar_lote",
             "parametros": {"cantidad": 999, "hashtags": ["a"], "canales": ["tiktok"]}}
    with pytest.raises(ValueError, match="cantidad"):
        interpretar("lanza 999 videos", llamar_llm=lambda _t, _e: crudo)


def test_parametros_none_no_revienta():
    out = interpretar("hola", llamar_llm=lambda _t, _e: {"accion": "no_entendido"})
    assert out == {"accion": "no_entendido", "parametros": {}}


def test_consulta_filtra_claves_inventadas():
    # additionalProperties: False es una promesa al modelo, no una garantia:
    # una clave inventada no debe llegar a la consulta de la tarea 5.
    crudo = {"accion": "estado_videos",
             "parametros": {"canal": "tiktok", "borrame": "DROP TABLE"}}
    out = interpretar("que se subio a tiktok", llamar_llm=lambda _t, _e: crudo)
    assert out["parametros"] == {"desde": None, "canal": "tiktok"}


def test_consulta_con_canal_ajeno_se_rechaza():
    crudo = {"accion": "estado_videos", "parametros": {"canal": "instagram"}}
    with pytest.raises(ValueError, match="canal"):
        interpretar("que se subio a instagram", llamar_llm=lambda _t, _e: crudo)


def test_listar_pendientes_con_max_booleano_se_rechaza():
    crudo = {"accion": "listar_pendientes", "parametros": {"max": True}}
    with pytest.raises(ValueError, match="max"):
        interpretar("lista pendientes", llamar_llm=lambda _t, _e: crudo)


def test_top_n_gigante_se_rechaza():
    crudo = {"accion": "lanzar_lote",
             "parametros": {"cantidad": 1, "hashtags": ["a"],
                            "canales": ["tiktok"], "top_n": 10**9}}
    with pytest.raises(ValueError, match="top_n"):
        interpretar("lanza uno", llamar_llm=lambda _t, _e: crudo)


# --- Diagnostico del fallo (regresion del «no entendi» mudo) -----------------


class _Respuesta:
    def __init__(self, estado, cuerpo):
        self.status_code = estado
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


class _HTTPError(Exception):
    def __init__(self, estado, cuerpo):
        super().__init__("boom")
        self.response = _Respuesta(estado, cuerpo)


def test_el_fallo_del_llm_se_registra_con_su_motivo(caplog):
    """Antes se tragaba la excepcion: «no entendi» era indistinguible de
    «clave invalida», «modelo inexistente» o «el proveedor rechaza el esquema».
    Paso de verdad: el bot respondio «No entendi la orden» en Slack y el log de
    la App no decia absolutamente nada."""

    def revienta(_t, _e):
        raise _HTTPError(401, {"error": {"message": "No auth credentials found"}})

    with caplog.at_level(logging.DEBUG):
        assert interpretar("estado de los videos", revienta)["accion"] == "no_entendido"
    assert "HTTP 401" in caplog.text
    assert "No auth credentials found" in caplog.text
    assert "_HTTPError" in caplog.text


def test_el_texto_del_usuario_no_acaba_en_el_log(caplog):
    """El cuerpo del error de OpenRouter puede traer la peticion entera, que
    incluye el texto escrito por la persona. Solo se loguea el campo error."""

    def revienta(_t, _e):
        raise _HTTPError(400, {"error": {"message": "bad"}, "echo": "mi frase secreta"})

    with caplog.at_level(logging.DEBUG):
        interpretar("mi frase secreta", revienta)
    assert "mi frase secreta" not in caplog.text


def test_una_accion_desconocida_deja_rastro(caplog):
    with caplog.at_level(logging.DEBUG):
        out = interpretar("x", llamar_llm=lambda _t, _e: {"accion": "volar"})
    assert out["accion"] == "no_entendido"
    assert "volar" in caplog.text


def test_el_motivo_sin_respuesta_http_no_revienta():
    assert _motivo(RuntimeError("sin red")) == "sin respuesta HTTP"


def test_el_motivo_acota_el_mensaje_del_proveedor():
    largo = _motivo(_HTTPError(500, {"error": "x" * 5000}))
    assert len(largo) < 260
    assert "HTTP 500" in largo


def test_el_motivo_admite_el_error_como_objeto_o_como_texto():
    assert "cuota" in _motivo(_HTTPError(429, {"error": {"code": 429, "message": "cuota"}}))
    assert "cuota" in _motivo(_HTTPError(429, {"error": "cuota"}))


# --- Compatibilidad con salida estructurada del proveedor -------------------


def _recorrer(esquema):
    yield esquema
    for hijo in esquema.get("properties", {}).values():
        yield from _recorrer(hijo)
    for clave in ("anyOf",):
        for hijo in esquema.get(clave, []):
            yield from _recorrer(hijo)
    if "items" in esquema:
        yield from _recorrer(esquema["items"])


def test_ningun_tipo_es_una_lista():
    """Regresion del HTTP 400 «Provider returned error» en el smoke test real.
    `"type": ["integer", "null"]` es JSON Schema valido y los dobles de test lo
    aceptaban, pero Anthropic lo rechaza en salida estructurada y la peticion
    entera falla. Lo opcional va con `anyOf` + `{"type": "null"}`."""
    for nodo in _recorrer(ESQUEMA):
        if "type" in nodo:
            assert not isinstance(nodo["type"], list), nodo


def test_lo_opcional_se_expresa_con_anyof_y_null():
    for nodo in _recorrer(ESQUEMA):
        if "anyOf" in nodo:
            assert {"type": "null"} in nodo["anyOf"], nodo


def test_el_enum_no_mezcla_null():
    """`enum: [..., None]` mete un null dentro de un enum de strings; el
    proveedor lo rechaza. Lo opcional ya lo cubre `anyOf`."""
    for nodo in _recorrer(ESQUEMA):
        if "enum" in nodo:
            assert None not in nodo["enum"], nodo


def test_todos_los_campos_de_parametros_son_required():
    """El modo estricto del proveedor exige que `required` nombre todas las
    claves de `properties`; si falta una, la peticion se rechaza."""
    props = ESQUEMA["properties"]["parametros"]
    assert set(props["required"]) == set(props["properties"])


def test_sin_rangos_en_el_esquema():
    """Los rangos se aplican en `validar`, no en el esquema: el modo estricto
    del proveedor no los admite, y ademas el codigo es la garantia real."""
    for nodo in _recorrer(ESQUEMA):
        assert "minimum" not in nodo and "maximum" not in nodo, nodo
