# Databricks notebook source
# MAGIC %md
# MAGIC # TikTok ingest: Apify -> bronze -> silver -> gold
# MAGIC Token comes from secret scope `tiktok_pipeline/apify_token`. Never pass it as a job parameter.

# COMMAND ----------

import json
import time

import requests
from pyspark.sql import functions as F
from pyspark.sql.types import (BooleanType, IntegerType, LongType, StringType,
                               StructField, StructType, TimestampType)

dbutils.widgets.text("hashtags", "funny,omg,viral")
dbutils.widgets.text("results_per_hashtag", "50")
dbutils.widgets.text("max_charge_usd", "1.0")
dbutils.widgets.text("run_timeout_secs", "900")

HASHTAGS = [h.strip() for h in dbutils.widgets.get("hashtags").split(",") if h.strip()]
RESULTS_PER_HASHTAG = int(dbutils.widgets.get("results_per_hashtag"))
MAX_CHARGE_USD = float(dbutils.widgets.get("max_charge_usd"))
RUN_TIMEOUT_SECS = int(dbutils.widgets.get("run_timeout_secs"))

ACTOR = "clockworks~tiktok-scraper"
API = "https://api.apify.com/v2"
BRONZE = "tiktok.bronze.raw_videos"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
# Tope duro de gasto por ejecución. El widget no puede superarlo: el actor se
# factura por resultados y un typo en el parámetro no debe multiplicar la factura.
HARD_CHARGE_CAP_USD = 2.0

if MAX_CHARGE_USD > HARD_CHARGE_CAP_USD:
    raise ValueError(
        f"max_charge_usd={MAX_CHARGE_USD} supera el tope duro "
        f"({HARD_CHARGE_CAP_USD} USD) configurado en el notebook"
    )
print(
    f"Coste maximo solicitado: {MAX_CHARGE_USD} USD "
    f"({len(HASHTAGS)} hashtags x {RESULTS_PER_HASHTAG} resultados)"
)

token = dbutils.secrets.get("tiktok_pipeline", "apify_token")
session = requests.Session()
session.headers.update({"Authorization": f"Bearer {token}"})

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Run the Apify actor and wait for it

# COMMAND ----------

resp = session.post(
    f"{API}/acts/{ACTOR}/runs",
    params={"maxTotalChargeUsd": MAX_CHARGE_USD, "timeout": RUN_TIMEOUT_SECS},
    json={"hashtags": HASHTAGS, "resultsPerPage": RESULTS_PER_HASHTAG},
    timeout=60,
)
resp.raise_for_status()
run = resp.json()["data"]
run_id, dataset_id = run["id"], run["defaultDatasetId"]
print(f"Started Apify run {run_id}, dataset {dataset_id}")

deadline = time.time() + RUN_TIMEOUT_SECS + 120
while True:
    r = session.get(f"{API}/actor-runs/{run_id}", timeout=60)
    r.raise_for_status()
    run = r.json()["data"]
    if run["status"] in TERMINAL:
        break
    if time.time() > deadline:
        session.post(f"{API}/actor-runs/{run_id}/abort", timeout=60)
        raise TimeoutError(f"Apify run {run_id} did not finish in time; abort requested")
    time.sleep(10)

