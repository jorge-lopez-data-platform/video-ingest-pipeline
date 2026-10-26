# Databricks notebook source
# Eco: imprime lo que le llega por parámetro, para ver si la sustitución funciona.
dbutils.widgets.text("valor", "")

v = dbutils.widgets.get("valor")
print("VALOR_RECIBIDO=[" + v + "]")
