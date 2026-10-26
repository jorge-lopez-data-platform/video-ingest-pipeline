# Databricks notebook source
dbutils.widgets.text("video_fuente_id", "<SIN-PARAM>")
valor = dbutils.widgets.get("video_fuente_id")
print("RECIBIDO:", repr(valor))
