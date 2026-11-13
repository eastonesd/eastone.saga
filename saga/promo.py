"""帶貨片：商品實拍圖＋慢速推近＋配音＋字幕 → 15 秒直式短片（1080×1920，純 CPU）。

跟短劇不同，畫面一律用商品原圖，不讓 AI 生成，避免把商品畫錯。
規格放在 promo/<slug>/spec.json：

{
  "name": "商品名稱",
  "voice": "zh-TW-HsiaoChenNeural",           # 選填
  "rate": "+8%",                              # 選填，語速
  "scenes": [
    {"img": "https://… 或 repo 內路徑", "head": "畫面大標", "say": "旁白（同時當字幕）", "min": 2.5},
    …
    {"img": "…", "head": "連結放在留言", "say": "連結放在留言。", "end": true}   # 最後一幕：行動呼籲
  ]
}

用法：python -m saga.promo <slug>  → promo/<slug>/out/<slug>.mp4、cover.jpg
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
ROOT = Path(__file__).resolve().parent.parent
FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", 3),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 3),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 3),
]
INK, GOLD, BG = (245, 242, 235), (248, 196, 90), (14, 16, 20)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path, idx in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size, index=idx)
    raise RuntimeError("找不到中文字型（請安裝 fonts-noto-cjk）")


def _ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗：{r.stderr[-1500:]}")


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def _load(src: str, base: Path) -> Image.Image:
    if src.startswith("http"):
        r = requests.get(src, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    p = Path(src)
    return Image.open(p if p.is_absolute() else base / p).convert("RGB")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """中文逐字換行；「｜」強制斷行"""
    lines: list[str] = []
    for part in text.split("｜"):
        cur = ""
        for ch in part:
            if draw.textlength(cur + ch, font=font) > width and cur:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    return lines


def _frame(img: Image.Image, head: str, say: str, end: bool) -> Image.Image:
    """一幕的畫面：模糊底圖 → 商品原圖（不裁切、不變形）→ 上方大標 → 下方字幕"""
    bg = img.resize((W, int(W * img.height / img.width))).resize((W, H)) if img.width else img
    bg = bg.filter(ImageFilter.GaussianBlur(48))
    bg = Image.blend(bg, Image.new("RGB", (W, H), BG), 0.72)
    d = ImageDraw.Draw(bg)

    # 商品原圖：等比縮放到 960 寬，置中
    box = 960 if not end else 760
    s = min(box / img.width, box / img.height)
    pic = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    mask = Image.new("L", pic.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *pic.size), radius=36, fill=255)
    top = 520 if not end else 470
    bg.paste(pic, ((W - pic.width) // 2, top + (box - pic.height) // 2), mask)

    # 大標
    hf = _font(92 if not end else 104)
    lines = _wrap(d, head, hf, W - 160)[:2]
    y = 250 if len(lines) == 1 else 190
    if end:
        y = 1300
    for ln in lines:
        tw = d.textlength(ln, font=hf)
        d.text(((W - tw) / 2, y), ln, font=hf, fill=GOLD if end else INK, stroke_width=4, stroke_fill=(0, 0, 0))
        y += 118

    # 字幕（旁白）
    if say and not end:
        sf = _font(54)
        sl = _wrap(d, say, sf, W - 200)[:3]
        h = len(sl) * 74 + 40
        y0 = 1560
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle((70, y0, W - 70, y0 + h), radius=28, fill=(0, 0, 0, 150))
        bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
        d = ImageDraw.Draw(bg)
        yy = y0 + 20
        for ln in sl:
            tw = d.textlength(ln, font=sf)
            d.text(((W - tw) / 2, yy), ln, font=sf, fill=INK)
            yy += 74
    return bg


async def _tts(text: str, out: Path, voice: str, rate: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def _speak(text: str, out: Path, voice: str, rate: str) -> None:
    if os.environ.get("PROMO_FAKE_TTS"):           # 本機測試用：依字數產生靜音
        _ff("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{0.2 * len(text) + 0.3:.2f}", str(out))
        return
    for i in range(3):
        try:
            asyncio.run(_tts(text, out, voice, rate))
            if out.exists() and out.stat().st_size > 1000:
                return
        except Exception as e:  # 微軟語音偶爾逾時，重試
            print(f"  ⚠ 配音重試 {i + 1}：{e}", flush=True)
    raise RuntimeError(f"配音失敗：{text}")


def build(slug: str) -> Path:
    base = ROOT / "promo" / slug
    spec = json.loads((base / "spec.json").read_text(encoding="utf-8"))
    out = base / "out"
    out.mkdir(parents=True, exist_ok=True)
    voice, rate = spec.get("voice", "zh-TW-HsiaoChenNeural"), spec.get("rate", "+8%")
    clips, audios = [], []
    for i, sc in enumerate(spec["scenes"]):
        print(f"・第 {i + 1} 幕：{sc['head']}", flush=True)
        img = _load(sc["img"], base)
        frame = _frame(img, sc["head"], sc.get("say", ""), bool(sc.get("end")))
        fpath = out / f"f{i}.png"
        frame.save(fpath)
        if i == 0:
            frame.save(out / "cover.jpg", quality=92)
        a = out / f"a{i}.mp3"
        _speak((sc.get("say") or sc["head"]).replace("｜", ""), a, voice, rate)
        dur = max(_duration(a) + 0.35, float(sc.get("min", 1.8)))
        n = round(dur * FPS)
        # 慢速推近 4%，讓靜態圖有呼吸感
        _ff("-loop", "1", "-i", str(fpath), "-vf",
            f"scale={W * 2}:{H * 2},zoompan=z='1+0.04*on/{n}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={n}:s={W}x{H}:fps={FPS},format=yuv420p",
            "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out / f"v{i}.mp4"))
        _ff("-i", str(a), "-af", f"apad,atrim=0:{n / FPS:.3f}", "-ar", "44100", "-ac", "2", str(out / f"a{i}.wav"))
        clips.append(out / f"v{i}.mp4"); audios.append(out / f"a{i}.wav")
    (out / "v.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    (out / "a.txt").write_text("".join(f"file '{c.name}'\n" for c in audios))
    _ff("-f", "concat", "-safe", "0", "-i", str(out / "v.txt"), "-c", "copy", str(out / "video.mp4"))
    _ff("-f", "concat", "-safe", "0", "-i", str(out / "a.txt"), str(out / "audio.wav"))
    final = out / f"{slug}.mp4"
    _ff("-i", str(out / "video.mp4"), "-i", str(out / "audio.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart", str(final))
    print(f"✓ 完成 {final}（{_duration(final):.1f} 秒）", flush=True)
    return final


if __name__ == "__main__":
    build(sys.argv[1])
