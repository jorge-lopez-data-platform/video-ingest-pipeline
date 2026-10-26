from unittest.mock import MagicMock

import logging

import pytest

import urllib.request

from slackbot.socket_mode import (_Vistos, PUERTO_POR_DEFECTO,
                                  arrancar_servidor_salud,
                                  crear_servidor_salud, crear_procesador,
                                  ejecutar_con_reconexion, puerto_salud,
                                  traducir)


def _ev(**kw):
    ev = {"type": "app_mention", "text": "estado", "channel": "C1", "ts": "1.0"}
    ev.update(kw)
    return {"type": "events_api", "payload": {"event": ev}}


def _correr(sobre, vistos=None):
    n = MagicMock()
    traducir(sobre["type"], sobre["payload"], n, vistos)
    return n


def test_mencion_llama_atender_con_texto_y_canal():
    n = _correr(_ev())
    n.atender.assert_called_once_with("estado", "C1")


def test_mensaje_directo_llama_atender():
    # En un MD no hace falta mencionar al bot: escribirle ya es dirigirse a el.
    n = _correr(_ev(type="message", channel_type="im"))
    n.atender.assert_called_once_with("estado", "C1")


def test_mensaje_de_canal_sin_mencion_se_ignora():
    """Un `message` de canal llega con TODA la conversacion del canal, mencione
    o no al bot. Sin este filtro, cada linea de charla costaba una llamada al
    LLM y una respuesta «no te entendi» en el canal."""
    n = _correr(_ev(type="message", text="hola equipo, mañana reunion"))
    assert not n.method_calls


def test_mensaje_de_canal_sin_channel_type_se_ignora():
    # Slack no siempre manda channel_type; la ausencia no es una exencion.
    n = _correr(_ev(type="message"))
    assert not n.method_calls


def test_propio_bot_y_subtype_no_llaman_nada():
    for extra in ({"bot_id": "B1"}, {"subtype": "bot_message"},
                  {"subtype": "message_changed"}):
        n = _correr(_ev(**extra))
        assert not n.method_calls


def test_mencion_y_message_duplicados_se_atienden_una_vez():
    # Slack puede entregar la misma mencion como app_mention y como message.
    # En un MD las dos formas se atienden, asi que aqui se deduplican.
    v, n = _Vistos(), MagicMock()
    for tipo in ("app_mention", "message"):
        s = _ev(type=tipo, channel_type="im")
        traducir(s["type"], s["payload"], n, v)
    n.atender.assert_called_once()


def _accion(action_id, valor='{"id":"x"}'):
    return {"type": "interactive", "payload": {
        "type": "block_actions", "channel": {"id": "C9"},
        "actions": [{"action_id": action_id, "value": valor}]}}


def test_block_actions_despacha_por_action_id():
    n = _correr(_accion("ejecutar"))
    n.ejecutar.assert_called_once_with('{"id":"x"}', "C9")
    n.cancelar.assert_not_called()
    n = _correr(_accion("cancelar"))
    n.cancelar.assert_called_once_with('{"id":"x"}', "C9")
    n.ejecutar.assert_not_called()


def test_action_id_desconocido_no_llama_nada():
    assert not _correr(_accion("otro")).method_calls


def test_sobres_malformados_no_lanzan():
    n = MagicMock()
    for tipo, p in [("events_api", None), ("events_api", {}), ("events_api", {"event": "x"}),
                    ("events_api", {"event": {"type": "message"}}),
                    ("interactive", {"actions": "x"}),
                    ("interactive", {"type": "block_actions", "actions": [1]}),
                    ("desconocido", {}), (None, None)]:
        traducir(tipo, p, n)
    assert not n.method_calls


def test_fallo_del_nucleo_no_propaga_ni_filtra(caplog):
    n = MagicMock()
    n.atender.side_effect = RuntimeError("secreto")
    s = _ev()
    with caplog.at_level(logging.DEBUG):
        traducir(s["type"], s["payload"], n)
    assert "secreto" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_fallo_de_una_accion_no_salta_las_demas():
    n = MagicMock()
    n.ejecutar.side_effect = RuntimeError("x")
    payload = {"type": "block_actions", "channel": {"id": "C9"}, "actions": [
        {"action_id": "ejecutar", "value": "a"},
        {"action_id": "cancelar", "value": "b"}]}
    traducir("interactive", payload, n)
    n.cancelar.assert_called_once_with("b", "C9")


def test_actions_vacio_o_no_lista():
    for acciones in ([], "x"):
        n = MagicMock()
        traducir("interactive", {"type": "block_actions", "channel": {"id": "C"},
                                 "actions": acciones}, n)
        assert not n.method_calls


