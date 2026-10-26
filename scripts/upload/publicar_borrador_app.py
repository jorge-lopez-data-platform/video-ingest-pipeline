"""Publica desde la app de TikTok el borrador que ya subió el pipeline.

El tramo final del pipeline no se puede cerrar por API: `inbox/video/init/` no
acepta título ni hashtags, y Direct Post está bloqueado porque la app de TikTok
no está auditada. El caption hay que escribirlo a mano en el móvil, y eso lo
hace ARTEMIS.

Este script es la versión parametrizada de esa ruta: le pasas el nombre del
video, resuelve su `_publicacion.txt`, compone la tarea con el texto exacto y la
lanza contra el dispositivo. La ruta de pantalla ya está verificada (ver
`project_tiktok_publicacion_app` en la memoria), así que el agente no explora:
va directo a Mensajes, abre la notificación del borrador, escribe y publica.

Uso:

    # ver qué haría, sin tocar el móvil (por defecto)
    uv run python scripts/publicar_borrador_app.py motivacional_7699804340013354262.mp4

    # publicar de verdad
    uv run python scripts/publicar_borrador_app.py motivacional_7699804340013354262.mp4 --publicar

Salidas: código 0 si el agente confirma la publicación, 1 si falla o no puede
verificar el caption, 2 si el entorno no está listo (sin dispositivo, sin ADB).
Cada intento real se anota en `publicaciones_app.jsonl` para auditoría.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VIDEOS_DIR = "/Volumes/tiktok/default/videos"
REMOTO = "dbfs:" + VIDEOS_DIR

# ARTEMIS vive en una carpeta aparte y se lanza con `uv run`; si algún día
# cambia de sitio basta con exportar ARTEMIS_DIR.
ARTEMIS_DIR = os.environ.get("ARTEMIS_DIR", r"C:\Users\lopee\artemis")
DISPOSITIVO = os.environ.get("ARTEMIS_DEVICE_SERIAL", "ZY22FLD3VS")
PAQUETE = "com.zhiliaoapp.musically"

REGISTRO = Path(__file__).resolve().parent.parent / "publicaciones_app.jsonl"


def _utf8() -> None:
    """Fuerza UTF-8 en stdout/stderr (la consola de Windows usa cp1252)."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def normalizar(nombre: str) -> str:
    """Nombre del mp4 con extensión, sin rutas: es el identificador compartido."""
    base = os.path.basename(nombre.strip())
    return base if base.endswith(".mp4") else base + ".mp4"


def _stem(nombre: str) -> str:
    return os.path.splitext(normalizar(nombre))[0]


def _leer_local(nombre: str) -> Path | None:
    """El `.txt` hermano cuando el volumen está montado (dentro de Databricks)."""
    raiz = Path(VIDEOS_DIR)
    plano = raiz / (_stem(nombre) + "_publicacion.txt")
    if plano.is_file():
        return plano
    # Las carpetas AAAA/MM/DD, de la más reciente a la más antigua, para que una
    # regeneración gane a la copia anterior.
    for anio in sorted((p for p in raiz.glob("*") if p.name.isdigit()), reverse=True):
        for mes in sorted((p for p in anio.glob("*") if p.name.isdigit()), reverse=True):
            for dia in sorted((p for p in mes.glob("*") if p.name.isdigit()), reverse=True):
                candidato = dia / (_stem(nombre) + "_publicacion.txt")
                if candidato.is_file():
                    return candidato
    return None