print(f"Run finished: {run['status']} | {run.get('statusMessage')} | usage USD {run.get('usageTotalUsd')}")
if run["status"] != "SUCCEEDED":
    raise RuntimeError(f"Apify run {run_id} ended with status {run['status']}: {run.get('statusMessage')}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Download dataset (paginated)

# COMMAND ----------

items, offset, page = [], 0, 500
while True:
    r = session.get(
        f"{API}/datasets/{dataset_id}/items",
        params={"format": "json", "clean": "true", "limit": page, "offset": offset},
        timeout=120,
    )
    r.raise_for_status()
    batch = r.json()
    if not batch:
        break
    items.extend(batch)
    offset += len(batch)

if not items:
    raise RuntimeError(f"Apify dataset {dataset_id} is empty; refusing to write nothing silently")
print(f"Downloaded {len(items)} items")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Append to bronze

# COMMAND ----------

# El parseo vive en ingest_lib/parse.py para poder probarlo sin cluster.
# Hay que importarlo desde la carpeta hermana del notebook en el workspace.
import os
import sys

_nb_path = (dbutils.notebook.entry_point.getDbutils().notebook()
            .getContext().notebookPath().get())
_nb_dir = "/Workspace" + os.path.dirname(_nb_path)
if _nb_dir not in sys.path:
    sys.path.insert(0, _nb_dir)

from ingest_lib.parse import parse_items

rows, skipped = parse_items(items, run_id)

print(f"{len(rows)} valid rows, {skipped} skipped (missing id/author)")
if not rows:
    raise RuntimeError("No valid rows after parsing; the actor output schema may have changed")

schema = StructType([
    StructField("video_id", StringType()), StructField("created_at", TimestampType()),
    StructField("text_language", StringType()), StructField("is_ad", BooleanType()),
    StructField("is_sponsored", BooleanType()), StructField("has_shop_product", BooleanType()),
    StructField("digg_count", LongType()), StructField("share_count", LongType()),
    StructField("play_count", LongType()), StructField("collect_count", LongType()),
    StructField("comment_count", LongType()), StructField("repost_count", LongType()),
    StructField("author_id", StringType()), StructField("author_name", StringType()),
    StructField("author_verified", BooleanType()), StructField("author_fans", LongType()),
    StructField("author_hearts", LongType()), StructField("author_videos", LongType()),
    StructField("duration_sec", IntegerType()), StructField("music_id", StringType()),
    StructField("video_url", StringType()), StructField("source_hashtag", StringType()),
    StructField("apify_run_id", StringType()),
])

df = spark.createDataFrame(rows, schema).withColumn("ingested_at", F.current_timestamp())
df.write.mode("append").saveAsTable(BRONZE)
print(f"Appended {df.count()} rows to {BRONZE}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Rebuild silver (latest snapshot per video) and gold (creator KPIs)

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE tiktok.silver.videos USING DELTA
COMMENT 'Deduplicated TikTok videos (latest snapshot per video_id) with derived metrics' AS
SELECT
  video_id, created_at, text_language, is_ad, is_sponsored, has_shop_product,
  play_count, digg_count AS like_count, share_count, collect_count AS save_count, comment_count, repost_count,
  author_id, author_name, author_verified, author_fans, author_hearts, author_videos,
  duration_sec, music_id, video_url, source_hashtag, ingested_at,
  digg_count + share_count + comment_count + collect_count AS total_interactions,
  CASE WHEN play_count > 0 THEN (digg_count + share_count + comment_count + collect_count) / play_count END AS engagement_rate,
  CASE WHEN play_count > 0 THEN share_count / play_count END AS share_rate,
  CASE WHEN author_fans > 0 THEN play_count / author_fans END AS views_per_follower,
  DATEDIFF(CAST(ingested_at AS DATE), CAST(created_at AS DATE)) AS age_days
FROM (
  -- Desempate explicito: dos observaciones del mismo run comparten ingested_at,
  -- y sin un segundo criterio el snapshot elegido no es determinista entre
  -- ejecuciones. apify_run_id desempata; el resto de columnas lo hace estable
  -- incluso dentro de una misma ejecucion.
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY video_id
    ORDER BY ingested_at DESC, apify_run_id DESC,
             play_count DESC, digg_count DESC
  ) AS rn
  FROM tiktok.bronze.raw_videos
)
WHERE rn = 1
""")

spark.sql("""
CREATE OR REPLACE TABLE tiktok.gold.creator_kpis USING DELTA
COMMENT 'Per-creator KPIs from public TikTok metrics; no real revenue data' AS
SELECT
  author_id,
  MAX(author_name) AS author_name,
  MAX(author_verified) AS author_verified,
  MAX(author_fans) AS followers,
  COUNT(*) AS videos_sampled,
  SUM(play_count) AS total_views,
  ROUND(AVG(play_count), 0) AS avg_views,
  ROUND(SUM(total_interactions) / NULLIF(SUM(play_count), 0), 4) AS engagement_rate,
  ROUND(SUM(share_count) / NULLIF(SUM(play_count), 0), 4) AS share_rate,
  ROUND(AVG(views_per_follower), 2) AS avg_views_per_follower,
  ROUND(AVG(duration_sec), 0) AS avg_duration_sec,
  CASE
    WHEN MAX(author_fans) >= 100000 THEN 'macro'
    WHEN MAX(author_fans) >= 10000 THEN 'mid'
    WHEN MAX(author_fans) >= 1000 THEN 'micro'
    ELSE 'nano'
  END AS creator_tier,
  current_timestamp() AS computed_at
FROM tiktok.silver.videos
GROUP BY author_id
""")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Curate viral candidates: Spanish, <=30 days, multi-viral, best metrics

# COMMAND ----------

# "Multiviral" = destaca a la vez en las tres dimensiones de viralidad dentro del
# lote: share_rate, engagement_rate y views_per_follower por encima de la mediana.
# viral_score ordena a los mejores candidatos (escala log para que un solo mega-video
# no domine el score solo por vistas).
spark.sql("""
CREATE OR REPLACE TABLE tiktok.gold.viral_candidates USING DELTA
COMMENT 'Top candidates to viralize: Spanish, max 30 days old, multi-dimensionally viral' AS
WITH fresh AS (
  SELECT *,
         (0.30 * LOG10(play_count + 1)
        + 0.25 * LOG10(share_count + 1)
        + 0.15 * LOG10(comment_count + 1)
        + 0.15 * LOG10(digg_count + 1)
        + 0.15 * (engagement_rate * 100)) AS viral_score
  FROM tiktok.silver.videos
  -- Solo se filtra lo que define el lote; las medianas se calculan sobre ese
  -- conjunto y PERCENTILE ignora los NULL, de modo que cada ratio se compara
  -- contra la mediana de los videos que si lo tienen.
  WHERE (text_language LIKE 'es%')
    AND created_at IS NOT NULL
    AND DATEDIFF(current_date(), CAST(created_at AS DATE)) <= 30
    AND play_count > 0
),
medians AS (
  SELECT PERCENTILE(share_rate, 0.5)         AS med_share_rate,
         PERCENTILE(engagement_rate, 0.5)    AS med_eng_rate,
         PERCENTILE(views_per_follower, 0.5) AS med_vpf
  FROM fresh
)
SELECT f.*
FROM fresh f CROSS JOIN medians m
WHERE f.share_rate >= m.med_share_rate
  AND f.engagement_rate >= m.med_eng_rate
  AND f.views_per_follower >= m.med_vpf
ORDER BY f.viral_score DESC
LIMIT 50
""")

cand = spark.sql("""
SELECT video_id, author_name, created_at, play_count, share_count, digg_count,
       ROUND(engagement_rate, 4) AS engagement_rate,
       ROUND(share_rate, 4)        AS share_rate,
       ROUND(viral_score, 2)       AS viral_score,
       age_days, video_url
FROM tiktok.gold.viral_candidates
ORDER BY viral_score DESC
""")
cand.show(50, truncate=False)

counts = spark.sql("""
SELECT (SELECT COUNT(*) FROM tiktok.bronze.raw_videos) AS bronze_rows,
       (SELECT COUNT(*) FROM tiktok.silver.videos) AS silver_videos,
       (SELECT COUNT(*) FROM tiktok.gold.creator_kpis) AS gold_creators,
       (SELECT COUNT(*) FROM tiktok.gold.viral_candidates) AS viral_candidates
""").first()
print(counts)
dbutils.notebook.exit(json.dumps(counts.asDict()))