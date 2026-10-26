# Bot de Slack para Databricks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar órdenes al pipeline en lenguaje natural desde Slack —lanzar lotes, consultar runs, listar pendientes y saber qué videos se subieron y cuáles no— con confirmación obligatoria antes de ejecutar nada.

**Architecture:** Una Databricks App (Flask) expone dos rutas —`/slack/events` y `/slack/interactions`— ambas con firma verificada. El texto se traduce a una acción tipada con JSON Schema contra OpenRouter; la acción se valida contra rangos, se presenta como tarjeta Block Kit con un `value` firmado de uso único, y solo el clic ejecuta la llamada a la API de Jobs o el SQL.

**Tech Stack:** Python 3.11, Flask, `requests` (OpenRouter + Slack Web API), `databricks-sdk` (Jobs API + SQL statement execution), pytest.

**Spec:** `docs/superpowers/specs/2026-10-24-bot-slack-databricks-design.md`

## Global Constraints

- Ningún token se imprime, ni se escribe en logs, ni viaja en una URL. Los de Slack y OpenRouter van en cabecera `Authorization`.
- Se leen del scope `tiktok_pipeline`: `slack_bot_token`, `slack_signing_secret`, `openrouter_api_key`. No se crean claves nuevas.
- Instagram queda fuera: si la orden lo pide, se responde que el canal no existe y no se ejecuta nada.
- Solo cuenta y contenido propios; el bot no crea, modifica ni borra jobs.
- Job de lotes: `tiktok_lote_manual_10`, id `638404369584915`.
- Firma de Slack: HMAC-SHA256 sobre `v0:{timestamp}:{cuerpo}`, ventana de 5 minutos.
- Tablas: `tiktok.gold.guiones_usados`, `tiktok.bronze.tiktok_uploads`, `tiktok.bronze.distribucion_video`.
- Estados de TikTok que cuentan como subido: `SEND_TO_USER_INBOX`, `PUBLISH_COMPLETE`.

## Review Focus

Cada línea lleva su test en la tarea que la posee.

1. **`hashtags` vacío o ausente** (`[]`, `""`, `null`) → debe tratarse como «no especificado», no como «vaciar hashtags del job».
2. **`cantidad` como float o string** (`"10"`, `10.0`) del LLM → debe rechazarse si no es entero, no truncarse en silencio.
3. **Reintento de Slack** (`X-Slack-Retry-Num`) → no debe producir un segundo run.
4. **Nombre con sufijo `_v2`** en `guiones_usados` vs `tiktok_uploads` → debe normalizarse en ambos lados o se reportarán videos subidos como pendientes.
5. **Timestamp de firma viejo** (>5 min) → 401, aunque el HMAC sea correcto.

---

## File Structure

| Ruta | Responsabilidad |
|---|---|
| `slackbot/firma.py` | Verificar firma de Slack. Sin dependencias del resto. |
| `slackbot/interpretar.py` | Texto → acción tipada vía OpenRouter. Valida contra esquema y rangos. |
| `slackbot/nombres.py` | `canonico()` — normalizar sufijo `_v2`. Compartido. |
| `slackbot/acciones.py` | Efectos: lanzar lote, consultar runs, clasificar videos. |
| `slackbot/slack.py` | Block Kit: tarjeta de confirmación, resultado, listas. |
| `slackbot/config.py` | Lectura de secretos del scope. Único punto que los toca. |
| `slackbot/app.py` | Flask: las dos rutas, verificación, despacho, hilo. |
| `tests/slackbot/` | Un fichero de test por módulo. |

---

### Task 1: Verificación de firma

**Files:**
- Create: `slackbot/__init__.py` (vacío)
- Create: `slackbot/firma.py`
- Test: `tests/slackbot/__init__.py` (vacío), `tests/slackbot/test_firma.py`

**Interfaces:**
- Consumes: nada.
- Produces: `firma_valida(secret: str, timestamp: str, cuerpo: bytes, firma: str, ahora: float | None = None) -> bool`

- [ ] **Step 1: Write the failing test**

