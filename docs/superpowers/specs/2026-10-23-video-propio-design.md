# Diseño: videos propios a partir de los guiones de `gold.guiones_60s`

Fecha: 2026-10-23
Estado: aprobado en conversación, pendiente de revisión escrita

## 1. Objetivo

Producir videos verticales de 60 s con **voz propia del usuario** y **b-roll licenciado**, a partir
de los guiones ya generados en `tiktok.gold.guiones_60s`, y publicarlos en la cuenta del usuario.

Éxito = un video terminado que el usuario acepte publicar, y un proceso repetible para los 30
guiones en cola sin rehacer trabajo manual cada vez.

## 2. Fuera de alcance

- TTS / voz sintética. La voz es del usuario.
- Generación de video por IA (Sora y similares).
- Programación automática de publicaciones.
- Dashboard o interfaz gráfica.
- Publicación pública: la app de TikTok sigue en Sandbox, así que la única privacidad posible
  hoy es `SELF_ONLY`. Producir no está bloqueado; publicar en público sí.

## 3. Restricciones duras

1. **Contenido propio.** El texto del guion es original (el prompt prohíbe copiar). El b-roll debe
   venir de grabaciones del usuario o de stock con licencia. **Nunca** de `video_fuente_url` ni de
   los mp4 de terceros en `tiktok.default.videos`.
2. **Puerta de veracidad.** Solo se producen guiones con `confianza = 'alta'`. Los campos
   `datos_a_verificar` se presentan como checklist y el usuario los confirma antes de publicar.
3. **Privacidad SELF_ONLY** hasta que la app pase revisión de producción.

## 4. Entorno verificado (2026-10-23)

| Pieza | Estado |
|---|---|
| ffmpeg | NO en PATH. Disponible vía `imageio_ffmpeg.get_ffmpeg_exe()` → 7.1 (gyan.dev essentials) |
| Encoders/filtros | `libx264`, `aac`, `ass`, `subtitles` (libass), `drawtext` — todos presentes |
| Python | 3.14.4. `ctranslate2` tiene wheel `cp314`, así que `faster-whisper` 1.2.1 es viable |
| `requests` | No instalado |
| Databricks CLI | v1.22.1, profile `DEFAULT`, scopes `apify` y `tiktok_pipeline` |
| `upload_own_video.py` | **Roto**: `IndentationError` en línea 90 (`...:rr`) |

Consecuencia: crear un `.venv` en el repo con `faster-whisper`, `requests` y `imageio-ffmpeg`.
No se instala ffmpeg a nivel de sistema: se resuelve el binario desde `imageio-ffmpeg` para que el
entorno sea reproducible.

## 5. Arquitectura

Cinco etapas, cada una un script que lee archivos y escribe archivos. Si una toma de voz sale mal,
se re-ejecutan solo las etapas 3-4.

```
gold.guiones_60s
      │  (1) pick_guion.py   filtra confianza=alta, exporta
      ▼
  guion.json
      │  (2) grabación manual del usuario
      ▼
  voz/*.wav
      │  (3) transcribe.py   faster-whisper, word_timestamps
      ▼
  palabras.json + subs.ass
      │  (4) fetch_broll.py  cascada local → Pexels → placeholder
      │      build_video.py  ffmpeg: loudnorm, concat, quema subs
      ▼
  out/<slug>.mp4
      │  (5) subir a /Volumes/tiktok/default/videos/ + upload_own_video.py
      ▼
  SELF_ONLY en TikTok
```

El montaje corre **en local** (Windows), no en Databricks: el clúster serverless no permite
binarios del sistema y un clúster clásico por 20-40 s de render no se justifica. Databricks sigue
siendo el sitio de los guiones y de la publicación.

## 6. Contratos de componentes

### `scripts/pick_guion.py`
- **Entrada:** nada (consulta Databricks) o `--guion-id`.
- **Salida:** `guion.json` con los campos del guion + `palabras_objetivo`.
- **Comportamiento:** filtra `confianza = 'alta'`; si hay varios, elige por `fuente_plays` desc;
  imprime `datos_a_verificar` como checklist y **exige confirmación interactiva** antes de escribir.
- **Fallo:** si no hay guiones de confianza alta, sale con código 2 y no escribe nada.

