"""Tests de `slackbot.consultas`: SQL, parametros y conversion de resultados."""

from types import SimpleNamespace

import pytest

from slackbot import consultas


def _respuesta(columnas, datos, estado="SUCCEEDED"):
    return SimpleNamespace(
        status=SimpleNamespace(state=estado),
        manifest=SimpleNamespace(
            schema=SimpleNamespace(
                columns=[SimpleNamespace(name=c) for c in columnas])),
        result=SimpleNamespace(data_array=datos),
    )


class _Cliente:
    """Guarda cada llamada y devuelve la respuesta encolada."""

    def __init__(self, respuestas=None, warehouses=None):
        self.llamadas = []
        self._respuestas = list(respuestas or [])
        self._warehouses = list(warehouses or [])
        self.statement_execution = SimpleNamespace(
            execute_statement=self._ejecutar)
        self.warehouses = SimpleNamespace(list=lambda: iter(self._warehouses))

    def _ejecutar(self, **kw):
        self.llamadas.append(kw)
        return self._respuestas.pop(0)


def test_convierte_filas_a_dicts_por_columna():
    c = _Cliente([_respuesta(["video_name", "status"],
                             [["a.mp4", "SEND_TO_USER_INBOX"]])])
    filas = consultas.ejecutar(c, "SELECT 1", [], "wh1")
    assert filas == [{"video_name": "a.mp4", "status": "SEND_TO_USER_INBOX"}]


def test_resultado_vacio_da_lista_vacia():
    c = _Cliente([_respuesta(["video_name"], None)])
    assert consultas.ejecutar(c, "SELECT 1", [], "wh1") == []


def test_null_se_conserva_como_none():
    c = _Cliente([_respuesta(["video_name", "fail_reason"], [["a.mp4", None]])])
    assert consultas.ejecutar(c, "SELECT 1", [], "wh1")[0]["fail_reason"] is None


def test_estado_no_exitoso_lanza_sin_incluir_la_consulta():
    c = _Cliente([_respuesta(["x"], [], estado="FAILED")])
    with pytest.raises(RuntimeError) as exc:
        consultas.ejecutar(c, "SELECT secreto FROM t", [], "wh1")
    assert "FAILED" in str(exc.value)
    assert "secreto" not in str(exc.value)


def test_warehouse_por_entorno_gana_a_la_lista(monkeypatch):
    monkeypatch.setenv(consultas.VARIABLE_WAREHOUSE, "del-entorno")
    c = _Cliente(warehouses=[SimpleNamespace(id="de-la-lista")])
    assert consultas.id_warehouse(c) == "del-entorno"


def test_warehouse_cae_al_primero_visible(monkeypatch):
    monkeypatch.delenv(consultas.VARIABLE_WAREHOUSE, raising=False)
    c = _Cliente(warehouses=[SimpleNamespace(id="primero"),
                             SimpleNamespace(id="segundo")])
    assert consultas.id_warehouse(c) == "primero"


def test_sin_warehouse_lanza_runtimeerror(monkeypatch):
    monkeypatch.delenv(consultas.VARIABLE_WAREHOUSE, raising=False)
    with pytest.raises(RuntimeError):
        consultas.id_warehouse(_Cliente(warehouses=[]))


def test_listar_pendientes_hace_dos_consultas_y_no_toca_distribucion():
    c = _Cliente([_respuesta(["video_name"], [["a.mp4"]]),
                  _respuesta(["video_name", "status"], [["a.mp4", "SEND"]])])
    datos = consultas.consultar_datos(c, "listar_pendientes", {},
                                      warehouse_id="wh1")
    assert len(c.llamadas) == 2
    assert all("distribucion_video" not in l["statement"] for l in c.llamadas)
    assert datos["generados"] == [{"video_name": "a.mp4"}]
    assert datos["filas"] == []


def test_subidas_se_ordenan_por_fecha_descendente():
    # Sin ORDER BY DESC, clasificar_tiktok mostraria el motivo de un intento viejo.
    c = _Cliente([_respuesta(["video_name"], []),
                  _respuesta(["video_name"], [])])
    consultas.consultar_datos(c, "listar_pendientes", {}, warehouse_id="wh1")
    assert "ORDER BY uploaded_at DESC" in c.llamadas[1]["statement"]


def test_estado_videos_anade_filas_de_distribucion():
    c = _Cliente([_respuesta(["video_name"], []),
                  _respuesta(["video_name"], []),
                  _respuesta(["video_name", "canal"], [["a.mp4", "youtube"]])])
    datos = consultas.consultar_datos(c, "estado_videos", {},
                                      warehouse_id="wh1")
    assert datos["filas"] == [{"video_name": "a.mp4", "canal": "youtube"}]