def test_block_actions_sin_channel_usa_container():
    p = _accion("ejecutar")["payload"]
    del p["channel"]
    p["container"] = {"channel_id": "C7"}
    n = MagicMock()
    traducir("interactive", p, n)
    n.ejecutar.assert_called_once_with('{"id":"x"}', "C7")


def test_block_actions_sin_canal_alguno_se_descarta():
    p = _accion("ejecutar")["payload"]
    del p["channel"]
    n = MagicMock()
    traducir("interactive", p, n)
    assert not n.method_calls


def test_message_deleted_y_view_submission_se_ignoran():
    assert not _correr(_ev(subtype="message_deleted")).method_calls
    assert not _correr({"type": "interactive",
                        "payload": {"type": "view_submission"}}).method_calls


def test_mismo_ts_en_dos_canales_se_atiende_en_ambos():
    v, n = _Vistos(), MagicMock()
    for canal in ("C1", "C2"):
        s = _ev(channel=canal)
        traducir(s["type"], s["payload"], n, v)
    assert n.atender.call_count == 2


def test_sin_ts_no_se_deduplica():
    v, n = _Vistos(), MagicMock()
    for _ in range(2):
        s = _ev(ts=None)
        traducir(s["type"], s["payload"], n, v)
    assert n.atender.call_count == 2


def test_ack_antes_del_nucleo():
    orden = []
    client = MagicMock()
    client.send_socket_mode_response.side_effect = lambda r: orden.append(("ack", r.envelope_id))
    n = MagicMock()
    n.atender.side_effect = lambda *a: orden.append(("nucleo",))
    req = MagicMock(envelope_id="E1", type="events_api", payload=_ev()["payload"])
    crear_procesador(n)(client, req)
    assert orden == [("ack", "E1"), ("nucleo",)]


def test_si_el_ack_falla_traducir_sigue():
    client = MagicMock()
    client.send_socket_mode_response.side_effect = OSError("caido")
    n = MagicMock()
    req = MagicMock(envelope_id="E1", type="events_api", payload=_ev()["payload"])
    crear_procesador(n)(client, req)
    n.atender.assert_called_once_with("estado", "C1")


class _SocketFalso:
    """is_connected() solo pasa a True tras `conecta_en` intentos; connect_to_new_endpoint
    no lanza ante fallo de red (como el SDK real)."""

    def __init__(self, conecta_en=None, error=None):
        self.intentos, self.conecta_en, self.error = 0, conecta_en, error

    def is_connected(self):
        return self.conecta_en is not None and self.intentos >= self.conecta_en

    def connect_to_new_endpoint(self):
        self.intentos += 1
        if self.error:
            raise self.error


def test_reconexion_con_espera_creciente_sin_excepciones():
    c = _SocketFalso(conecta_en=None)
    esperas, vueltas = [], iter([True] * 4 + [False])
    ejecutar_con_reconexion(c, dormir=esperas.append, seguir=lambda: next(vueltas))
    assert esperas == [1.0, 2.0, 4.0, 8.0]
    assert c.intentos == 4


def test_reconexion_resetea_espera_al_conectar():
    c = _SocketFalso(conecta_en=3)
    esperas, vueltas = [], iter([True] * 4 + [False])
    ejecutar_con_reconexion(c, sondeo=5.0, dormir=esperas.append,
                            seguir=lambda: next(vueltas))
    assert esperas == [1.0, 2.0, 5.0, 5.0]


def test_espera_tiene_tope():
    c = _SocketFalso(conecta_en=None)
    esperas, vueltas = [], iter([True] * 10 + [False])
    ejecutar_con_reconexion(c, dormir=esperas.append, seguir=lambda: next(vueltas))
    assert max(esperas) == 60.0


@pytest.mark.parametrize("codigo", ["invalid_auth", "not_allowed_token_type"])
def test_error_de_autenticacion_no_se_reintenta(codigo):
    err = RuntimeError("x")
    err.response = {"error": codigo}
    c = _SocketFalso(error=err)
    with pytest.raises(SystemExit):
        ejecutar_con_reconexion(c, dormir=lambda s: None)
    assert c.intentos == 1


# --- Servidor de salud ------------------------------------------------------


def test_puerto_salud_lee_la_variable_de_la_plataforma():
    assert puerto_salud({"DATABRICKS_APP_PORT": "8123"}) == 8123


def test_puerto_salud_cae_a_8000_si_no_hay_variable():
    assert puerto_salud({}) == PUERTO_POR_DEFECTO == 8000


