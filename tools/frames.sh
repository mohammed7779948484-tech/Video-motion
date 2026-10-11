#!/usr/bin/env bash
# Review helper: contact sheets from a rendered MP4 (outputs to review/, which is gitignored).
#   tools/frames.sh <video.mp4> sheet "0.3,1.4,3.9,..."     -> review/sheet.png (one frame per time, 5 per row)
#   tools/frames.sh <video.mp4> strip <start> <end> [name]  -> review/<name>.png (every frame in the window)
set -euo pipefail
VID="$1"; MODE="$2"; OUT=review; mkdir -p "$OUT"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
if [ "$MODE" = "sheet" ]; then
  i=0; IFS=',' read -ra TS <<< "$3"
  for t in "${TS[@]}"; do
    ffmpeg -v error -y -ss "$t" -i "$VID" -frames:v 1 -vf "scale=270:480,drawtext=text='${t}s':x=8:y=8:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6" "$TMP/$(printf %03d $i).png"
    i=$((i+1))
  done
  ffmpeg -v error -y -pattern_type glob -i "$TMP/*.png" -vf "tile=5x$(( (i+4)/5 )):padding=6:color=0x222222" -frames:v 1 "$OUT/sheet.png"
  echo "$OUT/sheet.png"
else
  S="$3"; E="$4"; NAME="${5:-strip-$S-$E}"
  ffmpeg -v error -y -ss "$S" -to "$E" -i "$VID" -vf "scale=216:384,drawtext=text='%{pts\:hms}':x=6:y=6:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6" "$TMP/%03d.png"
  N=$(ls "$TMP" | wc -l)
  ffmpeg -v error -y -pattern_type glob -i "$TMP/*.png" -vf "tile=8x$(( (N+7)/8 )):padding=4:color=0x222222" -frames:v 1 "$OUT/$NAME.png"
  echo "$OUT/$NAME.png"
fi
