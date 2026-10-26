"""Transcribe la voz propia con Whisper y genera subtítulos ASS.

Whisper puede alucinar palabras en silencios, así que dos guardias viven aquí:
el agrupador nunca descarta palabras, y la duración transcrita se compara con
la del audio antes de escribir subtítulos.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts import common

DESVIO_MAXIMO = 0.15
CODIGO_DURACION_SOSPECHOSA = 3


def agrupar_chunks(
    palabras: list[dict],
    max_palabras: int = 5,
    min_palabras: int = 3,
    pausa_corte: float = 0.4,
) -> list[dict]:
    """Agrupa palabras en chunks legibles sin perder ninguna.

    Corta al alcanzar `max_palabras` o ante una pausa mayor que `pausa_corte`.
    `min_palabras` es solo un objetivo: un chunk corto se emite igual, porque
    descartar palabras dejaría huecos entre los subtítulos y lo hablado.
    """
    if not palabras:
        return []

    chunks: list[dict] = []
    actual: list[dict] = []

    def cerrar() -> None:
        if actual:
            chunks.append({
                "inicio": actual[0]["inicio"],
                "fin": actual[-1]["fin"],
                "texto": " ".join(p["texto"] for p in actual),
            })
            actual.clear()

    for palabra in palabras:
        if actual:
            pausa = palabra["inicio"] - actual[-1]["fin"]
            if pausa > pausa_corte or len(actual) >= max_palabras:
                cerrar()
        actual.append(palabra)
    cerrar()
    return chunks


def duracion_hablada(chunks: list[dict]) -> float:
    """Suma el tiempo realmente hablado, ignorando las pausas.

    Se prefiere al span (último fin menos primer inicio) porque un chunk
    alucinado al final del audio hace que el span parezca correcto mientras
    la mayor parte de la grabación está en silencio.
    """
    return sum(chunk["fin"] - chunk["inicio"] for chunk in chunks)


def verificar_cobertura(duracion_audio: float, chunks: list[dict]) -> str | None:
    """Comprueba que la transcripción ocupa el audio sin estar vacía por dentro.

    Dos medidas, porque cada una sola engaña: el span detecta silencio al final
    y la densidad detecta palabras sueltas inventadas en un audio casi mudo.
    """
    if duracion_audio <= 0:
        return (
            "No se pudo leer la duración del audio; no hay forma de verificar "
            "si la transcripción cubre la grabación."
        )
    if not chunks:
        return "La transcripción no tiene bloques."

    span = chunks[-1]["fin"] - chunks[0]["inicio"]
    if span <= 0:
        return "La transcripción no tiene duración."
    if span / duracion_audio < 0.85:
        return (
            f"El texto cubre {span:.1f}s de {duracion_audio:.1f}s de audio "
            f"({span / duracion_audio:.0%}). Probablemente haya silencio o voz perdida."
        )

    densidad = duracion_hablada(chunks) / span
    if densidad < 0.35:
        return (
            f"Solo el {densidad:.0%} del tramo transcrito tiene voz: parece una "
            f"alucinación sobre un audio casi en silencio."
        )
    return None


def verificar_duracion(duracion_audio: float, duracion_texto: float) -> str | None:
    """Devuelve un aviso si el texto transcrito no cubre el audio.

    Una desviación grande suele significar alucinación en los silencios o un
    audio que no es la voz esperada.
    """
    if duracion_audio <= 0:
        return (
            "No se pudo leer la duración del audio; no hay forma de verificar "
            "si la transcripción cubre la grabación."
        )
    desvio = abs(duracion_audio - duracion_texto) / duracion_audio
    if desvio <= DESVIO_MAXIMO:
        return None
    return (
        f"La transcripción dura {duracion_texto:.1f}s pero el audio {duracion_audio:.1f}s "
        f"({desvio:.0%} de desviación). Revisa el audio antes de confiar en los subtítulos."
    )


def _timestamp(segundos: float) -> str:
    """Redondea en centésimas antes de partir, para no escribir "0:00:60.00"."""
    total = int(round(max(segundos, 0.0) * 100))
    horas, total = divmod(total, 360000)
    minutos, resto = divmod(total, 6000)
    return f"{horas}:{minutos:02d}:{resto // 100:02d}.{resto % 100:02d}"


def generar_ass(chunks: list[dict], ancho: int = 1080, alto: int = 1920) -> str:
    """Construye un .ass con texto grande, negrita y borde negro."""
    cabecera = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ancho}
PlayResY: {alto}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lineas = []
    for chunk in chunks:
        texto = chunk["texto"].replace("\n", " ")
        lineas.append(
            f"Dialogue: 0,{_timestamp(chunk['inicio'])},{_timestamp(chunk['fin'])},"
            f"TikTok,,0,0,0,,{texto}"
        )
    return cabecera + "\n".join(lineas) + "\n"


def transcribir(audio: Path, modelo: str = "medium") -> list[dict]:
    """Transcribe un audio en español y devuelve palabras con tiempos."""
    from faster_whisper import WhisperModel

    motor = WhisperModel(modelo, device="cpu", compute_type="int8")
    segmentos, _ = motor.transcribe(
        str(audio), language="es", word_timestamps=True, vad_filter=True
    )
    palabras = []
    for segmento in segmentos:
        for palabra in segmento.words or []:
            texto = palabra.word.strip()
            if texto:
                palabras.append({
                    "inicio": round(float(palabra.start), 3),
                    "fin": round(float(palabra.end), 3),
                    "texto": texto,
                })
    return palabras


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, help="Ruta al wav de la voz")
    parser.add_argument("--out-dir", default=".", help="Carpeta de salida")
    parser.add_argument("--modelo", default="medium", help="Modelo de Whisper")
    args = parser.parse_args(argv)
    common.configurar_salida()

    audio = Path(args.audio)
    if not audio.is_file():
        print(f"❌ No existe {audio}", file=sys.stderr)
        return 1

    info = common.probe(audio)
    duracion_audio = info["duracion"] or 0.0

    palabras = transcribir(audio, args.modelo)
    if not palabras:
        print("❌ Whisper no devolvió ninguna palabra. ¿El audio tiene voz?", file=sys.stderr)
        return CODIGO_DURACION_SOSPECHOSA

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "palabras.json").write_text(
        json.dumps(palabras, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    chunks = agrupar_chunks(palabras)
    aviso = verificar_cobertura(duracion_audio, chunks)
    if aviso:
        print(f"⚠️  {aviso}", file=sys.stderr)
        print("No se escriben subtítulos.", file=sys.stderr)
        return CODIGO_DURACION_SOSPECHOSA

    (out_dir / "subs.ass").write_text(generar_ass(chunks), encoding="utf-8")
    print(f"✅ {len(palabras)} palabras, {len(chunks)} subtítulos en {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
