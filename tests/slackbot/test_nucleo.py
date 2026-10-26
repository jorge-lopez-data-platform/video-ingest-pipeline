import json



from slackbot.nucleo import Nucleo


def _nucleo(**extra):
    enviados, lanzados = [], []
    deps = {
        "interpretar": lambda t: {"accion": "no_entendido", "parametros": {}},
        "lanzar": lambda p: lanzados.append(p) or {"run_id": 7},
        "leer_job": lambda: {"settings": {"parameters": []}},
        "consultar": lambda accion, parametros: {"no_disponible": True},
        "responder": lambda canal, texto, bloques=None: enviados.append((canal, texto)),
    }
    deps.update(extra)
    return Nucleo({"slack_signing_secret": "s"}, deps), enviados, lanzados


def _valor(ident="abc"):
    return json.dumps({"id": ident, "a": "lanzar_lote", "p": {"cantidad": 1, "canales": ["tiktok"]}})


def test_atender_no_entendido_responde():
    n, enviados, _ = _nucleo()
    n.atender("hola", "C1")
    assert enviados and enviados[0][0] == "C1"
    # Sin esto el test pasaba con cualquier respuesta, incluso una vacia.
    assert "no entend" in enviados[0][1].lower()


def test_ejecutar_un_solo_uso_por_instancia():
    n, enviados, lanzados = _nucleo()
    n.ejecutar(_valor(), "C1")
    n.ejecutar(_valor(), "C1")
    assert len(lanzados) == 1
    assert "ya se ejecuto" in enviados[-1][1]


def test_usados_no_se_comparte_entre_instancias():
    a, _, la = _nucleo()
    b, _, lb = _nucleo()
    a.ejecutar(_valor(), "C1")
    b.ejecutar(_valor(), "C1")
    assert len(la) == 1 and len(lb) == 1


def test_cancelar_consume_el_id():
    n, enviados, lanzados = _nucleo()
    n.cancelar(_valor(), "C1")
    n.ejecutar(_valor(), "C1")
    assert lanzados == []
    assert enviados[0][1] == "Cancelado."


def test_ejecutar_valor_invalido():
    n, enviados, lanzados = _nucleo()
    n.ejecutar("no-json", "C1")
    assert lanzados == [] and "ya no es valida" in enviados[0][1]


def test_decir_no_propaga_fallos_de_responder():
    def mal(*a, **k):
        raise RuntimeError("x")
    n, _, _ = _nucleo(responder=mal)
    n.decir("C1", "hola")  # no lanza


def test_enviar_slack_y_llm_usan_secretos_de_la_instancia():
    n = Nucleo({"slack_signing_secret": "s", "slack_bot_token": "tok",
                "openrouter_api_key": "k"}, {})
    vistos = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "choices": [{"message": {"content": "{}"}}]}

    n.enviar_slack("C1", "hola", post=lambda u, **kw: vistos.append(kw) or R())
    assert vistos[0]["headers"]["Authorization"] == "Bearer tok"
    assert n.llamar_llm_openrouter("m", "t", {}, post=lambda u, **kw: vistos.append(kw) or R()) == {}
    assert vistos[1]["headers"]["Authorization"] == "Bearer k"


def test_el_prompt_nombra_todas_las_acciones_del_esquema():
    """Regresion del smoke test real: «estado de los videos» devolvio
    «No entendi la orden». El prompt de sistema no describia ninguna accion,
    asi que el modelo solo tenia los nombres del enum para adivinar. Si se
    anade una accion al esquema y no al prompt, el bot vuelve a no entenderla."""
    from slackbot import interpretar as interp
    from slackbot.nucleo import INSTRUCCION

    for accion in interp.ACCIONES:
        assert accion in INSTRUCCION, accion


def test_el_prompt_indica_que_no_se_inventen_parametros():
    from slackbot.nucleo import INSTRUCCION

    assert "null" in INSTRUCCION
