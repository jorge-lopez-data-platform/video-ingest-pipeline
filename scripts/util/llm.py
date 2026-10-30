"""Llamadas a Claude (Anthropic SDK) que devuelven JSON validado contra un esquema."""

from __future__ import annotations

import json

import anthropic

MODELO_DEFECTO = "claude-haiku-5-5"


def crear_cliente(dbutils):
    return anthropic.Anthropic(api_key=dbutils.secrets.get("tiktok_pipeline", "anthropic_api_key"))


def pedir_json(cliente, modelo: str, system: str, usuario: str, esquema: dict,
               max_tokens: int = 8000) -> dict:
    resp = cliente.messages.create(
        model=modelo,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": usuario}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": esquema},
        },
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("el modelo rechazó la petición")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("respuesta truncada por max_tokens")
    texto = next(b.text for b in resp.content if b.type == "text")
    return json.loads(texto)