```python
import hashlib
import hmac

from slackbot.firma import firma_valida

SECRETO = "8f14e45fceea167a5a36dedd4bea2543"
CUERPO = b'{"type":"event_callback"}'
AHORA = 1_800_000_000.0


def firmar(ts: str, cuerpo: bytes) -> str:
    base = f"v0:{ts}:".encode() + cuerpo
    return "v0=" + hmac.new(SECRETO.encode(), base, hashlib.sha256).hexdigest()


def test_firma_correcta_y_reciente_pasa():
    ts = str(int(AHORA))
    assert firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_firma_con_cuerpo_distinto_falla():
    ts = str(int(AHORA))
    assert not firma_valida(SECRETO, ts, b"otro", firmar(ts, CUERPO), ahora=AHORA)


def test_firma_con_secreto_distinto_falla():
    ts = str(int(AHORA))
    base = f"v0:{ts}:".encode() + CUERPO
    ajena = "v0=" + hmac.new(b"otro", base, hashlib.sha256).hexdigest()
    assert not firma_valida(SECRETO, ts, CUERPO, ajena, ahora=AHORA)


def test_timestamp_viejo_falla_aunque_el_hmac_sea_correcto():
    # Review Focus #5: fuera de la ventana de 5 minutos no vale.
    ts = str(int(AHORA - 301))
    assert not firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_timestamp_del_futuro_falla():
    # Reloj adelantado no debe abrir una ventana indefinida.
    ts = str(int(AHORA + 301))
    assert not firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_firma_ausente_o_mal_formada_falla():
    ts = str(int(AHORA))
    assert not firma_valida(SECRETO, ts, CUERPO, "", ahora=AHORA)
    assert not firma_valida(SECRETO, ts, CUERPO, "v0=zzz", ahora=AHORA)
    assert not firma_valida(SECRETO, "no-numero", CUERPO, firmar(ts, CUERPO), ahora=AHORA)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_firma.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'slackbot.firma'`

- [ ] **Step 3: Implement `firma_valida` in `slackbot/firma.py`**

Comparación con `hmac.compare_digest`, nunca `==`. Ventana: `abs(ahora - int(timestamp)) > 300` → `False`. Cualquier `ValueError` al parsear el timestamp o el hex devuelve `False`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_firma.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add slackbot/__init__.py slackbot/firma.py tests/slackbot/
git commit -m "feat(slackbot): verificacion de firma de Slack"
```

---

### Task 2: Interpretación del lenguaje natural

**Files:**
- Create: `slackbot/interpretar.py`
- Test: `tests/slackbot/test_interpretar.py`

**Interfaces:**
- Consumes: `openrouter_api_key` del scope (vía `slackbot.config`, Task 7 — mientras tanto `interpretar` recibe el llamador por parámetro).
- Produces:
  - `ESQUEMA: dict` — JSON Schema.
  - `interpretar(texto: str, llamar_llm: Callable[[str, dict], dict]) -> dict` — devuelve `{"accion": str, "parametros": dict}` ya validado, o `{"accion": "no_entendido", ...}`.
  - `validar(accion: str, parametros: dict) -> dict` — lanza `ValueError` con el motivo si está fuera de rango.
  - Constantes `MAX_CANTIDAD = 20`, `MAX_LIKES = 10_000_000`, `CANALES = ("tiktok", "youtube", "facebook")`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from slackbot.interpretar import ESQUEMA, interpretar, validar


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_interpretar.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implement `ESQUEMA`, `validar`, `interpretar` in `slackbot/interpretar.py`**

`interpretar` llama al LLM con `response_format={"type": "json_schema", ...}`, `temperature=0`, y pasa la respuesta por `validar`. Un `ValueError` de `validar` **se propaga** (el llamador decide); un fallo de red o un JSON sin `accion` produce `no_entendido`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_interpretar.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add slackbot/interpretar.py tests/slackbot/test_interpretar.py
git commit -m "feat(slackbot): interpretacion de lenguaje natural a accion tipada"
```

---

### Task 3: Normalización de nombres

**Files:**
- Create: `slackbot/nombres.py`
- Test: `tests/slackbot/test_nombres.py`

**Interfaces:**
- Produces: `canonico(nombre: str) -> str` — `"x_v2.mp4"` → `"x.mp4"`; el resto, sin cambios. Acepta `None` y devuelve `""`.

- [ ] **Step 1: Write the failing test**

```python
from slackbot.nombres import canonico


def test_quita_el_sufijo_v2():
    assert canonico("film_v2.mp4") == "film.mp4"


def test_no_toca_un_nombre_sin_sufijo():
    assert canonico("film.mp4") == "film.mp4"


def test_solo_quita_el_sufijo_final():
    assert canonico("v2_film.mp4") == "v2_film.mp4"


def test_resiste_none_y_vacio():
    assert canonico(None) == ""
    assert canonico("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_nombres.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implement `canonico` in `slackbot/nombres.py`**

Misma regla que `pendientes_borradores.py`: `os.path.splitext`, y si la base termina en `_v2`, quitarla.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_nombres.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add slackbot/nombres.py tests/slackbot/test_nombres.py
git commit -m "feat(slackbot): normalizacion de nombres con sufijo _v2"
```

