import copy

from slackbot.acciones import JOB_LOTES, TAREAS_CANAL, construir_lanzamiento, lanzar

JOB = {
    "settings": {
        "parameters": [
            {"name": "cantidad", "default": "10"},
            {"name": "hashtags", "default": "motivacional,fyp"},
            {"name": "min_likes", "default": "20000"},
            {"name": "top_n", "default": "30"},
            {"name": "max_pendientes", "default": "0"},
        ],
        # Las mismas tareas que el job vivo, con la subida de TikTok incluida:
        # un fixture sin subir_borradores no puede probar el filtro de TikTok.
        "tasks": [{"task_key": k} for k in
                  ("guiones_60s", "generar_video", "subir_borradores",
                   "publicar_youtube", "publicar_facebook")],
    }
}


def test_solo_sobrescribe_lo_mencionado():
    out = construir_lanzamiento({"cantidad": 3, "hashtags": ["motivacion"],
                                 "min_likes": None, "top_n": None,
                                 "usar_apify": None, "canales": ["tiktok"]}, JOB)
    assert out["job_parameters"]["cantidad"] == "3"
    assert out["job_parameters"]["hashtags"] == "motivacion"
    # Lo no mencionado conserva el valor del job, no se borra.
    assert out["job_parameters"]["min_likes"] == "20000"
    assert out["job_parameters"]["top_n"] == "30"


def test_canal_ausente_desactiva_su_tarea():
    out = construir_lanzamiento({"cantidad": 1, "hashtags": None, "min_likes": None,
                                 "top_n": None, "usar_apify": None,
                                 "canales": ["tiktok"]}, JOB)
    claves = {t["task_key"] for t in out["tasks"]}
    assert TAREAS_CANAL["youtube"] not in claves
    assert TAREAS_CANAL["facebook"] not in claves
    assert "generar_video" in claves


def test_canal_presente_conserva_la_tarea():
    out = construir_lanzamiento({"cantidad": 1, "hashtags": None, "min_likes": None,
                                 "top_n": None, "usar_apify": None,
                                 "canales": ["tiktok", "youtube"]}, JOB)
    claves = {t["task_key"] for t in out["tasks"]}
    assert TAREAS_CANAL["youtube"] in claves
    assert TAREAS_CANAL["facebook"] not in claves


def test_tiktok_sin_pedirlo_quita_la_subida():
    # TikTok no tiene tarea propia de publicacion: su subida vive en
    # subir_borradores. Dejarla correr cuando no se pidio TikTok gastaria una
    # de las 5 subidas de la ventana de 24 h en una red que nadie pidio.
    out = construir_lanzamiento({"cantidad": 1, "hashtags": None, "min_likes": None,
                                 "top_n": None, "usar_apify": None,
                                 "canales": ["facebook"]}, JOB)
    claves = {t["task_key"] for t in out["tasks"]}
    assert TAREAS_CANAL["tiktok"] not in claves
    assert "listar_pendientes" not in claves  # colgaba de subir_borradores
    assert TAREAS_CANAL["facebook"] in claves


def test_tiktok_pedido_conserva_la_subida():
    out = construir_lanzamiento({"cantidad": 1, "hashtags": None, "min_likes": None,
                                 "top_n": None, "usar_apify": None,
                                 "canales": ["tiktok"]}, JOB)
    claves = {t["task_key"] for t in out["tasks"]}
    assert TAREAS_CANAL["tiktok"] in claves


# --- Casos adicionales ---

JOB_CON_DEPENDENCIAS = {
    "settings": {
        "parameters": [{"name": "cantidad", "default": "10"}],
        "tasks": [
            {"task_key": "generar_video"},
            {"task_key": "subir_borradores", "depends_on": [{"task_key": "generar_video"}]},
            {"task_key": "publicar_youtube", "depends_on": [{"task_key": "subir_borradores"}]},
            {"task_key": "publicar_facebook", "depends_on": [{"task_key": "subir_borradores"}]},
            {"task_key": "cerrar_distribucion",
             "depends_on": [{"task_key": "subir_borradores"},
                            {"task_key": "publicar_youtube"},
                            {"task_key": "publicar_facebook"}],
             "run_if": "ALL_DONE"},
        ],
    }
}


def _parametros(**sobre):
    base = {"cantidad": None, "hashtags": None, "min_likes": None,
            "top_n": None, "usar_apify": None, "canales": ["tiktok"]}
    base.update(sobre)
    return base


