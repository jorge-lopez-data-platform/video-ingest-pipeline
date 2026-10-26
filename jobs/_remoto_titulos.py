"""Títulos únicos para cada video, generados en la fase serial.

El título es `texto_pantalla_1` del guion, que produce el LLM de guiones. Eso
tenía tres defectos reales en producción:

1. **Se colaba el placeholder del prompt.** El esquema de `guiones_60s_viral`
   documenta el campo con `["overlay 0-5", "overlay 20-25", "overlay 45-50"]`,
   y el modelo copió el literal: hay títulos como "overlay 0-5: ¿Controlas lo
   incontrolable?" o "0-5: Nadie te dice esto".
2. **Se repetía el hook tal cual.** "SIEMPRE SON LOS MISMOS" con hook "Siempre
   son los mismos." — el titular no aportaba nada sobre el gancho.
3. **Nada impedía repetir.** El guion es estocástico y no había ni historial ni
   comprobación, así que dos videos podían salir con el mismo titular.

Aquí se resuelve en la fase serial, una sola vez por lote, por la misma razón
que los hashtags y las caras: si cada iteración paralela eligiera su título por
su cuenta, no habría forma de garantizar que fueran distintos entre sí.

Unicidad: como en `personaje_llm`, no se deja al azar del modelo. Se pide, se
comprueba contra el historial y contra el lote, y si el LLM insiste en repetir
se cae a un título derivado del propio guion de forma determinista. Publicar dos
videos con el mismo titular es lo que se pidió evitar.
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from datetime import datetime, timezone

MAX_TITULO = 90
VENTANA_HISTORIAL = 200
TABLA_TITULOS = "tiktok.gold.titulos_usados"

# Restos del esquema del prompt que el modelo llega a copiar literalmente:
# "overlay 0-5: ...", "0-5: ...", "0 - 5 ...". Se comen solo al principio.
_PREFIJO_RANGO = re.compile(
    r"^\s*(?:overlay\s*)?\d+(?:\s*[-–—]\s*\d+)?\s*(?:seg(?:undos)?)?\s*:?\s*",
    re.IGNORECASE,
)

# Un título entrecomillado o terminado en punto se ve sucio en el formulario.
_COMILLAS = "\"'«»“”‘’ "


def normalizar(texto) -> str:
    """Deja el título presentable: sin placeholder, sin hashtags, sin comillas."""
    limpio = " ".join(str(texto or "").split())
    if not limpio:
        return ""
    limpio = _PREFIJO_RANGO.sub("", limpio)
    limpio = " ".join(p for p in limpio.split() if not p.startswith("#"))
    limpio = limpio.strip(_COMILLAS)
    limpio = limpio.rstrip(" .,;:")
    if len(limpio) > MAX_TITULO:
        # Cortar a mitad de palabra se lee peor que cortar en el último espacio.
        recorte = limpio[:MAX_TITULO].rsplit(" ", 1)[0].rstrip(" ,.;:")
        limpio = recorte or limpio[:MAX_TITULO]
    return limpio


def clave(titulo) -> str:
    """Huella para comparar títulos: sin acentos, mayúsculas ni puntuación.

    "¿Y si lo pierdes todo?" y "Y si lo pierdes todo" son el mismo título para
    quien lo lee, así que deben contar como repetidos.
    """
    base = unicodedata.normalize("NFKD", str(titulo or ""))
    base = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", base.casefold())


PROMPT_SISTEMA = """Eres titulador de una cuenta de TikTok/YouTube Shorts en español que habla de motivación, superación y reflexión.

Recibes el guion completo de un video en JSON y escribes TITULARES cortos y distintos entre sí.

REGLAS:
- Entre 20 y 70 caracteres. Corto pega más.
- Español neutro, tuteo, sin hashtags, sin emojis, sin comillas.
- Debe enganchar por sí solo y no ser un resumen plano del guion.
- PROHIBIDO copiar el `hook` literal: si el titular dice lo mismo que el gancho, no aporta nada. Reformúlalo.
- PROHIBIDO copiar marcas de tiempo del esquema ("0-5", "overlay", "seg").
- No inventes datos, cifras ni nombres que no estén en el guion.
- Debes devolver opciones de verdad distintas entre sí y respecto a `ya_usados`.

