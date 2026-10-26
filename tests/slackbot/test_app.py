import hashlib
import hmac
import json
import time

import pytest

from slackbot.app import crear_app

SECRETO = "secreto-de-prueba"


def _firmar(ts, cuerpo):
    base = f"v0:{ts}:".encode() + cuerpo
    return "v0=" + hmac.new(SECRETO.encode(), base, hashlib.sha256).hexdigest()


def _post(cliente, ruta, payload, con_firma=True, retry=None):
    cuerpo = json.dumps(payload).encode()
    ts = str(int(time.time()))
    cabeceras = {"Content-Type": "application/json"}
    if con_firma:
        cabeceras["X-Slack-Signature"] = _firmar(ts, cuerpo)
        cabeceras["X-Slack-Request-Timestamp"] = ts
    if retry is not None:
        cabeceras["X-Slack-Retry-Num"] = str(retry)
    return cliente.post(ruta, data=cuerpo, headers=cabeceras)


VALOR_OK = json.dumps({"id": "uso-unico-1", "a": "lanzar_lote",
                       "p": {"cantidad": 1, "canales": ["tiktok"]}})


def _crudo(cliente, ruta, cuerpo, firma_ok=True, ctype="application/json", retry=None):
    ts = str(int(time.time()))
    h = {"Content-Type": ctype}
    if firma_ok is True:
        h["X-Slack-Signature"] = _firmar(ts, cuerpo)
        h["X-Slack-Request-Timestamp"] = ts
    elif firma_ok == "mala":
        h["X-Slack-Signature"] = "v0=" + "0" * 64
        h["X-Slack-Request-Timestamp"] = ts
    if retry is not None:
        h["X-Slack-Retry-Num"] = str(retry)
    return cliente.post(ruta, data=cuerpo, headers=h)


def _deps(**extra):
    base = {
        "interpretar": lambda texto: {"accion": "no_entendido", "parametros": {}},
        "lanzar": lambda params: {"run_id": 1, "run_page_url": "u"},
        "leer_job": lambda: {"settings": {"parameters": [], "tasks": []}},
        "consultar": lambda **kw: {},
        "responder": lambda *a, **k: None,
    }
    base.update(extra)
    return base


@pytest.fixture
def cliente():
    app = crear_app({"slack_signing_secret": SECRETO}, _deps())
    return app.test_client()


def test_sin_firma_devuelve_401(cliente):
    r = _post(cliente, "/slack/events", {"type": "event_callback"}, con_firma=False)
    assert r.status_code == 401


def test_url_verification_responde_el_challenge(cliente):
    r = _post(cliente, "/slack/events", {"type": "url_verification", "challenge": "abc"})
    assert r.status_code == 200
    assert r.get_json()["challenge"] == "abc"


def test_reintento_de_slack_no_lanza_nada():
    # Review Focus #3: parametros VALIDOS; sin la guarda se publicaria tarjeta.
    enviados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "lanzar_lote",
                                                 "parametros": {"cantidad": 1,
                                                                "canales": ["tiktok"]}},
                          responder=lambda *a, **k: enviados.append(a)))
    r = _post(app.test_client(), "/slack/events",
              {"type": "event_callback", "event": {"text": "lanza", "channel": "C1"}},
              retry=1)
    assert r.status_code == 200
    assert enviados == []


def test_orden_no_valida_no_ofrece_boton():
    enviados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "lanzar_lote",
                                                 "parametros": {"cantidad": "10"}},
                          responder=lambda *a, **k: enviados.append(a)))
    _post(app.test_client(), "/slack/events",
          {"type": "event_callback", "event": {"text": "x", "channel": "C1"}})
    assert "Ejecutar" not in str(enviados)


def test_instagram_no_se_ejecuta():
    enviados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "lanzar_lote",
                                                 "parametros": {"cantidad": 1,
                                                                "canales": ["instagram"]}},
                          responder=lambda *a, **k: enviados.append(a)))
    _post(app.test_client(), "/slack/events",
          {"type": "event_callback", "event": {"text": "a instagram", "channel": "C1"}})
    assert "instagram" in str(enviados).lower()
    assert "Ejecutar" not in str(enviados)