def _cli(*args: str) -> subprocess.CompletedProcess:
    """Ejecuta el CLI de Databricks, que aquí sí resuelve el perfil `DEFAULT`.

    El SDK se descartó: en este equipo no hay token en el entorno y
    `WorkspaceClient()` falla con "cannot configure default credentials".
    """
    return subprocess.run(
        ["databricks", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def _subcarpetas(ruta: str) -> list[str]:
    """Nombres de las subcarpetas de una ruta remota, de la más nueva a la más vieja."""
    salida = _cli("fs", "ls", ruta).stdout
    # Las carpetas de fecha son solo dígitos (2026 / 10 / 25); los ficheros
    # llevan extensión. Así no hace falta interpretar las marcas de `ls -l`.
    nombres = {l.strip().split("/")[-1] for l in salida.splitlines() if l.strip()}
    return sorted((n for n in nombres if n.isdigit()), reverse=True)


def _descargar(nombre: str, destino: Path) -> Path | None:
    """Baja el `.txt` del volumen con `databricks fs cp` cuando no hay montaje."""
    objetivo = _stem(nombre) + "_publicacion.txt"

    candidatos = [f"{REMOTO}/{objetivo}"]
    for anio in _subcarpetas(REMOTO):
        for mes in _subcarpetas(f"{REMOTO}/{anio}"):
            for dia in _subcarpetas(f"{REMOTO}/{anio}/{mes}"):
                candidatos.append(f"{REMOTO}/{anio}/{mes}/{dia}/{objetivo}")

    for remota in candidatos:
        resultado = _cli("fs", "cp", remota, str(destino), "--overwrite")
        if resultado.returncode == 0:
            return destino
    return None


def leer_caption(nombre: str, archivo: str | None) -> tuple[str, str]:
    """Devuelve (título, hashtags) del fichero de publicación del video.

    Línea 1 = título, líneas siguientes = hashtags, igual que lo escribe
    `generar_video_guion` y lo lee `ws_upload_draft_video_v3`.
    """
    if archivo:
        ruta: Path | None = Path(archivo)
        if not ruta.is_file():
            raise SystemExit(f"No existe el fichero indicado: {ruta}")
    else:
        ruta = _leer_local(nombre)
        if ruta is None:
            temporal = Path(tempfile.mkdtemp(prefix="publicacion_")) / (_stem(nombre) + "_publicacion.txt")
            ruta = _descargar(nombre, temporal)
        if ruta is None:
            raise SystemExit(
                f"No se encontró {_stem(nombre)}_publicacion.txt en {VIDEOS_DIR}. "
                "Usa --caption-file para darlo a mano."
            )

    lineas = [l.strip() for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lineas:
        raise SystemExit(f"El fichero de publicación está vacío: {ruta}")
    return lineas[0], " ".join(lineas[1:])


def construir_tarea(
    nombre: str, titulo: str, hashtags: str, etiqueta_ia: bool = True
) -> str:
    """Tarea de ARTEMIS con la ruta ya verificada, sin pedir exploración.

    `etiqueta_ia=False` publica sin marcar "Contenido generado por IA": el
    interruptor de "Más opciones" debe quedar gris al pulsar Publicar.
    """
    caption = f"{titulo}\n\n{hashtags}".strip()
    if etiqueta_ia:
        paso_ia = (
            '8. Pulsa "Más opciones" y activa el interruptor "Contenido generado por IA" '
            '(si pide confirmar, pulsa "Activar").'
        )
        regla_ia = "- La etiqueta de IA debe quedar ACTIVADA al publicar."
    else:
        paso_ia = (
            '8. Pulsa "Más opciones" y comprueba el interruptor "Contenido generado por IA": '
            "debe quedar GRIS (desactivado). Si está celeste/activo, tócalo para desactivarlo "
            "y verifica que vuelve a gris. NO lo dejes activado."
        )
        regla_ia = (
            "- La etiqueta de IA debe quedar DESACTIVADA (gris) al publicar. Es el objetivo "
            "principal de esta corrida: si no consigues dejarla en gris, NO publiques y reporta "
            '"no se pudo desactivar la etiqueta de IA".'
        )
    return f"""Publica en TikTok el borrador del video {nombre}. Estás en la cuenta @alguienqueteescucha y la app ya está abierta.

RUTA VERIFICADA (síguela en este orden):
1. Abre la pestaña "Mensajes".
2. Entra en "Notificaciones del sistema".
3. Pulsa la notificación "Tu contenido de Lomasviral está listo - Edita tu video antes de compartirlo en TikTok". Es el borrador de este video; si hay varias, elige la de arriba.
4. En el editor, ANTES de tocar nada, compara el titular que aparece sobre el video con este: "{titulo}". Si NO coincide, pulsa Atrás, prueba con la siguiente notificación y repite; si ninguna coincide, detente y reporta "sin coincidencia" sin publicar nada.
5. Pulsa "Siguiente".
6. Pulsa el campo de descripción y borra lo que haya.
7. Escribe EXACTAMENTE este texto, sin corregirlo, sin añadir ni quitar hashtags:
{caption}
{paso_ia}
9. Comprueba que la privacidad es "Todos pueden ver este video".
10. Pulsa "Publicar" y espera a que termine la subida.
11. Reporta como resultado el texto exacto de la descripción que quedó publicada.

REGLAS:
{regla_ia}
- Escribe el texto tal cual está arriba: mayúsculas, tildes y almohadillas incluidas.
- No uses el analizador de video: en este equipo falla al extraer fotogramas. Usa la captura de pantalla.
- No cambies privacidad, no añadas música, no comentes, no des like.
- Si algo no está donde dice la ruta, describe la pantalla en el reporte en vez de improvisar."""


def dispositivo_listo() -> bool:
    """True si `adb devices` lista el serial esperado como `device`."""
    try:
        salida = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=20
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"AVISO: no se pudo ejecutar adb ({exc}); se intenta igualmente.")
        return True
    return any(
        linea.split()[0] == DISPOSITIVO and linea.split()[1] == "device"
        for linea in salida.splitlines()
        if linea.startswith(DISPOSITIVO)
    )


def lanzar_artemis(tarea: str, perfil: str, espera: int) -> int:
    """Ejecuta `artemis run` en su carpeta y devuelve su código de salida."""
    orden = [
        "uv", "run", "artemis", "run", tarea,
        "-p", perfil,
        "-a", PAQUETE,
        "-s", DISPOSITIVO,
    ]
    print(f"Lanzando ARTEMIS (perfil {perfil}, dispositivo {DISPOSITIVO})…")
    try:
        return subprocess.run(orden, cwd=ARTEMIS_DIR, timeout=espera).returncode
    except FileNotFoundError:
        print("No se encontró `uv`. Instálalo o exporta ARTEMIS_DIR a la carpeta correcta.")
        return 2
    except subprocess.TimeoutExpired:
        print(f"ARTEMIS no terminó en {espera}s; revisa el móvil antes de reintentar.")
        return 1


def anotar(registro: dict) -> None:
    """Añade una línea JSON al registro de auditoría (nunca rompe la ejecución)."""
    try:
        with open(REGISTRO, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"AVISO: no se pudo anotar en {REGISTRO} ({exc})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publica en la app de TikTok un borrador del pipeline, con su caption.",
    )
    parser.add_argument("video", help="Nombre del mp4 (con o sin extensión).")
    parser.add_argument(
        "--publicar", action="store_true",
        help="Publicar de verdad. Sin esta bandera solo compone y muestra la tarea.",
    )
    parser.add_argument(
        "--caption-file", default=None,
        help="Fichero de publicación alternativo (si no está en el volumen).",
    )
    parser.add_argument(
        "--perfil", default="flash", choices=["flash", "pro"],
        help="Perfil de ARTEMIS (por defecto flash: la ruta ya está verificada).",
    )
    parser.add_argument(
        "--sin-etiqueta-ia", action="store_true",
        help='Publicar sin marcar "Contenido generado por IA" (el interruptor debe quedar gris).',
    )
    parser.add_argument(
        "--espera", type=int, default=1800,
        help="Segundos máximos de espera para ARTEMIS (por defecto 1800).",
    )
    args = parser.parse_args(argv)

    _utf8()
    nombre = normalizar(args.video)
    titulo, hashtags = leer_caption(nombre, args.caption_file)
    tarea = construir_tarea(nombre, titulo, hashtags, etiqueta_ia=not args.sin_etiqueta_ia)

    print(f"Video:     {nombre}")
    print(f"Título:    {titulo}")
    print(f"Hashtags:  {hashtags or '(ninguno)'}")
    print(f"Etiqueta IA: {'desactivada (gris)' if args.sin_etiqueta_ia else 'activada'}")
    if not hashtags:
        print("AVISO: el fichero no trae hashtags; el caption irá solo con el título.")

    if not args.publicar:
        print("\n--- Tarea que se lanzaría (dry-run) ---\n")
        print(tarea)
        print("\nNada enviado. Añade --publicar para ejecutarla en el móvil.")
        return 0

    if not dispositivo_listo():
        print(f"El dispositivo {DISPOSITIVO} no aparece como 'device' en `adb devices`.")
        print("Conéctalo, desbloquéalo y acepta la depuración USB.")
        return 2

    codigo = lanzar_artemis(tarea, args.perfil, args.espera)
    anotar({
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": nombre,
        "titulo": titulo,
        "hashtags": hashtags,
        "dispositivo": DISPOSITIVO,
        "perfil": args.perfil,
        "etiqueta_ia": not args.sin_etiqueta_ia,
        "codigo": codigo,
        "resultado": "publicado" if codigo == 0 else "fallido",
    })
    print("✅ ARTEMIS confirma la publicación." if codigo == 0 else "❌ ARTEMIS no terminó bien.")
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