def test_puerto_salud_ignora_un_valor_invalido():
    # Sin esto, un valor basura haria que la app no enlazara nunca y la
    # plataforma la reiniciaria en bucle.
    assert puerto_salud({"DATABRICKS_APP_PORT": "ocho mil"}) == 8000


def _cerrar(servidor, hilo):
    servidor.shutdown()
    servidor.server_close()
    hilo.join(timeout=5)


def test_el_servidor_de_salud_responde_200():
    servidor, hilo = arrancar_servidor_salud(0)
    try:
        puerto = servidor.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/", timeout=5) as r:
            assert r.status == 200
            assert r.read() == b"ok"
    finally:
        _cerrar(servidor, hilo)


def test_el_servidor_de_salud_responde_a_cualquier_ruta():
    # La plataforma sondea rutas que no controlamos: un 404 tambien contaria
    # como caida, asi que cualquier ruta tiene que dar 200.
    servidor, hilo = arrancar_servidor_salud(0)
    try:
        puerto = servidor.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{puerto}/healthz?probe=1", timeout=5) as r:
            assert r.status == 200
    finally:
        _cerrar(servidor, hilo)


def test_main_arranca_el_puerto_antes_del_bucle(monkeypatch):
    """Sin esto, borrar la llamada de `main` dejaria la app reiniciandose para
    siempre y ningun test lo notaria."""
    import sys
    import types

    from slackbot import socket_mode

    orden = []

    class _Socket:
        def __init__(self, **kw):
            # Regresion de la ronda 1: con auto_reconnect activo el SDK
            # reconecta en paralelo al bucle propio, sin espera creciente.
            assert kw.get("auto_reconnect_enabled") is False, "auto_reconnect activo"
            self.socket_mode_request_listeners = []

        def connect_to_new_endpoint(self):
            pass

    def _cliente():
        # Crear el WorkspaceClient es una llamada de red: tambien va detras
        # del puerto.
        orden.append("cliente")
        return object()

    modulos = {
        "databricks": types.ModuleType("databricks"),
        "databricks.sdk": types.SimpleNamespace(WorkspaceClient=_cliente),
        "slack_sdk": types.SimpleNamespace(WebClient=lambda **kw: object()),
        "slack_sdk.socket_mode": types.SimpleNamespace(SocketModeClient=_Socket),
    }
    for nombre, modulo in modulos.items():
        monkeypatch.setitem(sys.modules, nombre, modulo)
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-x")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(socket_mode, "construir_deps", lambda *a: {})
    monkeypatch.setattr(socket_mode, "arrancar_servidor_salud",
                        lambda puerto=None: orden.append("salud"))
    monkeypatch.setattr(socket_mode, "ejecutar_con_reconexion",
                        lambda socket: orden.append("bucle"))

    socket_mode.main()

    # La salud va antes que CUALQUIER llamada de red, no solo antes del bucle.
    assert orden == ["salud", "cliente", "bucle"]


def test_los_tokens_salen_del_scope_si_no_estan_en_el_entorno(monkeypatch):
    """En una Databricks App nadie exporta variables: el contenedor arranca solo.
    Si el codigo exigiera el entorno, el despliegue no levantaria nunca."""
    from slackbot import config, socket_mode

    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    monkeypatch.setattr(config, "secreto",
                        lambda cliente, clave: {"slack_app_token": "xapp-s"}[clave])
    assert socket_mode._secreto_o_entorno(object(), "SLACK_APP_TOKEN",
                                          "slack_app_token") == "xapp-s"


def test_un_salto_de_linea_al_final_del_secreto_se_recorta(monkeypatch):
    """Regresion real del primer despliegue: el token se cargo con `Read-Host`
    y tuberia y arrastro un `\\r\\n`. Con el, el SDK falla con
    `Invalid header value ... \\r\\n` y el bot no conecta nunca."""
    from slackbot import config, socket_mode

    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    monkeypatch.setattr(config, "secreto", lambda cliente, clave: "xapp-s\r\n")
    assert socket_mode._secreto_o_entorno(object(), "SLACK_APP_TOKEN",
                                          "slack_app_token") == "xapp-s"


def test_un_salto_de_linea_en_la_variable_de_entorno_tambien_se_recorta(monkeypatch):
    from slackbot import socket_mode

    monkeypatch.setenv("SLACK_APP_TOKEN", "  xapp-x\n")
    assert socket_mode._secreto_o_entorno(object(), "SLACK_APP_TOKEN",
                                          "slack_app_token") == "xapp-x"


