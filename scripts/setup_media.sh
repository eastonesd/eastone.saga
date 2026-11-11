#!/usr/bin/env bash
# 安裝 FFmpeg（含 libass 字幕）與思源（Noto CJK）中文字型。
# 不走 apt：Ubuntu 套件伺服器常卡住 10 分鐘以上。改從 GitHub 直接下載，並由 actions/cache 保存在 ~/.saga-tools。
# 任何一步失敗才退回 apt。
set -uo pipefail
T="$HOME/.saga-tools"
FONTS="$HOME/.local/share/fonts"
mkdir -p "$T/bin" "$FONTS"

get() {  # get URL OUT：最多 3 次、每次 120 秒
  for i in 1 2 3; do
    curl -fsSL --max-time 120 -o "$2" "$1" && return 0
    echo "  下載失敗（第 $i 次）：$1"; sleep 5
  done
  return 1
}

ok=1
if [ ! -x "$T/bin/ffmpeg" ]; then
  echo "▶ 下載 FFmpeg"
  if get https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz /tmp/ff.tar.xz; then
    tar -xJf /tmp/ff.tar.xz -C /tmp && cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe "$T/bin/" || ok=0
    rm -rf /tmp/ff.tar.xz /tmp/ffmpeg-master-latest-linux64-gpl
  else
    ok=0
  fi
fi
for f in Sans/OTC/NotoSansCJK-Regular.ttc Sans/OTC/NotoSansCJK-Bold.ttc Sans/OTC/NotoSansCJK-Black.ttc \
         Serif/OTC/NotoSerifCJK-Bold.ttc Serif/OTC/NotoSerifCJK-Black.ttc; do
  name=$(basename "$f")
  [ -s "$T/fonts/$name" ] && continue
  mkdir -p "$T/fonts"
  echo "▶ 下載字型 $name"
  get "https://github.com/notofonts/noto-cjk/raw/main/$f" "$T/fonts/$name" || ok=0
done
cp -n "$T"/fonts/*.ttc "$FONTS/" 2>/dev/null || true
fc-cache -f "$FONTS" >/dev/null 2>&1 || true

if [ "$ok" = 1 ] && "$T/bin/ffmpeg" -hide_banner -filters 2>/dev/null | grep -q " ass " && fc-list | grep -q "Noto Sans CJK"; then
  echo "$T/bin" >> "${GITHUB_PATH:-/dev/null}"
  export PATH="$T/bin:$PATH"
  echo "✓ FFmpeg $("$T/bin/ffmpeg" -version | head -1 | cut -d' ' -f3)、思源字型 $(fc-list | grep -c 'Noto S.* CJK') 套"
  exit 0
fi

echo "⚠ 直接下載不完整，改用 apt"
for i in 1 2 3; do
  sudo timeout 240 apt-get update -qq && sudo timeout 240 apt-get install -y -qq --no-install-recommends ffmpeg fonts-noto-cjk fonts-noto-cjk-extra > /dev/null && break
  echo "apt 第 $i 次失敗，重試…"; sleep 10
done
command -v ffmpeg