Responde SIEMPRE en JSON válido, sin markdown:
{"titulos": ["titular 1", "titular 2", "titular 3"]}"""


def _parsear(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def desde_guion(guion: dict) -> list[str]:
    """Titulares de respaldo sacados del propio guion, sin llamar al LLM.

    Es el último recurso cuando el modelo no logra uno nuevo. Se ordenan de más
    a menos deseable: primero lo que ya estaba pensado como titular, y al final
    los bloques del cuerpo, que son frases más largas.
    """
    campos = (
        "texto_pantalla_1", "texto_pantalla_2", "texto_pantalla_3",
        "hook", "pattern_interrupt", "cierre", "open_loop", "pre_cierre", "cta",
    )
    vistos, salida = set(), []
    for campo in campos:
        limpio = normalizar(guion.get(campo))
        if not limpio:
            continue
        k = clave(limpio)
        if k and k not in vistos:
            vistos.add(k)
            salida.append(limpio)
    return salida


def elegir(cliente, modelo: str, guion: dict, evitar: list[str] | None = None,
           intentos: int = 3) -> str:
    """Devuelve un titular que no esté en `evitar` (comparado con `clave`).

    `evitar` son los títulos ya usados: el historial de la tabla más los que van
    saliendo en este mismo lote. Si el LLM no consigue uno distinto tras
    `intentos`, se usa un respaldo determinista derivado del guion.
    """
    evitar = [t for t in (evitar or []) if str(t).strip()]
    claves_evitadas = {clave(t) for t in evitar}

    contenido = json.dumps(guion, ensure_ascii=False, default=str)
    usuario = (
        f"GUION (JSON):\n{contenido}\n\n"
        f"TÍTULOS YA USADOS (no repitas ninguno, ni con otras mayúsculas):\n"
        f"{json.dumps(evitar[-40:], ensure_ascii=False) if evitar else '(ninguno)'}"
    )

    for intento in range(intentos):
        resp = cliente.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": usuario},
            ],
            # Alta a propósito: se busca variedad. La unicidad se comprueba
            # abajo; la temperatura sola no la garantiza.
            temperature=0.9 + 0.03 * intento,
            response_format={"type": "json_object"},
        )
        try:
            datos = _parsear(resp.choices[0].message.content)
        except (json.JSONDecodeError, ValueError):
            print(f"  titular: respuesta no parseable en el intento {intento + 1}")
            continue

        for bruto in datos.get("titulos") or []:
            limpio = normalizar(bruto)
            if not limpio:
                continue
            k = clave(limpio)
            if not k:
                continue
            if k in claves_evitadas:
                print(f"  titular repetido en el intento {intento + 1}: {limpio!r}")
                continue
            return limpio

        usuario += ("\n\nATENCIÓN: los titulares que devolviste ya están usados o "
                    "siguen el esquema del guion. Escribe titulares NUEVOS, "
                    "distintos entre sí y que no repitan el hook literal.")

    # Respaldo determinista: no depende de la suerte del muestreo.
    for candidato in desde_guion(guion):
        if clave(candidato) not in claves_evitadas:
            print("  titular: el LLM no logró uno nuevo; se usa el del guion")
            return candidato

    # Todo el guion coincide con títulos ya usados. Se desambigua con una marca
    # corta para no publicar jamás dos videos con el mismo titular.
    print("  AVISO: todo el guion ya estaba usado como titular; se desambigua")
    rnd = random.Random(clave(contenido))
    for candidato in desde_guion(guion):
        base = candidato[:MAX_TITULO - 4].rstrip(" ,.;:")
        for _ in range(50):
            variante = normalizar(f"{base} ({rnd.randrange(10, 99)})")
            if clave(variante) not in claves_evitadas:
                return variante
    return f"Video {rnd.randrange(10000, 99999)}"


def cargar_historial(spark, tabla: str = TABLA_TITULOS,
                     ventana: int = VENTANA_HISTORIAL) -> list[str]:
    """Títulos recientes, del más nuevo al más viejo.

    Si la tabla no existe todavía (primera corrida) se devuelve vacío en vez de
    fallar: el historial mejora la variedad, no es requisito para publicar.
    """
    try:
        filas = spark.sql(
            f"SELECT titulo FROM {tabla} ORDER BY generado_en DESC LIMIT {ventana}"
        ).collect()
        return [normalizar(f["titulo"]) for f in filas if str(f["titulo"] or "").strip()]
    except Exception as exc:
        print(f"Historial de títulos no legible ({type(exc).__name__}); "
              "se garantiza solo la unicidad dentro del lote.")
        return []


def registrar(spark, video_fuente_id: str, titulo: str,
              tabla: str = TABLA_TITULOS) -> None:
    """Apunta el título usado. Se llama cuando el mp4 ya existe, no antes."""
    from pyspark.sql import Row
    from pyspark.sql.types import StringType, StructField, StructType, TimestampType

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {tabla} (
              video_fuente_id STRING, titulo STRING, generado_en TIMESTAMP
            ) USING DELTA"""
    )
    esquema = StructType([
        StructField("video_fuente_id", StringType()),
        StructField("titulo", StringType()),
        StructField("generado_en", TimestampType()),
    ])
    spark.createDataFrame(
        [Row(video_fuente_id=str(video_fuente_id), titulo=titulo,
             generado_en=datetime.now(timezone.utc))],
        esquema).write.mode("append").saveAsTable(tabla)