def test_boton_ejecutado_dos_veces_solo_lanza_una():
    lanzados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(lanzar=lambda p: (lanzados.append(p),
                                            {"run_id": 1, "run_page_url": "u"})[1]))
    payload = {"type": "block_actions", "actions": [
        {"action_id": "ejecutar", "value": VALOR_OK}]}
    c = app.test_client()
    _post(c, "/slack/interactions", payload)
    _post(c, "/slack/interactions", payload)
    assert len(lanzados) == 1


def test_llamada_llm_usa_json_schema_temperatura_cero_y_clave_en_cabecera():
    from slackbot.app import llamar_llm_openrouter
    from slackbot.interpretar import ESQUEMA

    visto = {}

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{\"accion\": \"no_entendido\", \"parametros\": {}}"}}]}

    def post(url, **kw):
        visto.update(kw, url=url)
        return _R()

    r = llamar_llm_openrouter("clave-x", "modelo-x", "hola", ESQUEMA, post=post)
    assert r == {"accion": "no_entendido", "parametros": {}}
    assert visto["json"]["temperature"] == 0
    rf = visto["json"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == ESQUEMA
    assert visto["headers"]["Authorization"] == "Bearer clave-x"
    assert "clave-x" not in visto["url"]


def test_boton_cancelar_no_lanza_nada():
    lanzados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(lanzar=lambda p: lanzados.append(p)))
    _post(app.test_client(), "/slack/interactions", {"type": "block_actions", "actions": [
        {"action_id": "cancelar", "value": ""}]})
    assert lanzados == []


RUTAS = ["/slack/events", "/slack/interactions"]


@pytest.mark.parametrize("ruta", RUTAS)
def test_firma_incorrecta_sobre_cuerpo_valido_es_401(cliente, ruta):
    r = _crudo(cliente, ruta, b'{"type": "block_actions"}', firma_ok="mala")
    assert r.status_code == 401


def test_interactions_sin_firma_es_401(cliente):
    r = _crudo(cliente, "/slack/interactions", b"{}", firma_ok=False)
    assert r.status_code == 401


@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("cuerpo,ctype", [
    (b"{not json", "application/json"),
    (b"payload=", "application/x-www-form-urlencoded"),
    (b"payload=%7Bnot", "application/x-www-form-urlencoded"),
    (b"payload=%ff%fe", "application/x-www-form-urlencoded"),
    (bytes([0xff, 0xfe]), "application/x-www-form-urlencoded"),
    (b"[1, 2]", "application/json"),
])
def test_cuerpo_mal_formado_nunca_da_500(ruta, cuerpo, ctype):
    lanzados = []
    app = crear_app({"slack_signing_secret": SECRETO}, _deps(lanzar=lanzados.append))
    r = _crudo(app.test_client(), ruta, cuerpo, ctype=ctype)
    assert r.status_code in (200, 400)
    assert lanzados == []


def test_interactions_descarta_reintentos():
    lanzados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(lanzar=lambda p: lanzados.append(p) or {"run_id": 1}))
    r = _post(app.test_client(), "/slack/interactions",
              {"type": "block_actions", "actions": [
                  {"action_id": "ejecutar", "value": VALOR_OK}]}, retry=1)
    assert r.status_code == 200
    assert lanzados == []


@pytest.mark.parametrize("valor", ["basura", "", "{}", '{"id": "x"}',
                                   json.dumps({"id": "y", "a": "lanzar_lote", "p": {}}),
                                   json.dumps({"id": "z", "a": "lanzar_lote",
                                               "p": {"cantidad": "10", "canales": ["tiktok"]}})])
def test_value_invalido_no_lanza_y_responde_negativa(valor):
    lanzados, enviados = [], []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(lanzar=lambda p: lanzados.append(p),
                          responder=lambda *a, **k: enviados.append(a)))
    _post(app.test_client(), "/slack/interactions", {"type": "block_actions", "actions": [
        {"action_id": "ejecutar", "value": valor}]})
    assert lanzados == []
    assert "ya no es v" in str(enviados)
    assert "Ejecutar" not in str(enviados)


