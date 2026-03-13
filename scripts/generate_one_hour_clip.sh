#!/bin/bash -

ffmpeg -f lavfi -i color=c=black:s=1920x1080:r=30:d=3600 \
  -vf "drawtext=fontfile=/usr/share/fonts/ubuntu/UbuntuMono-R.ttf:text='%{eif\\:t+1\\:d}':x=(w-text_w)/2:y=(h-text_h)/2:fontsize=200:fontcolor=white" \
  -c:v libx264 -pix_fmt yuv420p -crf 18 -preset medium output.mkv
