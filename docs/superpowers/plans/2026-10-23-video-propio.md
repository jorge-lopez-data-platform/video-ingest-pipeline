# Producción de video propio — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Producir un mp4 vertical de 60 s con voz propia y b-roll licenciado a partir de un guion de `tiktok.gold.guiones_60s`, listo para subir en `SELF_ONLY`.

**Architecture:** Cinco etapas como scripts CLI independientes que se comunican por archivos JSON en disco. El montaje corre en local con ffmpeg; Databricks solo aporta los guiones y la publicación.

**Tech Stack:** Python 3.14, `faster-whisper` 1.2.1, ffmpeg 7.1 vía `imageio-ffmpeg`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-video-propio-design.md`

## Global Constraints

- Video: 1080x1920, 30 fps, H.264 CRF 20, `yuv420p`, audio AAC 192 kbps 48 kHz.
- Solo guiones con `confianza = 'alta'`. Nunca otro valor.
- El b-roll nunca sale de `video_fuente_url` ni de `/Volumes/tiktok/default/videos/`.
- Cuenta destino: `lomasviral`. Privacidad: `SELF_ONLY`.
- **No existe `ffprobe` en esta máquina.** Toda inspección de video se hace ejecutando `ffmpeg -i <archivo>` y parseando su salida de diagnóstico (`Duration:`, `Stream #`). No importar `ffprobe` ni asumir que está en PATH.
- La ruta a ffmpeg se resuelve siempre con `imageio_ffmpeg.get_ffmpeg_exe()`. No invocar `ffmpeg` a secas: no está en PATH.
- Cada script es idempotente sobre sus propias salidas y termina con código distinto de 0 ante error.
- Tabla real hoy: 3 filas, de las cuales **1 es `alta`** (`palabras_reales=178`, `duracion_estimada=65.9`, `datos_a_verificar` vacío). Las pruebas no deben asumir más filas.

## Review Focus

Estas son las condiciones que la spec implica pero ningún test de tarea cubriría por defecto. Cada
una tiene su test en la tarea que posee el código.

1. **Audio más corto que el guion** (el usuario lee rápido): los subtítulos deben terminar antes que el video, no estirarse ni generar chunks huérfanos sin tiempo.
2. **Silencio al inicio o al final** (el usuario tarda en hablar): Whisper puede alucinar palabras ahí; el chequeo de duración debe avisar en vez de aceptar texto inexistente.
3. **Una sola palabra suelta entre dos pausas largas**: el agrupador debe emitirla, no descartarla ni fusionarla con un chunk lejano.
4. **Manifest de b-roll con menos escenas que chunks**: el video se construye igual, reutilizando el último clip; no debe fallar ni dejar hueco negro.
5. **`guiones_60s` vacía o sin filas `alta`**: `pick_guion.py` sale con código 2 sin escribir `guion.json`, nunca con un guion degradado.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `requirements.txt` | Dependencias fijadas |
| `scripts/__init__.py` | Marca el paquete para que pytest importe desde la raíz |
| `scripts/common.py` | Ruta de ffmpeg, ejecución de ffmpeg, sondeo por `ffmpeg -i` |
| `scripts/pick_guion.py` | Consulta Databricks, filtra `alta`, escribe `guion.json` |
| `scripts/transcribe.py` | Whisper → `palabras.json` + `subs.ass` |
| `scripts/fetch_broll.py` | Cascada local → Pexels → placeholder → `broll/manifest.json` |
| `scripts/build_video.py` | Ensambla con ffmpeg → `out/<slug>.mp4` |
| `tests/conftest.py` | Fixtures: audio sintético, filas de guion |
| `tests/test_common.py`, `test_pick_guion.py`, `test_transcribe.py`, `test_fetch_broll.py`, `test_build_video.py` | Pruebas por unidad |

---

### Task 1: Entorno, helper de ffmpeg y arreglo de la publicación

**Files:**
- Create: `requirements.txt`, `scripts/__init__.py`, `scripts/common.py`, `tests/conftest.py`, `tests/test_common.py`
- Modify: `upload_own_video.py:90`

