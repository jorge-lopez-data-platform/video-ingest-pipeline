# Databricks notebook source
# MAGIC %md
# MAGIC # Registrar la publicación en gold: una fila por intento y canal
# MAGIC
# MAGIC Corre al final del DAG, justo antes de `cerrar_distribucion`, con
# MAGIC `run_if: ALL_DONE`. Escribe `tiktok.gold.publicacion_log`: una fila por
# MAGIC `(video_name, canal, run_id, clave_intento)` con el estado unificado de
# MAGIC cada red.
# MAGIC
# MAGIC `clave_intento` separa los intentos cuando el origen los conserva: en
# MAGIC `tiktok_uploads` es el `uploaded_at` de ese intento, asi que dos reintentos
# MAGIC del mismo video dan dos filas. En `distribucion_video` es la cadena vacia,
# MAGIC porque esa tabla ya sobrescribe y solo guarda el ultimo estado conocido; un
# MAGIC `NULL` no serviria, porque nunca compara igual consigo mismo en el `ON` del
# MAGIC `MERGE` y cada reejecucion duplicaria todas sus filas.
# MAGIC
# MAGIC Tres lecturas y un `MERGE` idempotente:
# MAGIC
# MAGIC 1. `bronze.distribucion_video`: YouTube y Facebook, tal cual.
# MAGIC 2. `bronze.tiktok_uploads`: TikTok (esa tabla nunca está en la anterior).
# MAGIC 3. `gold.guiones_usados`: nombres canónicos de lo generado.
# MAGIC
# MAGIC Un fallo aquí **no tumba el DAG**: la publicación ya ocurrió y solo se pierde
# MAGIC la fila de registro. Reejecutar el cierre reconstruye sin duplicar.
# MAGIC
# MAGIC Las funciones puras van arriba del todo, antes de cualquier uso de
# MAGIC `dbutils`/`spark`, para que los tests las carguen sin Databricks. Se detiene
# MAGIC la carga en el marcador `FIN FUNCIONES PURAS`.

# COMMAND ----------

import os
import re

# --- Reglas puras (sin dbutils ni spark) ------------------------------------

SUFIJO_VERSION = "_v2"

# Misma regla que `slackbot/nombres.py::canonico` (y que `pendientes_borradores`):
# se quita "_v2" del final de la BASE y se conserva la extension. Este patron es
# su equivalente en Spark (regexp_replace, dos pasos); el test comprueba que
# ambos coinciden. A: hay extension (ultimo punto) y la base acaba en _v2.
# B: no hay ningun punto y el nombre acaba en _v2.
CANON_REGEX_A = (r"^(.*)_v2(\.[^.]*)$", "$1$2")
CANON_REGEX_B = (r"^([^.]*)_v2$", "$1")


def canonico(nombre):
    """'x_v2.mp4' -> 'x.mp4'. Sin sufijo se devuelve igual; None -> ''."""
    if not nombre:
        return ""
    base, ext = os.path.splitext(nombre)
    if base.endswith(SUFIJO_VERSION):
        return base[: -len(SUFIJO_VERSION)] + ext
    return nombre


# estado de origen -> estado unificado (vocabulario del spec).
ESTADOS = {
    "PUBLICADO": "PUBLICADO",
    "PUBLISH_COMPLETE": "PUBLICADO",
    "PUBLICADO_PRIVADO": "PUBLICADO_PRIVADO",
    "SEND_TO_USER_INBOX": "EN_BORRADOR",
    "SKIPPED_CUOTA": "SALTADO",
    "SKIPPED_INBOX_FULL": "SALTADO",
    "FALLIDO": "FALLIDO",
}
ESTADO_DESCONOCIDO = "DESCONOCIDO"

# Formato de la clave de intento, en las dos sintaxis. `construir()` usa el de
# Spark; el de Python existe para que un test pueda comprobar que la traduccion
# da lo mismo. Si alguien cambia uno solo, el test falla.
FORMATO_INTENTO = "%Y-%m-%d %H:%M:%S.%f"
FORMATO_INTENTO_SPARK = "yyyy-MM-dd HH:mm:ss.SSSSSS"