---

### Task 4: Construcción del lanzamiento

**Files:**
- Create: `slackbot/acciones.py`
- Test: `tests/slackbot/test_acciones.py`

**Interfaces:**
- Consumes: `validar` de Task 2.
- Produces:
  - `JOB_LOTES = 638404369584915`
  - `TAREAS_CANAL = {"youtube": "publicar_youtube", "facebook": "publicar_facebook"}`
  - `construir_lanzamiento(parametros: dict, job: dict) -> dict` — payload de `run_now` con solo lo mencionado sobrescrito.
  - `lanzar(cliente, parametros: dict) -> dict` — devuelve `{"run_id": int, "run_page_url": str}`.

- [ ] **Step 1: Write the failing test**

```python
from slackbot.acciones import TAREAS_CANAL, construir_lanzamiento

JOB = {
    "settings": {
        "parameters": [
            {"name": "cantidad", "default": "10"},
            {"name": "hashtags", "default": "motivacional,fyp"},
            {"name": "min_likes", "default": "20000"},
            {"name": "top_n", "default": "30"},
            {"name": "max_pendientes", "default": "0"},
        ],
        "tasks": [{"task_key": k} for k in
                  ("guiones_60s", "generar_video", "publicar_youtube", "publicar_facebook")],
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


def test_tiktok_no_es_una_tarea_desactivable():
    # TikTok no tiene tarea propia de publicacion: va dentro de subir_borradores.
    assert "tiktok" not in TAREAS_CANAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_acciones.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implement `construir_lanzamiento` and `lanzar` in `slackbot/acciones.py`**

Los valores del job llegan como texto (`"10"`); se convierten con `str()`. Las tareas se copian de `job["settings"]["tasks"]` filtrando las de `TAREAS_CANAL` cuyo canal no esté en `canales`. `lanzar` usa `cliente.jobs.run_now(job_id=JOB_LOTES, job_parameters=...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_acciones.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add slackbot/acciones.py tests/slackbot/test_acciones.py
git commit -m "feat(slackbot): construccion del lanzamiento con solo lo mencionado"
```

---

### Task 5: Estado de los videos

**Files:**
- Modify: `slackbot/acciones.py` (añadir funciones, no reescribir)
- Test: `tests/slackbot/test_estado_videos.py`

**Interfaces:**
- Consumes: `canonico` de Task 3.
- Produces:
  - `ESTADOS_OK = frozenset({"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"})`
  - `clasificar_tiktok(generados: list[dict], subidas: list[dict]) -> dict` — `{"en_inbox": [...], "pendientes": [{"video_name", "motivo"}]}`
  - `agrupar_por_canal(filas: list[dict]) -> dict[str, list[dict]]`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_estado_videos.py -v`
Expected: FAIL — `ImportError: cannot import name 'clasificar_tiktok'`

- [ ] **Step 3: Implement `clasificar_tiktok` and `agrupar_por_canal` in `slackbot/acciones.py`**

`clasificar_tiktok` indexa las subidas por `canonico(video_name)`, quedándose con el estado de éxito si alguno lo fue. `agrupar_por_canal` agrupa preservando el orden de entrada.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_estado_videos.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add slackbot/acciones.py tests/slackbot/test_estado_videos.py
git commit -m "feat(slackbot): clasificacion de videos subidos y pendientes"
```

---

### Task 6: Mensajes de Slack

**Files:**
- Create: `slackbot/slack.py`
- Test: `tests/slackbot/test_slack.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `tarjeta_confirmacion(accion: str, parametros: dict, predeterminados: dict, valor_boton: str) -> list[dict]`
  - `texto_resultado(run: dict) -> str`
  - `texto_estado_videos(clasificacion: dict, por_canal: dict) -> str`
  - `MAX_LISTA = 15`

- [ ] **Step 1: Write the failing test**

```python
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
    assert "3" in texto
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_slack.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implement the four functions in `slackbot/slack.py`**

Block Kit: un `section` por grupo de parámetros, un `actions` con dos `button`. El de «Cancelar» lleva `action_id="cancelar"` y `value` vacío; el de «Ejecutar», `action_id="ejecutar"` y el `valor_boton`. Los textos de lista usan `"\n".join(...[:MAX_LISTA])` y añaden `… y N más`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_slack.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add slackbot/slack.py tests/slackbot/test_slack.py
git commit -m "feat(slackbot): tarjetas y respuestas de Slack"
```

---

### Task 7: Las dos rutas

**Files:**
- Create: `slackbot/config.py`
- Create: `slackbot/app.py`
- Test: `tests/slackbot/test_app.py`

**Interfaces:**
- Consumes: `firma_valida` (T1), `interpretar` (T2), `construir_lanzamiento`/`lanzar` (T4), `tarjeta_confirmacion`/`texto_*` (T6).
- Produces:
  - `crear_app(secretos: dict, deps: dict) -> Flask` — `deps` inyecta `interpretar`, `lanzar`, `leer_job`, `consultar`, `responder`, para que los tests no toquen red.
  - `USADOS: set[str]` — identificadores de botón ya consumidos.
  - `main()` — arranca Flask leyendo los secretos del scope.

- [ ] **Step 1: Write the failing test**

```python
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
    # Review Focus #3
    llamadas = []
    app = crear_app({"slack_signing_secret": SECRETO},
                    _deps(interpretar=lambda t: {"accion": "lanzar_lote",
                                                 "parametros": {"cantidad": 1}},
                          lanzar=lambda p: llamadas.append(p)))
    r = _post(app.test_client(), "/slack/events",
              {"type": "event_callback", "event": {"text": "lanza", "channel": "C1"}},
              retry=1)
    assert r.status_code == 200
    assert llamadas == []


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
        {"action_id": "ejecutar", "value": "uso-unico-1"}]}
    c = app.test_client()
    _post(c, "/slack/interactions", payload)
    _post(c, "/slack/interactions", payload)
    assert len(lanzados) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/slackbot/test_app.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implement `crear_app` and `main` in `slackbot/app.py`**

Lee `X-Slack-Signature` y `X-Slack-Request-Timestamp` con `request.get_data()`. Si `X-Slack-Retry-Num` está presente, responde 200 sin procesar. El trabajo pesado va en `threading.Thread(daemon=True)`. Un `ValueError` de `validar` se responde como texto explicativo sin botón. `config.py` expone `secretos() -> dict` leyendo el scope.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/slackbot/test_app.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -v`
Expected: todo pasa; ningún test previo se rompe.

- [ ] **Step 6: Commit**

```bash
git add slackbot/config.py slackbot/app.py tests/slackbot/test_app.py
git commit -m "feat(slackbot): rutas de Slack con firma y confirmacion"
```

---

### Task 8: Despliegue y documentación

**Files:**
- Create: `slackbot/app.yaml`
- Create: `slackbot/requirements.txt`
- Modify: `docs/operacion-multicanal.md` (sección «Bot de Slack»)

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: nada para código.

- [ ] **Step 1: Write `slackbot/requirements.txt`**

```
flask
requests
databricks-sdk
```

- [ ] **Step 2: Write `slackbot/app.yaml`**

```yaml
command:
  - "python"
  - "-m"
  - "slackbot.app"
env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: warehouse
resources:
  - name: warehouse
    description: SQL warehouse para estado_videos
    sql_warehouse:
      id: ${WAREHOUSE_ID}
      permission: CAN_USE
```

- [ ] **Step 3: Crear la app y los secretos**

```bash
databricks apps create tiktok-slack-bot --profile DEFAULT
databricks secrets put-secret tiktok_pipeline slack_bot_token --profile DEFAULT
databricks secrets put-secret tiktok_pipeline slack_signing_secret --profile DEFAULT
```

Expected: la app aparece en `databricks apps list`; los dos `put-secret` piden el valor por entrada estándar, sin mostrarlo.

- [ ] **Step 4: Documentar en `docs/operacion-multicanal.md`**

Tabla de las cuatro acciones, el flujo mensaje → tarjeta → botón, y el aviso de que Instagram queda fuera. Enlazar el spec y este plan.

- [ ] **Step 5: Prueba de humo manual**

Con la app desplegada, en un canal de pruebas:

```
lanza 1 video con #motivacion y min_likes 100000
```

Expected: aparece una tarjeta con `cantidad=1`, `hashtags=motivacion`, `min_likes=100000` y el resto «por defecto del job». Sin pulsar Ejecutar no se crea ningún run — verificable con `databricks jobs list-runs --job-id 638404369584915 --profile DEFAULT`.

- [ ] **Step 6: Commit**

```bash
git add slackbot/app.yaml slackbot/requirements.txt docs/operacion-multicanal.md
git commit -m "chore(slackbot): despliegue, secretos y documentacion"
```
