#!/usr/bin/env bash
# Genera un video vertical original de 9 s (1080x1920) con ffmpeg: fondo animado, texto y audio sintético.
# Uso: FFMPEG=/ruta/ffmpeg bash scripts/generate_original_video.sh [salida.mp4]
set -euo pipefail

FF="${FFMPEG:-ffmpeg}"
OUT="${1:-omg_original.mp4}"
FONT_BOLD="${FONT_BOLD:-C\\:/Windows/Fonts/impact.ttf}"
FONT_TEXT="${FONT_TEXT:-C\\:/Windows/Fonts/arialbd.ttf}"

"$FF" -y \
  -f lavfi -i "gradients=s=1080x1920:d=9:c0=0xff2d95:c1=0x2d6bff:c2=0xffe14a:speed=0.6:nb_colors=3,format=yuv420p" \
  -f lavfi -i "sine=f=220:d=9" \
  -f lavfi -i "sine=f=330:d=9" \
  -filter_complex "[0:v]drawtext=fontfile='${FONT_BOLD}':text='OMG':fontsize='300+40*sin(6*t)':fontcolor=white:borderw=14:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-150,drawtext=fontfile='${FONT_TEXT}':text='wait for it...':fontsize=84:fontcolor=white:borderw=6:bordercolor=black:x=(w-text_w)/2:y=h/2+120:enable='lt(t,5)',drawtext=fontfile='${FONT_BOLD}':text='YOU WON''T BELIEVE THIS':fontsize=90:fontcolor=yellow:borderw=8:bordercolor=black:x=(w-text_w)/2:y=h/2+120:enable='gte(t,5)'[v];[1:a][2:a]amix=inputs=2,volume=0.25,tremolo=f=4:d=0.6[a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -preset medium -crf 20 -r 30 -c:a aac -b:a 128k \
  -movflags +faststart -t 9 "$OUT"