def test_el_canal_no_se_filtra_en_guiones_usados_porque_no_tiene_columna():
    # guiones_usados no tiene columna `canal`: filtrar ahi romperia la consulta.
    c = _Cliente([_respuesta(["video_name"], []),
                  _respuesta(["video_name"], []),
                  _respuesta(["video_name"], [])])
    consultas.consultar_datos(c, "estado_videos", {"canal": "youtube"},
                              warehouse_id="wh1")
    assert "canal" not in c.llamadas[0]["statement"]
    assert "canal = :canal" in c.llamadas[2]["statement"]


def test_desde_va_como_parametro_tipado_no_interpolado():
    c = _Cliente([_respuesta(["video_name"], []),
                  _respuesta(["video_name"], []),
                  _respuesta(["video_name"], [])])
    consultas.consultar_datos(c, "estado_videos", {"desde": "2026-10-01"},
                              warehouse_id="wh1")
    primera = c.llamadas[0]
    assert "2026-10-01" not in primera["statement"]
    (param,) = primera["parameters"]
    assert (param.name, param.value, param.type) == ("desde", "2026-10-01", "DATE")


def test_los_parametros_son_objetos_del_sdk_no_dicts():
    # Regresion real: con dicts, execute_statement revienta en `p.as_dict()`.
    # Los dobles no lo veian porque nunca llamaban al SDK de verdad.
    c = _Cliente([_respuesta(["video_name"], []),
                  _respuesta(["video_name"], []),
                  _respuesta(["video_name"], [])])
    consultas.consultar_datos(c, "estado_videos", {"desde": "2026-10-01"},
                              warehouse_id="wh1")
    for llamada in c.llamadas:
        for p in llamada["parameters"]:
            assert callable(getattr(p, "as_dict", None)), f"no es del SDK: {p!r}"


def test_accion_desconocida_lanza_valueerror():
    with pytest.raises(ValueError):
        consultas.consultar_datos(_Cliente(), "consultar_estado", {},
                                  warehouse_id="wh1")


# --- Sentencias que no terminan dentro del wait_timeout ---------------------


class _ClientePendiente:
    """Devuelve los estados de la lista, uno por llamada; el ultimo se repite."""

    def __init__(self, estados, columnas=("video_name",), datos=(("a.mp4",),)):
        self._estados = list(estados)
        self._columnas, self._datos = columnas, datos
        self._i = 0
        self.sondeos = 0
        self.statement_execution = self

    def _siguiente(self, dato):
        estado = self._estados[min(self._i, len(self._estados) - 1)]
        self._i += 1
        r = _respuesta(list(self._columnas), self._datos, estado=estado)
        r.statement_id = "s1"
        return r

    def execute_statement(self, **_kw):
        return self._siguiente(True)

    def get_statement(self, statement_id):
        self.sondeos += 1
        return self._siguiente(False)


def _reloj(pasos):
    it = iter(pasos)
    return lambda: next(it)


def test_una_consulta_pendiente_se_sondea_hasta_succeeded():
    """Regresion del smoke test real: la primera consulta tras un rato parado
    daba RuntimeError porque el warehouse serverless arranca en frio y no
    termina dentro de los 30 s de wait_timeout."""
    c = _ClientePendiente(["PENDING", "RUNNING", "SUCCEEDED"])
    dormido = []
    filas = consultas.ejecutar(c, "SELECT 1", [], "wh", esperar=dormido.append,
                               reloj=_reloj([0, 1, 2, 3, 4]))
    assert filas == [{"video_name": "a.mp4"}]
    assert c.sondeos >= 2
    assert len(dormido) == c.sondeos


def test_si_nunca_termina_falla_diciendo_el_estado():
    c = _ClientePendiente(["PENDING"])
    with pytest.raises(RuntimeError, match="PENDING"):
        consultas.ejecutar(c, "SELECT 1", [], "wh", esperar=lambda _s: None,
                           reloj=_reloj([0, 60, 200]))


def test_un_estado_final_distinto_no_se_sondea():
    """FAILED o CANCELED no mejoran esperando: se falla al instante."""
    c = _ClientePendiente(["FAILED"])
    dormido = []
    with pytest.raises(RuntimeError, match="FAILED"):
        consultas.ejecutar(c, "SELECT 1", [], "wh", esperar=dormido.append,
                           reloj=_reloj([0, 1]))
    assert dormido == [] and c.sondeos == 0