# Clave de identidad de una fila. Se escribe UNA vez aqui y de ella salen tanto
# el `partitionBy` de la deduplicacion como el `ON` del MERGE, para que no
# puedan divergir.
CLAVE = ("video_name", "canal", "run_id", "clave_intento")

# Equivalencias de strftime a SimpleDateFormat, solo las que usa este formato.
_SPARK = (("%Y", "yyyy"), ("%m", "MM"), ("%d", "dd"), ("%H", "HH"),
          ("%M", "mm"), ("%S", "ss"), ("%f", "SSSSSS"))


def formato_spark(fmt):
    """Traduce un formato strftime al equivalente de Spark."""
    for py, sp in _SPARK:
        fmt = fmt.replace(py, sp)
    return fmt


def clave_intento(marca):
    """Identifica un intento por su marca de tiempo. None -> ''.

    En `tiktok_uploads` la clave del spec `(video, canal, run_id)` no basta:
    todos los reintentos llevan `run_id = 'unknown'` y colapsarian en una fila.
    """
    if marca is None:
        return ""
    if isinstance(marca, str):
        return marca
    return marca.strftime(FORMATO_INTENTO)


def estado_unificado(estado_origen, motivo_fallo=None):
    """Traduce el literal de la fuente al vocabulario unico.

    Un literal que no esta en el vocabulario (p. ej. 'start_400') es FALLIDO si
    trae motivo de fallo; si no, DESCONOCIDO, para no inventar un exito. El
    literal original siempre se conserva aparte en `estado_origen`.
    """
    if estado_origen in ESTADOS:
        return ESTADOS[estado_origen]
    if motivo_fallo:
        return "FALLIDO"
    return ESTADO_DESCONOCIDO


# --- FIN FUNCIONES PURAS ----------------------------------------------------

# COMMAND ----------

from pyspark.sql import functions as F, Window  # noqa: E402

DISTRIBUCION = "tiktok.bronze.distribucion_video"
SUBIDOS = "tiktok.bronze.tiktok_uploads"
GENERADOS = "tiktok.gold.guiones_usados"
LOG = "tiktok.gold.publicacion_log"

dbutils.widgets.text("run_id", "", "Run id a registrar (vacio = todo el historico)")
dbutils.widgets.text("fecha", "", "Fecha AAAA-MM-DD (vacio = todas)")

# COMMAND ----------

# MAGIC %md ## 1. Parametros
# MAGIC
# MAGIC `cerrar_distribucion` no usa widgets: toma `currentRunId` del contexto y,
# MAGIC si no es numerico, cae a una ventana de 6 horas. Aqui se acepta `run_id`
# MAGIC por widget y, si no llega, se lee del contexto solo para mostrarlo. **No se
# MAGIC filtra por el run del contexto**: es el run de ESTA tarea, y las filas de
# MAGIC origen llevan otro valor (hoy todas `unknown`). Sin filtro explicito, se
# MAGIC reconstruye todo el historico; el `MERGE` lo hace idempotente y es lo que
# MAGIC da el relleno retroactivo de TikTok que pide el spec.

# COMMAND ----------

run_id_param = (dbutils.widgets.get("run_id") or "").strip()
fecha_param = (dbutils.widgets.get("fecha") or "").strip()

run_ctx = ""
try:
    run_ctx = (dbutils.notebook.entry_point.getDbutils()
               .notebook().getContext().currentRunId().toString())
except Exception as exc:
    print(f"currentRunId no disponible ({type(exc).__name__}).")

if fecha_param and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha_param):
    print(f"fecha invalida '{fecha_param}'; se ignora.")
    fecha_param = ""
if run_id_param and not re.fullmatch(r"[\w.\-]+", run_id_param):
    print(f"run_id invalido '{run_id_param}'; se ignora.")
    run_id_param = ""

print(f"run_id filtro={run_id_param or '(todos)'} fecha filtro={fecha_param or '(todas)'} "
      f"run de esta tarea={run_ctx or '?'}")

# COMMAND ----------

# MAGIC %md ## 2. Construir las filas y escribir (envuelto: nunca tumba el DAG)

# COMMAND ----------


def nombre_canonico(col):
    """Equivalente Spark de `canonico` (ver CANON_REGEX_A/B)."""
    paso = F.regexp_replace(col, *CANON_REGEX_A)
    return F.regexp_replace(paso, *CANON_REGEX_B)