**Interfaces:**
- Produces: `common.FFMPEG: str`, `common.run_ffmpeg(args: list[str], check: bool = True) -> subprocess.CompletedProcess`, `common.probe(path: Path) -> dict` con claves `duracion: float | None`, `ancho: int | None`, `alto: int | None`, `tiene_audio: bool`.

- [ ] **Step 1: Crear entorno e instalar**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -U pip
```

`requirements.txt` con: `faster-whisper==1.2.1`, `requests`, `imageio-ffmpeg`, `pytest`.

- [ ] **Step 2: Escribir el test que falla**

```python
def test_probe_lee_resolucion_y_audio(tmp_path):
    video = tmp_path / "v.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                       "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2",
                       "-shortest", "-c:v", "libx264", "-c:a", "aac", "-y", str(video)])
    info = common.probe(video)
    assert info["ancho"] == 1080
    assert info["alto"] == 1920
    assert info["tiene_audio"] is True
    assert 1.5 <= info["duracion"] <= 2.5

def test_probe_sin_audio(tmp_path):
    video = tmp_path / "mudo.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=1080x1920:d=1",
                       "-c:v", "libx264", "-y", str(video)])
    assert common.probe(video)["tiene_audio"] is False
```

- [ ] **Step 3: Ejecutar y ver que falla**

Run: `.venv/Scripts/python -m pytest tests/test_common.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.common'`

- [ ] **Step 4: Implementar `scripts/common.py`**

`probe` ejecuta `ffmpeg -i <path>` capturando stderr, y parsea con dos regex: `Duration: (\d+):(\d+):([\d.]+)` y `Stream #\d+:\d+.*: (Video|Audio)`. Si hay stream Video, extrae `(\d+)x(\d+)` de la misma línea. Devuelve `None`/`False` cuando no encuentra el dato — nunca lanza por ausencia, solo por ejecución fallida.

- [ ] **Step 5: Ejecutar y ver que pasa**

Run: `.venv/Scripts/python -m pytest tests/test_common.py -v`
Expected: 2 passed

- [ ] **Step 6: Arreglar `upload_own_video.py:90`**

La línea `if status != "PUBLISH_COMPLETE":rr` es un `IndentationError`. Debe quedar `if status != "PUBLISH_COMPLETE":` y el `raise` de la línea siguiente con su indentación normal.

Verificar: `python -c "import ast; ast.parse(open('upload_own_video.py', encoding='utf-8').read())"`
Expected: sin salida, código 0.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt scripts/ tests/ upload_own_video.py
git commit -m "feat: entorno, helper ffmpeg y arreglo de upload_own_video"
```

---

### Task 2: `pick_guion.py` — selección con puerta de confianza

**Files:**
- Create: `scripts/pick_guion.py`, `tests/test_pick_guion.py`

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces: `seleccionar_guion(filas: list[dict]) -> dict` que lanza `LookupError` si no hay ninguna fila con `confianza == "alta"`; `construir_guion_json(fila: dict) -> dict` con claves `slug, hook, open_loop, bloque_1, pattern_interrupt, bloque_2, pre_cierre, cierre, cta, hashtags (list[str]), texto_en_pantalla (list[str]), datos_a_verificar (list[str]), palabras_objetivo (int)`.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_descarta_confianza_media():
    filas = [{"confianza": "media", "hook": "a", "fuente_plays": 9},
             {"confianza": "alta", "hook": "b", "fuente_plays": 5}]
    assert pick_guion.seleccionar_guion(filas)["hook"] == "b"

def test_sin_guiones_altos_lanza_lookup():
    with pytest.raises(LookupError):
        pick_guion.seleccionar_guion([{"confianza": "media", "hook": "a"}])

def test_empata_por_plays_desc():
    filas = [{"confianza": "alta", "hook": "bajo", "fuente_plays": 1},
             {"confianza": "alta", "hook": "alto", "fuente_plays": 9}]
    assert pick_guion.seleccionar_guion(filas)["hook"] == "alto"

def test_construir_json_parte_campos_delimitados():
    fila = {"hook": "H", "hashtags": "a,b,c", "datos_a_verificar": "x | y",
            "texto_pantalla_1": "uno", "texto_pantalla_2": "", "texto_pantalla_3": "tres",
            "palabras_reales": 178}
    g = pick_guion.construir_guion_json(fila)
    assert g["hashtags"] == ["a", "b", "c"]
    assert g["datos_a_verificar"] == ["x", "y"]
    assert g["texto_en_pantalla"] == ["uno", "tres"]
    assert g["palabras_objetivo"] == 178
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `.venv/Scripts/python -m pytest tests/test_pick_guion.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implementar `scripts/pick_guion.py`**

