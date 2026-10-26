# Databricks notebook source
dbutils.jobs.taskValues.set(key="manifiesto", value="/Volumes/tiktok/default/videos/lotes/lote_X.json")
dbutils.jobs.taskValues.set(key="ids", value=[
    "7689607819489791250", "7690499505489333526", "7699804340013354262"])
dbutils.jobs.taskValues.set(key="nombres", value=[
    "motivacional_7689607819489791250.mp4", "motivacional_7690499505489333526.mp4"])
print("publicado")