def test_responder_se_llama_fuera_del_candado():
    # Un responder lento (la negativa de «ya se ejecuto») no debe bloquear
    # otros clics: con responder dentro del candado el tercer clic no llega.
    import threading
    lento_dentro, soltar, tercero = threading.Event(), threading.Event(), threading.Event()
    n = []

    def responder(*a, **k):
        if "ya se ejecuto" in a[1]:
            n.append(1)
            if len(n) == 1:
                lento_dentro.set()
                soltar.wait(5)
            else:
                tercero.set()

    app = crear_app({"slack_signing_secret": SECRETO}, _deps(responder=responder))
    c = app.test_client()
    pl = {"type": "block_actions", "actions": [{"action_id": "ejecutar", "value": VALOR_OK}]}
    _post(c, "/slack/interactions", pl)
    t = threading.Thread(target=lambda: _post(c, "/slack/interactions", pl))
    t.start()
    assert lento_dentro.wait(5)
    _post(c, "/slack/interactions", pl)
    assert tercero.wait(1)
    soltar.set()
    t.join()


def test_predeterminados_ilegibles_se_avisa_en_la_tarjeta(caplog):
    enviados = []

    def leer_job():
        raise RuntimeError("secreto-xyz")

    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "lanzar_lote",
                                                 "parametros": {"cantidad": 1,
                                                                "canales": ["tiktok"]}},
                          leer_job=leer_job,
                          responder=lambda *a, **k: enviados.append(a)))
    with caplog.at_level("ERROR", logger="slackbot"):
        _post(app.test_client(), "/slack/events",
              {"type": "event_callback", "event": {"text": "x", "channel": "C1"}})
    assert "Ejecutar" in str(enviados)
    assert "no pude leer los valores por defecto" in str(enviados).lower()
    assert "RuntimeError" in caplog.text
    assert "secreto-xyz" not in caplog.text


def test_respuesta_fallida_tras_lanzar_deja_log_con_run_id(caplog):
    def responder(*a, **k):
        raise ConnectionError("token-abc")

    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(lanzar=lambda p: {"run_id": 4242, "run_page_url": "u"},
                          responder=responder))
    with caplog.at_level("ERROR", logger="slackbot"):
        _post(app.test_client(), "/slack/interactions",
              {"type": "block_actions", "actions": [
                  {"action_id": "ejecutar", "value": VALOR_OK}]})
    assert "4242" in caplog.text and "lanzada" in caplog.text
    assert "ConnectionError" in caplog.text
    assert "token-abc" not in caplog.text


def test_orden_sin_accion_responde_no_entendido():
    enviados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"parametros": {}},
                          responder=lambda *a, **k: enviados.append(a)))
    _post(app.test_client(), "/slack/events",
          {"type": "event_callback", "event": {"text": "x", "channel": "C1"}})
    assert "No entendi" in str(enviados)


def test_enviar_slack_ok_false_lanza_con_error_y_sin_token():
    from slackbot.app import enviar_slack

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False, "error": "not_in_channel"}

    with pytest.raises(RuntimeError) as e:
        enviar_slack("xoxb-secreto", "C1", "hola", post=lambda *a, **k: R())
    assert "not_in_channel" in str(e.value) and "xoxb-secreto" not in str(e.value)


def test_consultar_que_lanza_valueerror_responde_al_usuario():
    enviados = []

    def consultar(**kw):
        raise ValueError("invalid literal")

    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "consultar_estado",
                                                 "parametros": {"run_id": "#2283"}},
                          consultar=consultar,
                          responder=lambda *a, **k: enviados.append(a)))
    _post(app.test_client(), "/slack/events",
          {"type": "event_callback", "event": {"text": "x", "channel": "C1"}})
    assert len(enviados) == 1 and "No pude" in enviados[0][1]


def test_listar_pendientes_cruza_con_clasificar_y_respeta_max():
    enviados = []
    datos = {"generados": [{"video_name": f"v{i}.mp4"} for i in range(3)],
             "subidas": [], "filas": []}
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "listar_pendientes",
                                                 "parametros": {"max": 2}},
                          consultar=lambda **kw: datos,
                          responder=lambda *a, **k: enviados.append(a)))
    _post(app.test_client(), "/slack/events",
          {"type": "event_callback", "event": {"text": "x", "channel": "C1"}})
    texto = enviados[0][1]
    assert "v0" in texto and "v1" in texto and "v2" not in texto


def test_cancelar_invalida_la_tarjeta():
    lanzados = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(lanzar=lambda p: lanzados.append(p) or {"run_id": 1}))
    c = app.test_client()
    _post(c, "/slack/interactions", {"type": "block_actions", "actions": [
        {"action_id": "cancelar", "value": VALOR_OK}]})
    _post(c, "/slack/interactions", {"type": "block_actions", "actions": [
        {"action_id": "ejecutar", "value": VALOR_OK}]})
    assert lanzados == []