`seleccionar_guion` filtra `confianza == "alta"` (comparación sobre `str(...).strip().lower()`), ordena por `fuente_plays` descendente con `None` tratado como 0, y devuelve la primera. Lanza `LookupError` con mensaje accionable si la lista queda vacía.

`construir_guion_json` separa por `,` los hashtags y por `|` los datos, descarta vacíos, y deriva `slug` de `video_fuente_id` o, si falta, de las primeras palabras del hook en kebab-case.

CLI: `python scripts/pick_guion.py --out guion.json [--no-databricks]`. Por defecto consulta con `databricks experimental aitools tools query --profile DEFAULT --output json`, imprime cada `datos_a_verificar` como checklist y pide confirmación por `input()` antes de escribir. Con `LookupError` imprime el motivo y sale con código 2.

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `.venv/Scripts/python -m pytest tests/test_pick_guion.py -v`
Expected: 4 passed

- [ ] **Step 5: Verificación manual contra la tabla real**

Run: `.venv/Scripts/python scripts/pick_guion.py --no-confirm --out /tmp/guion.json`
Expected: escribe el guion `motivacional` (`datos_a_verificar` vacío) y sale con código 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/pick_guion.py tests/test_pick_guion.py
git commit -m "feat: seleccion de guion con puerta de confianza alta"
```

---

### Task 3: `transcribe.py` — Whisper, chunks y subtítulos ASS

**Files:**
- Create: `scripts/transcribe.py`, `tests/test_transcribe.py`
- Modify: `tests/conftest.py` (fixture de audio sintético)

**Interfaces:**
- Consumes: `common.FFMPEG`, `common.probe`.
- Produces: `agrupar_chunks(palabras: list[dict], max_palabras: int = 5, min_palabras: int = 3, pausa_corte: float = 0.4) -> list[dict]` donde cada chunk es `{"inicio": float, "fin": float, "texto": str}`; `generar_ass(chunks: list[dict], ancho: int = 1080, alto: int = 1920) -> str`; `verificar_duracion(duracion_audio: float, duracion_texto: float) -> str | None` que devuelve un aviso o `None`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_agrupa_respetando_pausa():
    palabras = [{"inicio": 0.0, "fin": 0.3, "texto": "uno"},
                {"inicio": 0.3, "fin": 0.6, "texto": "dos"},
                {"inicio": 2.0, "fin": 2.3, "texto": "tres"}]
    chunks = transcribe.agrupar_chunks(palabras)
    assert len(chunks) == 2
    assert chunks[0]["texto"] == "uno dos"

def test_palabra_unica_entre_pausas_se_emite():
    palabras = [{"inicio": 0.0, "fin": 0.2, "texto": "solo"},
                {"inicio": 5.0, "fin": 5.2, "texto": "otra"}]
    chunks = transcribe.agrupar_chunks(palabras)
    assert [c["texto"] for c in chunks] == ["solo", "otra"]

def test_chunks_respetan_maximo():
    palabras = [{"inicio": i * 0.3, "fin": i * 0.3 + 0.3, "texto": f"p{i}"} for i in range(12)]
    chunks = transcribe.agrupar_chunks(palabras)
    assert all(len(c["texto"].split()) <= 5 for c in chunks)

def test_verificar_duracion_avisa_por_desvio():
    assert transcribe.verificar_duracion(60.0, 61.0) is None
    assert transcribe.verificar_duracion(60.0, 30.0) is not None

def test_generar_ass_contiene_dialogos():
    ass = transcribe.generar_ass([{"inicio": 0.0, "fin": 1.5, "texto": "hola"}])
    assert "[Events]" in ass and "Dialogue:" in ass and "hola" in ass
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `.venv/Scripts/python -m pytest tests/test_transcribe.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implementar `agrupar_chunks` y `generar_ass`**

