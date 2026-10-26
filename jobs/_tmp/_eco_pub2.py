# Databricks notebook source
dbutils.jobs.taskValues.set(key="cadenas", value=["AAA", "BBB"])
dbutils.jobs.taskValues.set(key="diccionarios", value=[
    {"video_name": "AAA.mp4", "video_fuente_id": "111"},
    {"video_name": "BBB.mp4", "video_fuente_id": "222"}])
print("publicado")
