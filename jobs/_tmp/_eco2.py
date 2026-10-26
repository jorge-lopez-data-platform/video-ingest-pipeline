# Databricks notebook source
dbutils.widgets.text("video_fuente_id", "")
v = dbutils.widgets.get("video_fuente_id")
dbutils.notebook.exit("VALOR=[" + str(v) + "]")