`agrupar_chunks` acumula palabras mientras `len(chunk) < max_palabras` y la pausa entre la palabra anterior y la siguiente sea `<= pausa_corte`; al cortar, emite el chunk aunque tenga menos de `min_palabras` (nunca descarta palabras). El chunk hereda `inicio` de su primera palabra y `fin` de la última.

`generar_ass` emite cabecera `[Script Info]` con `PlayResX: 1080`, `PlayResY: 1920`, un estilo `TikTok` (`Bold=1`, `Fontsize=72`, `Outline=3`, `Shadow=0`, `Alignment=2`, `MarginV=320`, blanco con borde negro) y un `Dialogue:` por chunk con tiempos `H:MM:SS.cc`.

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `.venv/Scripts/python -m pytest tests/test_transcribe.py -v`
Expected: 5 passed

- [ ] **Step 5: Implementar la transcripción y la CLI**

`transcribir(audio: Path, modelo: str = "medium")` usa `faster_whisper.WhisperModel(modelo, device="cpu", compute_type="int8")` con `language="es"`, `word_timestamps=True`, `vad_filter=True`, y devuelve `list[dict]` con el mismo formato que usan los tests. La CLI `python scripts/transcribe.py --audio voz/toma1.wav --out-dir .` escribe `palabras.json` y `subs.ass`, y **no** escribe `subs.ass` si `verificar_duracion` devuelve aviso: imprime el aviso y sale con código 3.

- [ ] **Step 6: Verificación con audio sintético**

Fixture `audio_60s` en `conftest.py`: 60 s de seno con silencios en 0-1 s y 58-60 s, generado con `common.run_ffmpeg`.

Run: `.venv/Scripts/python scripts/transcribe.py --audio tests/fixtures/audio_60s.wav --out-dir /tmp/tr`
Expected: aviso de duración (el seno no tiene voz) y código 3 — confirma que el guardia funciona.

- [ ] **Step 7: Commit**

```bash
git add scripts/transcribe.py tests/test_transcribe.py tests/conftest.py
git commit -m "feat: transcripcion con whisper, chunks y subtitulos ASS"
```

---

### Task 4: `build_video.py` — montaje y validación

**Files:**
- Create: `scripts/build_video.py`, `tests/test_build_video.py`

**Interfaces:**
- Consumes: `common.run_ffmpeg`, `common.probe`, `transcribe.generar_ass`.
- Produces: `construir_video(audio: Path, subs: Path, escenas: list[dict], salida: Path) -> Path`; `validar_salida(path: Path, duracion_esperada: float | None) -> None` que lanza `ValueError` con el motivo; `emparejar_escenas(chunks: list[str], escenas: list[dict]) -> list[dict]`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_validar_salida_ok(tmp_path):
    v = _video_sintetico(tmp_path, segundos=2)
    build_video.validar_salida(v, duracion_esperada=None)

def test_validar_rechaza_sin_audio(tmp_path):
    v = _video_sintetico(tmp_path, segundos=2, con_audio=False)
    with pytest.raises(ValueError):
        build_video.validar_salida(v, duracion_esperada=None)

def test_validar_rechaza_resolucion_distinta(tmp_path):
    v = _video_sintetico(tmp_path, segundos=2, tam="720x1280")
    with pytest.raises(ValueError):
        build_video.validar_salida(v, duracion_esperada=None)

