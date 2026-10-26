# Databricks notebook source
dbutils.widgets.text("valor", "")
dbutils.widgets.text("manifiesto", "")
dbutils.widgets.text("dry_run", "")
dbutils.notebook.exit("valor=[" + dbutils.widgets.get("valor") + "] manifiesto=["
                      + dbutils.widgets.get("manifiesto") + "]")