def estado_expr(origen, motivo):
    mapa = F.create_map(*[F.lit(x) for kv in ESTADOS.items() for x in kv])
    return F.coalesce(
        mapa[origen],
        F.when(F.coalesce(motivo, F.lit("")) != "", F.lit("FALLIDO"))
         .otherwise(F.lit(ESTADO_DESCONOCIDO)),
    )


def construir():
    # YouTube y Facebook: tal cual, solo con nombre canonico.
    dist = spark.table(DISTRIBUCION).select(
        nombre_canonico(F.col("video_name")).alias("video_name"),
        F.col("canal").cast("string"),
        F.col("estado").cast("string").alias("estado_origen"),
        F.col("id_externo").cast("string"),
        F.col("url").cast("string"),
        F.col("motivo_fallo").cast("string"),
        F.coalesce(F.col("run_id").cast("string"), F.lit("unknown")).alias("run_id"),
        # Esta fuente ya sobrescribe: no conserva intentos. Cadena vacia, no NULL.
        F.lit("").alias("clave_intento"),
        F.coalesce(F.col("intentos").cast("int"), F.lit(1)).alias("intentos"),
        F.lit("distribucion_video").alias("fuente"),
        F.col("actualizado_en").alias("_ts"),
    ).where(F.col("canal").isin("youtube", "facebook"))

    # El nombre canonico sale del regex, no de `guiones_usados`: esa tabla usa
    # la misma regla, asi que unir contra ella devolvia siempre el mismo valor.
    # Se elimino el join (antes `_canon` y `_canon_tt` coincidian por
    # construccion) y con el la lectura de `guiones_usados`.
    tt = spark.table(SUBIDOS).select(
        nombre_canonico(F.col("video_name")).alias("_canon_tt"),
        F.col("status").cast("string").alias("estado_origen"),
        F.col("publish_id").cast("string").alias("id_externo"),
        F.col("fail_reason").cast("string").alias("motivo_fallo"),
        F.coalesce(F.col("run_id").cast("string"), F.lit("unknown")).alias("run_id"),
        # El intento se identifica por su marca de tiempo: dos reintentos del
        # mismo video dan dos filas aunque el run_id sea el mismo ('unknown').
        # Se usa epoch (unix_micros) y no date_format: date_format depende de
        # spark.sql.session.timeZone, asi que la misma fila tendria otra clave
        # en otro huso y el MERGE volveria a insertarla.
        F.coalesce(
            F.unix_micros(F.col("uploaded_at")).cast("string"),
            F.lit(""),
        ).alias("clave_intento"),
        F.col("uploaded_at").alias("_ts"),
    )
    tt = tt.select(
        F.col("_canon_tt").alias("video_name"),
        F.lit("tiktok").alias("canal"),
        "estado_origen", "id_externo",
        F.lit(None).cast("string").alias("url"),
        "motivo_fallo", "run_id", "clave_intento",
        F.lit(1).alias("intentos"),
        F.lit("tiktok_uploads").alias("fuente"),
        "_ts",
    )

    df = dist.unionByName(tt)
    if run_id_param:
        df = df.where(F.col("run_id") == run_id_param)
    # `fecha` es la particion y `to_date` usa la zona de la sesion: se fija UTC
    # para que la misma fila caiga siempre el mismo dia. `clave_intento` es
    # epoch y no depende de esto, pero la particion si.
    df = df.withColumn("fecha", F.to_date(F.to_utc_timestamp("_ts", "UTC")))
    if fecha_param:
        df = df.where(F.col("fecha") == F.lit(fecha_param).cast("date"))

    # Una fila por clave: si la fuente repite (video, canal, run, intento), gana
    # la ultima. Para YouTube y Facebook clave_intento es constante, asi que se
    # comporta como antes; para TikTok separa cada reintento.
    # Desempate por estado_origen: con el mismo _ts, row_number elegia al azar y
    # dos corridas podian escribir filas distintas para la misma clave.
    w = Window.partitionBy(*CLAVE) \
              .orderBy(F.col("_ts").desc(), F.col("estado_origen").asc_nulls_last())
    df = (df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1")
            .withColumn("estado", estado_expr(F.col("estado_origen"), F.col("motivo_fallo")))
            .withColumn("registrado_en", F.current_timestamp()))
    return df.select(
        "video_name", "canal", "estado", "estado_origen", "id_externo", "url",
        "motivo_fallo", "run_id", "clave_intento", "intentos", "fuente", "fecha",
        F.col("_ts").alias("intento_en"), "registrado_en",
    )