def test_menos_escenas_que_bloques_reutiliza_ultima(tmp_path):
    assert len(build_video.emparejar_escenas(["a", "b", "c", "d"], [{"ruta": "x"}])) == 4
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `.venv/Scripts/python -m pytest tests/test_build_video.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implementar `emparejar_escenas` y `validar_salida`**

`emparejar_escenas(chunks: list[str], escenas: list[dict]) -> list[dict]` devuelve un dict por chunk: el de la escena correspondiente por índice y, si `escenas` se agota, repite la última. Si `escenas` está vacía, lanza `ValueError`.

`validar_salida(path, duracion_esperada)` usa `common.probe` y lanza `ValueError` si falta audio, si `(ancho, alto) != (1080, 1920)`, o si `duracion_esperada` no es `None` y la diferencia supera 3 s.

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `.venv/Scripts/python -m pytest tests/test_build_video.py -v`
Expected: 4 passed

- [ ] **Step 5: Implementar `construir_video`**

Un solo comando ffmpeg con filtro complejo: normaliza el audio con `loudnorm=I=-14:TP=-1.5:LRA=11`, escala y recorta cada clip a 1080x1920 con `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920`, encadena con `concat=n=N:v=1:a=0`, aplica `ass=<subs>`, y codifica con `libx264 -crf 20 -pix_fmt yuv420p -r 30`. Salida en `out/<slug>.mp4`.

Si el audio es más corto que la suma de escenas, `-shortest` recorta el video; si es más largo, el último clip se congela hasta el final del audio. La CLI valida con `validar_salida` y sale con código 4 si falla.

- [ ] **Step 6: Verificación de integración**

Run: `.venv/Scripts/python scripts/build_video.py --audio tests/fixtures/audio_60s.wav --subs /tmp/tr/subs.ass --escenas tests/fixtures/escenas.json --out out/prueba.mp4`
Expected: `out/prueba.mp4` existe; `common.probe` reporta 1080x1920, audio, ~60 s.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_video.py tests/test_build_video.py
git commit -m "feat: montaje de video con validacion de salida"
```

---

### Task 5: `fetch_broll.py` — cascada de b-roll

**Files:**
- Create: `scripts/fetch_broll.py`, `tests/test_fetch_broll.py`

**Interfaces:**
- Consumes: `common.probe`.
- Produces: `resolver_escenas(guion: dict, broll_dir: Path, pexels_key: str | None, por_escena: int = 4, duracion_total: float = 60) -> list[dict]` con `{"ruta": str | None, "texto_overlay": str | None, "fuente": "local" | "pexels" | "placeholder"}`; escribe `broll/manifest.json`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_usa_local_antes_que_pexels(tmp_path, monkeypatch):
    (tmp_path / "a.mp4").write_bytes(b"x")
    monkeypatch.setattr(fetch_broll, "_pexels", lambda *_: pytest.fail("no debe llamarse"))
    escenas = fetch_broll.resolver_escenas({"texto_en_pantalla": ["uno"]}, tmp_path, "key")
    assert escenas[0]["fuente"] == "local"

def test_sin_nada_cae_en_placeholder(tmp_path):
    escenas = fetch_broll.resolver_escenas({"texto_en_pantalla": ["uno", "dos"]}, tmp_path, None)
    assert all(e["fuente"] == "placeholder" for e in escenas)
    assert escenas[0]["texto_overlay"] == "uno"

def test_numero_de_escenas_cubre_el_video(tmp_path):
    escenas = fetch_broll.resolver_escenas({"texto_en_pantalla": []}, tmp_path, None, duracion_total=60, por_escena=4)
    assert len(escenas) == 15
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `.venv/Scripts/python -m pytest tests/test_fetch_broll.py -v`
Expected: FAIL — módulo inexistente

- [ ] **Step 3: Implementar `resolver_escenas`**

Calcula `n_escenas = ceil(duracion_total / por_escena)` (60 s / 4 s = 15). Para cada índice: busca clips en `broll_dir` (`.mp4`, `.mov`) ciclando con `i % len(clips)`; si no hay, consulta `_pexels(termino, pexels_key)` con el texto de overlay como término; si tampoco, emite placeholder con `texto_overlay` tomado de `texto_en_pantalla` (o `None` si se agotó).

`_pexels` solo se llama si `pexels_key` no es `None`. Valida cada clip con `common.probe` y descarta los que no sean verticales.

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `.venv/Scripts/python -m pytest tests/test_fetch_broll.py -v`
Expected: 3 passed

- [ ] **Step 5: Añadir el secreto y documentar**