### `scripts/transcribe.py`
- **Entrada:** `--audio voz/toma1.wav`.
- **Salida:** `palabras.json` (lista `{inicio, fin, texto}`) y `subs.ass`.
- **Comportamiento:** `faster-whisper` modelo `medium`, `language="es"`, `word_timestamps=True`.
  Agrupa palabras en chunks de 3-5 palabras respetando pausas > 0.4 s.
- **Fallo:** si la duración transcrita se desvía >15 % de la del audio, avisa (posible alucinación
  en silencios) y no genera `subs.ass` silenciosamente.

### `scripts/fetch_broll.py`
- **Entrada:** `guion.json`, `palabras.json`.
- **Salida:** `broll/manifest.json` — por escena: ruta, offset de inicio, duración.
- **Comportamiento:** cascada (1) `broll/` local, (2) Pexels API si existe `pexels_api_key`,
  (3) placeholder de color con `drawtext` del `texto_en_pantalla`.
- **Regla:** si no hay clip convincente para una escena, se prefiere **texto a pantalla completa**
  sobre fondo con movimiento antes que stock genérico.

### `scripts/build_video.py`
- **Entrada:** `voz/*.wav`, `palabras.json`, `subs.ass`, `broll/manifest.json`.
- **Salida:** `out/<slug>.mp4` (1080x1920, 30 fps, H.264 CRF 20, AAC 192k).
- **Comportamiento:** `loudnorm` al audio, `afftdn` si hay ruido, padding de 200 ms al inicio,
  concat de b-roll escalado/recortado a 9:16 con `zoompan` suave, quema de subtítulos con `ass=`.
- **Validación:** `ffprobe` al final; falla si no hay pista de audio, si la resolución no es
  1080x1920 o si la duración se aleja >3 s de 60 s.

### Etapa 5 — publicación
- `databricks fs cp` del mp4 al volumen y ejecución de `upload_own_video.py` en modo `SELF_ONLY`.
- **Prerrequisito:** corregir el `IndentationError` de la línea 90 y reimportar el notebook.

## 7. Formato de video

- 1080x1920, 30 fps, H.264 CRF 20, perfil High, `yuv420p` (compatibilidad TikTok).
- Audio AAC 192 kbps, 48 kHz, loudness normalizado a -14 LUFS (estándar de plataforma).
- Subtítulos: ASS, negrita, blanco con borde negro 3 px, tercio inferior, 3-5 palabras por chunk.
- Gancho visual: el primer frame debe tener texto o movimiento; nunca un plano estático 2 s.

## 8. Manejo de errores

- Cada script falla con código distinto de 0 y mensaje en el que se pueda actuar. Sin `except`
  que se trague errores.
- Los pasos intermedios son idempotentes: re-ejecutar sobrescribe sus propias salidas.
- `pick_guion.py` nunca escribe un guion con `confianza` distinta de `alta`.

## 9. Pruebas

- **Unitarias:** agrupación de chunks en `transcribe.py` (pausas, límites de frase); generación de
  ASS; parsing de `manifest.json`; selección y filtrado en `pick_guion.py` con filas sintéticas.
- **De integración:** audio sintético de 60 s (seno + silencios) → `transcribe.py` → `build_video.py`
  → `ffprobe` confirma 1080x1920, pista de audio, duración ~60 s.
- **Manual:** un guion real de punta a punta, revisado por el usuario antes de publicar.

No se testea contra TikTok: la publicación es manual y en `SELF_ONLY`.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Whisper alucina texto en silencios | Chequeo de duración >15 %, modelo `medium`, VAD activado |
| Voz con ruido de fondo | `afftdn` y recomendación de grabar en habitación con textiles |
| b-roll genérico mata retención | Regla de placeholder con texto; revisión manual del primer render |
| Stock sin licencia clara | Solo Pexels/Pixabay y biblioteca propia; registrar la fuente en el manifest |
| App Sandbox bloquea publicación pública | Se produce igual; publicación en `SELF_ONLY` hasta aprobación |
| Dato falso en guion | Puerta `confianza='alta'` + checklist de `datos_a_verificar` |

## 11. Orden de implementación

1. Arreglar `upload_own_video.py` (línea 90) y reimportar.
2. `.venv` + `requirements.txt`.
3. `pick_guion.py` + tests.
4. `transcribe.py` + tests.
5. `build_video.py` con un audio sintético; validación `ffprobe`.
6. `fetch_broll.py` (local primero; Pexels después).
7. **Un video real de punta a punta** y revisión del usuario.
8. Solo entonces, repetir para el resto de la cola.