def test_el_entorno_gana_al_scope(monkeypatch):
    from slackbot import config, socket_mode

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-entorno")
    monkeypatch.setattr(config, "secreto", lambda cliente, clave: "xoxb-scope")
    assert socket_mode._secreto_o_entorno(object(), "SLACK_BOT_TOKEN",
                                          "slack_bot_token") == "xoxb-entorno"


def test_sin_entorno_ni_scope_sale_con_systemexit_sin_filtrar(monkeypatch, caplog):
    from slackbot import config, socket_mode

    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

    def revienta(cliente, clave):
        raise RuntimeError("valor secreto que no debe verse")

    monkeypatch.setattr(config, "secreto", revienta)
    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            socket_mode._secreto_o_entorno(object(), "SLACK_APP_TOKEN",
                                           "slack_app_token")
    mensaje = str(exc.value)
    assert "SLACK_APP_TOKEN" in mensaje
    # El tipo se conserva: sin el, «falta la clave» y «falta READ» se ven igual.
    assert "RuntimeError" in mensaje
    # Pero ni el valor ni el mensaje de la excepcion salen, ni por el log.
    assert "valor secreto" not in mensaje
    assert "valor secreto" not in caplog.text


def test_un_token_con_el_prefijo_correcto_pasa_intacto():
    from slackbot.socket_mode import _validar_forma

    assert _validar_forma("SLACK_APP_TOKEN", "xapp-1-abc") == "xapp-1-abc"
    assert _validar_forma("SLACK_BOT_TOKEN", "xoxb-1-abc") == "xoxb-1-abc"


def test_una_clave_sin_regla_de_prefijo_no_se_valida():
    # OPENROUTER_API_KEY no tiene forma fija: no se le impone ninguna.
    from slackbot.socket_mode import _validar_forma

    assert _validar_forma("OPENROUTER_API_KEY", "sk-or-v1-x") == "sk-or-v1-x"


@pytest.mark.parametrize("variable,valor", [
    ("SLACK_APP_TOKEN", "xoxb-cruzado"),
    ("SLACK_BOT_TOKEN", "xapp-cruzado"),
    ("SLACK_APP_TOKEN", ""),
])
def test_un_token_con_prefijo_equivocado_falla_sin_filtrar_el_valor(
        variable, valor, caplog):
    """Regresion de la fuga real: con un token invalido, `slack_sdk` escribe la
    cabecera Authorization entera en su error y el token acabo en claro en los
    logs de la App. Si el valor mal formado no llega al SDK, no hay fuga."""
    from slackbot.socket_mode import _validar_forma

    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            _validar_forma(variable, valor)
    mensaje = str(exc.value)
    assert variable in mensaje
    if valor:
        assert valor not in mensaje
        assert valor[:4] not in mensaje
        assert valor not in caplog.text
    else:
        # Un token vacio no aporta ningun caracter que pueda filtrarse; se
        # comprueba que el mensaje nombra la variable y nada mas utilizable.
        assert "longitud 0" in mensaje


def test_main_valida_la_forma_de_los_tokens(monkeypatch):
    """Sin esta llamada, `_validar_forma` seria codigo muerto y la fuga seguiria
    abierta: el SDK recibiria igualmente el valor mal formado."""
    import sys
    import types

    from slackbot import socket_mode

    visto = {}

    class _Socket:
        def __init__(self, **kw):
            visto["app_token"] = kw["app_token"]
            self.socket_mode_request_listeners = []

    modulos = {
        "databricks": types.ModuleType("databricks"),
        "databricks.sdk": types.SimpleNamespace(WorkspaceClient=lambda: object()),
        "slack_sdk": types.SimpleNamespace(WebClient=lambda **kw: object()),
        "slack_sdk.socket_mode": types.SimpleNamespace(SocketModeClient=_Socket),
    }
    for nombre, modulo in modulos.items():
        monkeypatch.setitem(sys.modules, nombre, modulo)
    monkeypatch.setenv("SLACK_APP_TOKEN", "xoxb-equivocado")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(socket_mode, "construir_deps", lambda *a: {})
    monkeypatch.setattr(socket_mode, "arrancar_servidor_salud", lambda puerto=None: None)
    monkeypatch.setattr(socket_mode, "ejecutar_con_reconexion", lambda socket: None)

    with pytest.raises(SystemExit):
        socket_mode.main()
    assert "app_token" not in visto  # jamas se construyo el cliente con el valor


def test_arrancar_devuelve_un_hilo_daemon_vivo():
    servidor, hilo = arrancar_servidor_salud(0)
    try:
        assert hilo.daemon is True
        assert hilo.is_alive() is True
        assert hilo.name == "salud"
    finally:
        _cerrar(servidor, hilo)
