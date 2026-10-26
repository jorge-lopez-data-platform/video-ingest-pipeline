# Databricks notebook source
dbutils.widgets.text("valor", "")
dbutils.widgets.text("video_name", "")
dbutils.widgets.text("video_fuente_id", "")
dbutils.notebook.exit(
    "valor=[" + dbutils.widgets.get("valor") + "] "
    "video_name=[" + dbutils.widgets.get("video_name") + "] "
    "id=[" + dbutils.widgets.get("video_fuente_id") + "]")
