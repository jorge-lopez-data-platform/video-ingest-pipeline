"""Diseña el personaje a partir del guion, con DeepSeek, y garantiza que no se repita.

El guion completo se envía como JSON al modelo; el modelo decide la ficha visual
(piel, pelo, ropa, accesorio, expresión) para que la figura tenga que ver con lo
que se cuenta. La ficha es un dict plano de colores y etiquetas — no una imagen
generada por IA — porque no hay presupuesto de generación de imágenes y porque
Pillow necesita colores concretos para animar boca y ojos.

Unicidad: el LLM es estocástico y puede devolver dos veces la misma ficha. Por
eso se pide `evitar` y, además, se comprueba la `firma()` contra las ya usadas;
si se repite, se reintenta con una instrucción explícita y, como último recurso,
se fuerza una variación determinista sobre la ficha repetida. Sin ese respaldo,
el requisito «siempre uno distinto» dependería de la suerte del muestreo.
"""

from __future__ import annotations

import json
import random
import re

from scripts.escenarios import TODOS as ESCENARIOS
from scripts.personaje import (ACCESORIOS, EXPRESIONES, PELO_ESTILOS,
                               ROPA_TIPOS, SPEC_DEFECTO, firma,
                               spec_normalizada)

PROMPT_SISTEMA = """Eres director de arte de una cuenta de TikTok en español. Recibes el guion completo de un video (JSON) y diseñas UN personaje que lo cuente en cámara.

La ficha se dibuja con formas planas de Pillow, así que solo puedes elegir colores y etiquetas de estas listas. Responde SIEMPRE en JSON válido, sin markdown.

REGLAS:
- El personaje debe encajar con el TEMA y el TONO del guion (motivacional, fe, superación, terror, curiosidad…). Si el guion es de terror, no pongas un jersey alegre.
- El ESCENARIO es el lugar donde está el personaje: elígelo por lo que cuenta el guion, no al azar. Un guion de disciplina encaja en "gimnasio"; uno de fe, en "paraiso"; uno sobre una pérdida, en "noche" o "abstracto".
- La EXPRESIÓN es la emoción dominante del guion, no la del momento final: si el guion va de miedo que se resuelve, elige "preocupada" o "seria", no "sonriente".
- Debe verse como una persona común creíble, no como un superhéroe ni un personaje de dibujos famoso.
- Varía respecto a las fichas de `evitar`: cambia al menos el pelo o la ropa.
- No describas rasgos de personas reales identificables (políticos, famosos, etnias concretas).
- `razon` explica en una frase por qué esa ficha sirve a este guion.

CAMPOS:
- piel, pelo_color, ropa_color, ojos: hex "#RRGGBB" con buen contraste entre sí y con el escenario. En escenarios claros (playa, desierto) evita piel casi blanca; en "noche" evita ropa casi negra.
- pelo_estilo: uno de {pelos}
- ropa_tipo: uno de {ropas}
- accesorio: uno de {accesorios}
- expresion: uno de {expresiones}
- escenario: uno de {escenarios}

OUTPUT:
{{
  "piel": "#E9B58F",
  "pelo_color": "#2B2018",
  "pelo_estilo": "corto",
  "ojos": "#4A3426",
  "ropa_tipo": "jersey",
  "ropa_color": "#1D6F66",
  "accesorio": "ninguno",
  "expresion": "calida",
  "escenario": "paraiso",
  "razon": "..."
}}""".format(
    pelos=", ".join(PELO_ESTILOS), ropas=", ".join(ROPA_TIPOS),
    accesorios=", ".join(ACCESORIOS), expresiones=", ".join(EXPRESIONES),
    escenarios=", ".join(ESCENARIOS),
)


def _parsear(raw: str) -> dict:
    """Extrae el JSON de la respuesta aunque venga con texto alrededor."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def disenar(cliente, modelo: str, guion: dict, evitar: list[dict] | None = None,
            intentos: int = 3) -> dict:
    """Pide al LLM la ficha del personaje y la devuelve validada.

    `cliente` es un `OpenAI` ya configurado contra OpenRouter. `evitar` son las
    fichas que ya se usaron en otros videos; se pasan al prompt y se comprueban
    después, porque el modelo no siempre las respeta.
    """
    evitar = evitar or []
    firmas_evitadas = {firma(s) for s in evitar}

    # El guion va completo: el modelo necesita el tono, no solo el titular.
    contenido = json.dumps(guion, ensure_ascii=False, default=str)
    usuario = (
        f"GUION (JSON):\n{contenido}\n\n"
        f"FICHAS YA USADAS (no repitas ninguna):\n"
        f"{json.dumps(evitar[-12:], ensure_ascii=False) if evitar else '(ninguna)'}"
    )

    ultima: dict | None = None
    for intento in range(intentos):
        resp = cliente.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": usuario},
            ],
            # Alta a propósito: se busca variedad entre guiones. La unicidad no
            # se deja solo a la temperatura, se comprueba abajo.
            temperature=0.9 + 0.03 * intento,
            response_format={"type": "json_object"},
        )
        ficha = spec_normalizada(_parsear(resp.choices[0].message.content))
        if firma(ficha) not in firmas_evitadas:
            return ficha
        ultima = ficha
        print(f"  ficha repetida en el intento {intento + 1}: {firma(ficha)}")
        usuario += ("\n\nATENCIÓN: acabas de devolver una ficha ya usada. "
                    "Cambia pelo_estilo o ropa_tipo y ropa_color; si sigues "
                    "coincidiendo, cambia también el escenario o la expresión.")

    # Último recurso: variar la ficha repetida de forma determinista. Preferible
    # a publicar dos videos con el mismo personaje, que es lo que se pidió evitar.
    print("  el LLM no logró una ficha nueva; se varía la última de forma local")
    return _variar(ultima or dict(SPEC_DEFECTO), firmas_evitadas)


def _variar(ficha: dict, firmas_evitadas: set[str]) -> dict:
    """Genera una ficha distinta cambiando estilo, ropa, escenario y emoción."""
    rnd = random.Random(firma(ficha))
    for estilo in PELO_ESTILOS:
        for tipo in ROPA_TIPOS:
            for _ in range(24):
                candidata = dict(ficha)
                candidata["pelo_estilo"] = estilo
                candidata["ropa_tipo"] = tipo
                candidata["ropa_color"] = "#{:02X}{:02X}{:02X}".format(
                    rnd.randrange(24, 232), rnd.randrange(24, 232), rnd.randrange(24, 232)
                )
                candidata["accesorio"] = rnd.choice(ACCESORIOS)
                candidata["expresion"] = rnd.choice(EXPRESIONES)
                candidata["escenario"] = rnd.choice(ESCENARIOS)
                if firma(candidata) not in firmas_evitadas:
                    return spec_normalizada(candidata)
    return spec_normalizada(ficha)