def test_no_hay_dependencias_colgantes_tras_filtrar():
    out = construir_lanzamiento(_parametros(), JOB_CON_DEPENDENCIAS)
    claves = {t["task_key"] for t in out["tasks"]}
    assert claves == {"generar_video", "subir_borradores", "cerrar_distribucion"}
    for tarea in out["tasks"]:
        for dep in tarea.get("depends_on", []):
            assert dep["task_key"] in claves
    cierre = next(t for t in out["tasks"] if t["task_key"] == "cerrar_distribucion")
    assert cierre["depends_on"] == [{"task_key": "subir_borradores"}]
    assert cierre["run_if"] == "ALL_DONE"


def test_no_muta_el_job_de_entrada():
    original = copy.deepcopy(JOB_CON_DEPENDENCIAS)
    construir_lanzamiento(_parametros(canales=["tiktok"], cantidad=2), JOB_CON_DEPENDENCIAS)
    assert JOB_CON_DEPENDENCIAS == original


def test_usar_apify_se_pasa_en_minusculas():
    out = construir_lanzamiento(_parametros(usar_apify=False), JOB)
    assert out["job_parameters"]["usar_apify"] == "false"
    out = construir_lanzamiento(_parametros(usar_apify=True), JOB)
    assert out["job_parameters"]["usar_apify"] == "true"


def test_hashtags_se_unen_con_coma():
    out = construir_lanzamiento(_parametros(hashtags=["motivacion", "fyp"]), JOB)
    assert out["job_parameters"]["hashtags"] == "motivacion,fyp"


class _JobSdk:
    def as_dict(self):
        return copy.deepcopy(JOB_CON_DEPENDENCIAS)


class _Espera:
    run_id = 987


class _Run:
    run_page_url = "https://ejemplo.azuredatabricks.net/?o=1#job/1/run/987"


class _Jobs:
    def __init__(self):
        self.run_now_kwargs = None

    def get(self, job_id):
        assert job_id == JOB_LOTES
        return _JobSdk()

    def run_now(self, **kwargs):
        self.run_now_kwargs = kwargs
        return _Espera()

    def get_run(self, run_id):
        assert run_id == 987
        return _Run()


class _Cliente:
    def __init__(self):
        self.jobs = _Jobs()


def test_lanzar_devuelve_run_id_y_url_y_solo_ejecuta_run_now():
    cliente = _Cliente()
    out = lanzar(cliente, _parametros(cantidad=2, canales=["tiktok"]))
    assert out == {"run_id": 987, "run_page_url": _Run.run_page_url}
    assert cliente.jobs.run_now_kwargs["job_id"] == JOB_LOTES
    assert cliente.jobs.run_now_kwargs["job_parameters"]["cantidad"] == "2"


def test_lanzar_pasa_only_con_las_tareas_filtradas():
    # Sin `only` el filtro de canales no llega a Databricks: las tareas viven
    # en la definicion del job, no en job_parameters. Este test es el que
    # habria cazado que una orden de solo TikTok publicara en Facebook.
    cliente = _Cliente()
    lanzar(cliente, _parametros(cantidad=1, canales=["tiktok"]))
    only = cliente.jobs.run_now_kwargs["only"]
    assert TAREAS_CANAL["youtube"] not in only
    assert TAREAS_CANAL["facebook"] not in only
    assert TAREAS_CANAL["tiktok"] in only


def test_si_get_run_falla_la_corrida_no_se_reporta_como_error():
    # La corrida ya esta lanzada: devolver un error haria que el usuario
    # reintentara y lanzaria una segunda corrida.
    class _JobsSinRun(_Jobs):
        def get_run(self, run_id):
            raise RuntimeError("timeout")

    class _ClienteSinRun:
        def __init__(self):
            self.jobs = _JobsSinRun()

    out = lanzar(_ClienteSinRun(), _parametros(cantidad=1, canales=["tiktok"]))
    assert out["run_id"] == 987
    assert out["run_page_url"] is None


def _params(canales):
    return {"cantidad": None, "hashtags": None, "min_likes": None, "top_n": None,
            "usar_apify": None, "canales": canales}


def test_tiktok_fija_max_pendientes_al_cupo_del_inbox():
    out = construir_lanzamiento(_params(["tiktok"]), JOB)
    assert out["job_parameters"]["max_pendientes"] == "5"


def test_sin_tiktok_conserva_max_pendientes_del_job():
    out = construir_lanzamiento(_params(["facebook"]), JOB)
    assert out["job_parameters"]["max_pendientes"] == "0"


def test_max_pendientes_explicito_no_se_pisa():
    p = _params(["tiktok"])
    p["max_pendientes"] = 3
    assert construir_lanzamiento(p, JOB)["job_parameters"]["max_pendientes"] == "3"
