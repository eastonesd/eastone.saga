"""劇集海報：Kaggle 畫底圖（主角＋反派對峙），這裡疊上大字劇名排版。

輸出 series/<slug>/poster/
  art_v1..4.jpg     底圖候選（Kaggle 產生）
  candidates.jpg    四張候選並排（App 裡挑選用）
  poster.jpg        1080×1920（TikTok / Reels / Shorts / 紅果直式封面）
  poster_3x4.jpg    1080×1440（Instagram 主頁格狀預覽，同一張海報裁切）
  choice.txt        選用第幾張底圖（預設 1）
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import paths

W, H = 1080, 1920
ART_W, ART_H = 832, 1216  # SDXL 標準直式尺寸，比 768×1344 不容易畫出重複的人


def poster_dir(slug: str) -> Path:
    return paths.series_dir(slug) / "poster"


def _pick_cast(bible: dict) -> list[dict]:
    chars = bible["characters"]
    hero = next((c for c in chars if "主" in c.get("role", "")), chars[0])
    villain = next((c for c in chars if "反" in c.get("role", "") and c is not hero),
                   next((c for c in chars if c is not hero), hero))
    return [hero, villain]


def poster_shots(slug: str, bible: dict, style: dict, n: int = 4) -> list[dict]:
    """給 Kaggle 的底圖工作：主角在左、反派在右，史詩對峙構圖，不要文字"""
    from .render import _short

    hero, villain = _pick_cast(bible)
    loc = bible["locations"][0]["visual"] if bible.get("locations") else ""
    look = lambda c: _short(c.get("key_features") or c["appearance"], 14)  # noqa: E731
    prompt = (f"epic movie poster key art, {_short(style['prompt'], 10)}, exactly two people facing off, "
              f"on the left {look(hero)}, on the right {look(villain)}, dramatic low angle, "
              f"{_short(loc, 8)}, volumetric god rays, high contrast, no text")
    ids = [hero["id"], villain["id"]] if villain is not hero else [hero["id"]]
    return [{"key": f"poster_v{i}", "prompt": prompt, "refs": ids, "w": ART_W, "h": ART_H,
             "seed": 9001 + i * 7919} for i in range(1, n + 1)]


def needs_art(slug: str) -> bool:
    return not any(poster_dir(slug).glob("art_v*.jpg"))


# ── 排版 ──────────────────────────────────────────────────────

def _font(family: str, size: int) -> ImageFont.FreeTypeFont:
    r = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True)
    path = r.stdout.strip()
    try:
        return ImageFont.truetype(path, size, index=_ttc_index(path, family))
    except Exception:
        return ImageFont.load_default()


def _ttc_index(path: str, family: str) -> int:
    if not path.endswith(".ttc"):
        return 0
    # Noto CJK 的 .ttc 依序為 JP, KR, SC, TC, HK（有的版本 SC/TC 順序不同）→ 逐一找 TC
    for i in range(10):
        try:
            f = ImageFont.truetype(path, 20, index=i)
            if "TC" in " ".join(f.getname()):
                return i
        except Exception:
            break
    return 0


def _cover(img: Image.Image, w: int, h: int, focus_y: float = 0.4) -> Image.Image:
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x = (img.width - w) // 2
    y = int((img.height - h) * focus_y)
    return img.crop((x, y, x + w, y + h))


def _vgrad(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    g = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        g.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4)))
    return g.resize((w, h))


def _text_layer(text: str, font, fill_top, fill_bottom, stroke: int, glow: tuple, spacing: int = 0):
    """回傳（有漸層金屬字＋描邊＋外光暈的 RGBA 圖層）"""
    chars = list(text)
    widths = [font.getbbox(c)[2] - font.getbbox(c)[0] for c in chars]
    asc, desc = font.getmetrics()
    tw = sum(widths) + spacing * (len(chars) - 1) + stroke * 4
    th = asc + desc + stroke * 4
    pad = 60
    mask = Image.new("L", (tw + pad * 2, th + pad * 2), 0)
    d = ImageDraw.Draw(mask)
    x = pad + stroke * 2
    for c, cw in zip(chars, widths):
        d.text((x - font.getbbox(c)[0], pad + stroke * 2), c, font=font, fill=255)
        x += cw + spacing
    outline = mask.filter(ImageFilter.MaxFilter(stroke * 2 + 1))
    glow_mask = outline.filter(ImageFilter.GaussianBlur(26))
    layer = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", mask.size, glow), (0, 0), glow_mask)
    layer.paste(Image.new("RGBA", mask.size, (12, 6, 4, 255)), (0, 0), outline)
    grad = _vgrad(mask.width, mask.height, fill_top, fill_bottom)
    layer.paste(grad, (0, 0), mask)
    # 上緣高光
    hi = ImageChops.multiply(mask, _vgrad(mask.width, mask.height, (255, 255, 255, 255), (0, 0, 0, 0)).split()[3])
    layer.paste(Image.new("RGBA", mask.size, (255, 255, 240, 90)), (0, 0), hi)
    return layer


def _split_title(title: str) -> tuple[str, str]:
    parts = re.split(r"[：:｜|—\-]", title, maxsplit=1)
    main = parts[0].strip()
    sub = parts[1].strip() if len(parts) > 1 else ""
    return main, sub


def _fit(text: str, family: str, start: int, max_w: int, spacing_ratio: float = 0.0):
    size = start
    while size > 40:
        f = _font(family, size)
        w = sum(f.getbbox(c)[2] - f.getbbox(c)[0] for c in text) + int(size * spacing_ratio) * (len(text) - 1)
        if w <= max_w:
            return f, int(size * spacing_ratio)
        size -= 6
    return _font(family, size), int(size * spacing_ratio)


def compose(slug: str, art: Path, out: Path) -> Path:
    bible = paths.read_json(paths.bible_path(slug))
    main, sub = _split_title(bible["title"])
    canvas = _cover(Image.open(art).convert("RGB"), W, H).convert("RGBA")
    # 暗角＋上下漸層，讓文字跳出來
    canvas.alpha_composite(_vgrad(W, int(H * 0.22), (0, 0, 0, 170), (0, 0, 0, 0)), (0, 0))
    canvas.alpha_composite(_vgrad(W, int(H * 0.52), (0, 0, 0, 0), (4, 2, 8, 245)), (0, H - int(H * 0.52)))
    vign = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vign).ellipse((-W * 0.25, -H * 0.1, W * 1.25, H * 1.05), fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(120))
    canvas = Image.composite(canvas, Image.new("RGBA", (W, H), (0, 0, 0, 255)), vign)

    # 主標：燙金大字
    f_main, sp = _fit(main, "Noto Serif CJK TC:style=Black", 260, int(W * 0.9), 0.04)
    t = _text_layer(main, f_main, (255, 252, 226, 255), (246, 176, 38, 255), 7, (255, 110, 20, 190), sp)
    y_main = int(H * 0.565)
    canvas.alpha_composite(t, ((W - t.width) // 2, y_main))
    y = y_main + t.height - 70

    # 副標：紅底白字橫幅
    if sub:
        f_sub, sp2 = _fit(sub, "Noto Sans CJK TC:style=Black", 92, int(W * 0.7), 0.18)
        sw = sum(f_sub.getbbox(c)[2] - f_sub.getbbox(c)[0] for c in sub) + sp2 * (len(sub) - 1)
        bh = int(f_sub.size * 1.45)
        bx, by = (W - sw) // 2 - 46, y
        band = Image.new("RGBA", (sw + 92, bh), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        bd.polygon([(18, 0), (band.width, 0), (band.width - 18, bh), (0, bh)], fill=(178, 18, 28, 235))
        canvas.alpha_composite(band, (bx, by))
        d = ImageDraw.Draw(canvas)
        x = bx + 46
        for c in sub:
            d.text((x - f_sub.getbbox(c)[0], by + int(bh * 0.12)), c, font=f_sub, fill=(255, 255, 255, 255))
            x += f_sub.getbbox(c)[2] - f_sub.getbbox(c)[0] + sp2
        y = by + bh + 40

    # 一句話賣點（最多兩行）
    d = ImageDraw.Draw(canvas)
    f_log = _font("Noto Sans CJK TC:style=Bold", 40)
    log = re.sub(r"\s+", "", bible.get("logline", ""))
    lines, cur = [], ""
    for c in log:
        if f_log.getlength(cur + c) > W * 0.82 and c not in "，。！？、：；」』）":  # 標點不放行首
            lines.append(cur)
            cur = c
        else:
            cur += c
    lines.append(cur)
    for ln in lines[:2]:
        lw = f_log.getlength(ln)
        d.text(((W - lw) / 2, y), ln, font=f_log, fill=(235, 230, 220, 235), stroke_width=2, stroke_fill=(0, 0, 0, 200))
        y += 58

    # 角標：集數、AI 標示、出品
    f_badge = _font("Noto Sans CJK TC:style=Black", 44)
    badge = f"全 {bible.get('episode_count', '')} 集"
    bw = f_badge.getlength(badge) + 56
    d.rounded_rectangle((54, 64, 54 + bw, 64 + 78), radius=39, fill=(214, 150, 40, 235))
    d.text((54 + 28, 72), badge, font=f_badge, fill=(20, 10, 0, 255))
    f_small = _font("Noto Sans CJK TC:style=Bold", 30)
    ai = "AI 生成內容"
    d.rounded_rectangle((W - 54 - f_small.getlength(ai) - 36, 78, W - 54, 78 + 52), radius=12,
                        outline=(255, 255, 255, 180), width=2)
    d.text((W - 54 - f_small.getlength(ai) - 18, 84), ai, font=f_small, fill=(255, 255, 255, 220))
    f_mark = _font("Noto Sans CJK TC:style=Bold", 30)
    mark = "SAGA.ai 出品"
    d.text(((W - f_mark.getlength(mark)) / 2, max(y + 24, H - 76)), mark, font=f_mark, fill=(200, 200, 215, 170))

    out.parent.mkdir(parents=True, exist_ok=True)
    final = canvas.convert("RGB")
    final.save(out, quality=92)
    # 3:4 版本：同一張海報，從上方裁掉一段，保留劇名
    final.crop((0, H - 1440, W, H)).save(out.with_name(out.stem + "_3x4.jpg"), quality=92)
    return out


def finalize(slug: str) -> Path | None:
    """依 choice.txt（預設 1）合成正式海報；底圖候選另外做縮圖方便挑"""
    d = poster_dir(slug)
    arts = sorted(d.glob("art_v*.jpg"))
    if not arts:
        return None
    try:
        pick = int((d / "choice.txt").read_text().strip())
    except (OSError, ValueError):
        pick = 1
    art = d / f"art_v{pick}.jpg"
    if not art.exists():
        art = arts[0]
    sheet = Image.new("RGB", (len(arts) * 300, 438), "black")
    for i, a in enumerate(arts):
        sheet.paste(Image.open(a).convert("RGB").resize((300, 438)), (i * 300, 0))
    sheet.save(d / "candidates.jpg", quality=85)
    out = compose(slug, art, d / "poster.jpg")
    print(f"▶ 海報：{out}（底圖 {art.name}）", flush=True)
    return out