Si el usuario tiene key de Pexels: `databricks secrets put-secret tiktok_pipeline pexels_api_key --profile DEFAULT`. La CLI lee `PEXELS_API_KEY` del entorno o de `dbutils`, y sin key funciona en modo local+placeholder.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_broll.py tests/test_fetch_broll.py
git commit -m "feat: cascada de b-roll local, pexels y placeholder"
```

---

### Task 6: Primer video real de punta a punta

**Files:**
- Create: `docs/CONTENT_WORKFLOW.md`
- Modify: `README.md` (enlace a la guía)

**Interfaces:** ninguna nueva; encadena las CLI de las tareas 2-5.

- [ ] **Step 1: Seleccionar el guion**

Run: `.venv/Scripts/python scripts/pick_guion.py --out guion.json`
Expected: guion motivacional, confirmación interactiva, `guion.json` escrito.

- [ ] **Step 2: Grabar la voz**

El usuario graba 60-70 s leyendo el guion en una habitación con textiles, móvil a 20 cm,
`voz/toma1.wav` (m4a convertido con `run_ffmpeg`). Sin música ni auriculares.

- [ ] **Step 3: Transcribir y montar**

```bash
.venv/Scripts/python scripts/transcribe.py --audio voz/toma1.wav --out-dir .
.venv/Scripts/python scripts/fetch_broll.py --guion guion.json --broll-dir broll
.venv/Scripts/python scripts/build_video.py --audio voz/toma1.wav --subs subs.ass --escenas broll/manifest.json --out out/motivacional.mp4
```

- [ ] **Step 4: Revisión humana (bloqueante)**

El usuario ve `out/motivacional.mp4` completo y confirma tres cosas: los subtítulos coinciden con
su voz, ninguna escena avergüenza, y cada dato de `datos_a_verificar` está verificado (hoy: lista
vacía, así que basta con confirmar que no afirma hechos concretos).

- [ ] **Step 5: Documentar el flujo**

`docs/CONTENT_WORKFLOW.md`: los comandos exactos, la checklist de revisión y la regla de que el
b-roll nunca sale de `video_fuente_url`.

- [ ] **Step 6: Commit**

```bash
git add docs/CONTENT_WORKFLOW.md README.md
git commit -m "docs: flujo de produccion de video y primer render revisado"
```

---

### Task 7: Publicación en `SELF_ONLY`

**Files:**
- Modify: `docs/CONTENT_WORKFLOW.md` (sección de publicación)

**Interfaces:**
- Consumes: el mp4 de la tarea 6 y `upload_own_video.py` ya arreglado.

- [ ] **Step 1: Verificar que el notebook arreglado está en el workspace**

Run: `MSYS_NO_PATHCONV=1 databricks workspace import "/Users/lopezechagarayjorge@gmail.com/tiktok_ingest/upload_own_video" --file "C:/Users/lopee/tiktok-pipeline/upload_own_video.py" --format SOURCE --language PYTHON --overwrite --profile DEFAULT`
Expected: sin salida; luego `databricks workspace export` y `diff` contra el local da idénticos.

- [ ] **Step 2: Subir el mp4 al volumen**

```bash
MSYS_NO_PATHCONV=1 databricks fs cp "C:/Users/lopee/tiktok-pipeline/out/motivacional.mp4" "dbfs:/Volumes/tiktok/default/videos/own/motivacional.mp4" --profile DEFAULT
```

- [ ] **Step 3: Publicar**

Ejecutar el notebook con `video_path=/Volumes/tiktok/default/videos/own/motivacional.mp4`,
`caption` del guion, `privacy_level=SELF_ONLY`.

Expected: `PUBLISH_COMPLETE`. Si la app devuelve `scope_not_authorized`, el token no tiene
`video.publish` en Sandbox: se detiene aquí y se documenta, sin intentar otra ruta.

- [ ] **Step 4: Commit**

```bash
git add docs/CONTENT_WORKFLOW.md
git commit -m "docs: publicacion SELF_ONLY y limite de la app sandbox"
```

---

## Notas de ejecución

- Las tareas 1-5 no necesitan la voz del usuario: se implementan y prueban con audio sintético.
- La tarea 6 requiere una grabación real; si no está disponible, ejecutar hasta la tarea 5 y parar.
- La tarea 7 puede fallar por estado de la app de TikTok, no por el código. Ese fallo no invalida
  las tareas anteriores.
