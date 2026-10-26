# Databricks notebook source
dbutils.jobs.taskValues.set(key="ids", value=[{"video_fuente_id": "AAA"}, {"video_fuente_id": "BBB"}])
dbutils.jobs.taskValues.set(key="ids_planos", value=["AAA", "BBB"])
print("publicado")