def escribir():
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {LOG} (
            video_name STRING, canal STRING, estado STRING, estado_origen STRING,
            id_externo STRING, url STRING, motivo_fallo STRING, run_id STRING,
            -- Sin NOT NULL: una tabla migrada por ALTER no lo tendria y las dos
            -- versiones de la tabla deben tener el mismo esquema.
            clave_intento STRING,
            intentos INT, fuente STRING, fecha DATE, intento_en TIMESTAMP,
            registrado_en TIMESTAMP
        ) USING DELTA PARTITIONED BY (fecha)""")
    # `IF NOT EXISTS` no altera una tabla creada con el esquema anterior, que
    # carecia de `clave_intento` e `intento_en`, y el MERGE fallaria. Se añaden
    # si faltan; son aditivas, no tocan las filas ya escritas.
    actuales = {f.name for f in spark.table(LOG).schema.fields}
    if "clave_intento" not in actuales:
        spark.sql(f"ALTER TABLE {LOG} ADD COLUMNS (clave_intento STRING)")
        print("clave_intento añadida a una tabla existente.")
    if "intento_en" not in actuales:
        spark.sql(f"ALTER TABLE {LOG} ADD COLUMNS (intento_en TIMESTAMP)")

    # Migracion, idempotente y FUERA del `if`: si el ALTER funciona y algo falla
    # despues, la proxima corrida ya no entraría en el `if` y las filas quedarian
    # en NULL para siempre. Ejecutarlo siempre cuesta casi nada.
    #  1. Las filas viejas de YouTube/Facebook (esquema de 3 columnas) valen
    #     clave ''. Es su clave correcta, la misma que escribe `construir()`.
    spark.sql(f"UPDATE {LOG} SET clave_intento = '' "
              f"WHERE clave_intento IS NULL AND canal <> 'tiktok'")
    #  2. Las de TikTok con clave '' vienen del esquema viejo, que perdia los
    #     intentos: se BORRAN, no se conservan, porque `construir()` las
    #     reconstruye completas desde `tiktok_uploads`. Si se dejaran, quedaria
    #     una fila fantasma por video que el MERGE nunca emparejaria.
    borradas = spark.sql(f"SELECT COUNT(*) AS n FROM {LOG} "
                         f"WHERE canal = 'tiktok' AND clave_intento = ''").first()["n"]
    if borradas:
        spark.sql(f"DELETE FROM {LOG} WHERE canal = 'tiktok' AND clave_intento = ''")
        print(f"{borradas} filas viejas de TikTok borradas; se reconstruyen.")
    construir().createOrReplaceTempView("_publicacion_log_nuevo")
    spark.sql(f"""
        MERGE INTO {LOG} AS t
        USING _publicacion_log_nuevo AS s
        ON  {' AND '.join(f't.{c} <=> s.{c}' for c in CLAVE)}
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *""")
    return spark.table("_publicacion_log_nuevo").groupBy("canal", "estado").count().collect()


try:
    resumen = escribir()
    print(f"{LOG} actualizado:")
    for r in sorted(resumen, key=lambda r: (r["canal"], r["estado"])):
        print(f"  {r['canal']:9} {r['estado']:18} {r['count']}")
    salida = "ok"
except Exception as exc:
    # La publicacion ya ocurrio: se pierde solo el registro. No se relanza el
    # DAG. La tarea queda en verde a proposito, asi que el aviso va por partida
    # doble: al log de la tarea y al mensaje final, que es lo que se ve en la UI.
    print(f"ERROR registrando en {LOG} ({type(exc).__name__}): {exc}")
    salida = "error_log"

if salida != "ok":
    print(f"ATENCION: la publicacion ocurrio pero {LOG} no se actualizo. "
          f"Reejecuta el cierre para reconstruir la tabla; el MERGE es idempotente.")

dbutils.notebook.exit(salida)
