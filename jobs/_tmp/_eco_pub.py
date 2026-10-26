# Databricks notebook source
# Publica una lista plana de cadenas como task value.
dbutils.jobs.taskValues.set(key="lista", value=["AAA", "BBB"])
print("publicado")
