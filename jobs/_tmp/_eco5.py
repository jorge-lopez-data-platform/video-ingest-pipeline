# Databricks notebook source
dbutils.widgets.text("id_lote", "")
dbutils.widgets.text("indice", "")
dbutils.notebook.exit("id_lote=[" + dbutils.widgets.get("id_lote")
                      + "] indice=[" + dbutils.widgets.get("indice") + "]")
