-- Reconstruye en tiktok.gold.guiones_60s los guiones de los 14 videos que ya
-- existen, leyendo las versiones que Delta conserva. La tabla se sobrescribía
-- en cada corrida y hoy solo tiene 4 filas, así que sin esto no se pueden
-- regenerar los videos antiguos (su guion no está en ninguna otra tabla).
--
-- Se elige la versión MÁS RECIENTE de cada ID: si un guion se corrigió después,
-- la última es la que produjo el video que hay en el volumen.
INSERT INTO tiktok.gold.guiones_60s
SELECT video_fuente_id, video_fuente_url, fuente_plays, hook, open_loop, bloque_1,
       pattern_interrupt, bloque_2, pre_cierre, cierre, cta, hashtags,
       datos_a_verificar, confianza, texto_pantalla_1, texto_pantalla_2,
       texto_pantalla_3, palabras_reales, duracion_estimada, generado_en
FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_fuente_id ORDER BY v DESC) AS rn
  FROM (
    SELECT 12 AS v, * FROM tiktok.gold.guiones_60s VERSION AS OF 12
    UNION ALL SELECT 10, * FROM tiktok.gold.guiones_60s VERSION AS OF 10
    UNION ALL SELECT 9,  * FROM tiktok.gold.guiones_60s VERSION AS OF 9
    UNION ALL SELECT 7,  * FROM tiktok.gold.guiones_60s VERSION AS OF 7
  )
  WHERE video_fuente_id IN (SELECT video_fuente_id FROM tiktok.gold.guiones_usados)
) AS historial
WHERE rn = 1
  AND video_fuente_id NOT IN (SELECT video_fuente_id FROM tiktok.gold.guiones_60s)
