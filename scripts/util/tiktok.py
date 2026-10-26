import os
from yt_dlp import YoutubeDL

# Array con las URLs de los videos de TikTok
video_urls = [
    "https://www.tiktok.com/@sostopt/video/7698687502042795284",
    "https://www.tiktok.com/@boseok751/video/7695359721066253576",
    "https://www.tiktok.com/@zi.wii/video/7254219705974672642",
    "https://www.tiktok.com/@frida_c.jota/video/7690795251350392085",
    "https://www.tiktok.com/@.stxgm/video/7429675110409260310",
    "https://www.tiktok.com/@litbaddieshot/video/7698508897773997330",
    "https://www.tiktok.com/@donovanje4/video/7698606902762065172",
    "https://www.tiktok.com/@greatness_.7/video/7688717389608897810",
    "https://www.tiktok.com/@4uben_9unior/video/7411176161964772650",
    "https://www.tiktok.com/@pareja.sim.filtro/video/7698536901761879297"
]

def descargar_lote_tiktoks(urls):
    # Nombre de la carpeta de destino
    nombre_carpeta = "Descargas_TikTok"
    
    # Crear la carpeta si no existe
    os.makedirs(nombre_carpeta, exist_ok=True)

    # Opciones de configuración para yt-dlp indicando la ruta de salida
    opciones = {
        'format': 'best',
        'outtmpl': os.path.join(nombre_carpeta, '%(title)s.%(ext)s'),
        'noplaylist': True,
    }

    print(f"Se encontraron {len(urls)} videos para descargar en la carpeta '{nombre_carpeta}'.\n")
    
    with YoutubeDL(opciones) as ydl:
        for i, url in enumerate(urls, start=1):
            try:
                print(f"[{i}/{len(urls)}] Descargando: {url}")
                ydl.download([url])
                print(" ¡Descargado con éxito!\n")
            except Exception as e:
                print(f" Error al descargar {url}: {e}\n")

if __name__ == "__main__":
    descargar_lote_tiktoks(video_urls)