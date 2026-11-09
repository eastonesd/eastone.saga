"""畫風實驗室：同一批鏡頭 × 多組算圖設定，輸出對照圖。

設定檔 lab/<preset>.json：
{
  "shots": 4,                     # 從第 1 集挑幾個鏡頭（會盡量含一個雙人鏡頭）
  "configs": [
    {"name": "A", "unet": "jugg", "steps": 28, "cfg": 5, "ip": 0.5,
     "loras": {"chibi": 0.8, "d25": 0.3},
     "style": "畫風提示詞（取代 STYLES 的 prompt）", "ref": "定妝照提示詞", "neg": "負面提示詞",
     "add": {"liu_bei": "black hair, long black beard"}}     # 個別角色補充描述
  ]
}
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import paths, render

LORA_LIB = {
    "chibi": {"repo": "Muapi/nendoroid-chibi-art-style-capture-lora-xl",
              "file": "nendoroid-chibi-art-style-capture-lora-xl.safetensors"},
    "d25": {"repo": "MarkBW/2.5Dstyle-xl", "file": "2.5DRealistic.safetensors"},
}
LAB = Path(__file__).resolve().parent.parent / "lab"


def build(slug: str, preset: str) -> tuple[dict, list[dict], list[dict]]:
    spec = json.loads((LAB / f"{preset}.json").read_text(encoding="utf-8"))
    bible = paths.read_json(paths.bible_path(slug))
    job = render.build_job(slug, [], video=False, poster=False)
    base_chars = {c["id"]: c for c in job["characters"]}
    chars_by_id = {c["id"]: c for c in bible["characters"]}
    ep = paths.read_json(paths.episode_path(slug, 1))
    picks = [s for s in ep["shots"] if s["characters"]][: spec.get("shots", 4)]
    duo = next((s for s in ep["shots"] if len(s["characters"]) >= 2), None)
    if duo and duo not in picks:
        picks[-1] = duo
    used = sorted({c for s in picks for c in s["characters"][:2]})
    job["characters"], job["shots"], job["loras"] = [], [], {}
    job.pop("style_lora", None)
    for k, cfg in enumerate(spec["configs"]):
        for n in cfg.get("loras", {}):
            job["loras"][n] = LORA_LIB[n]
        render_kw = {x: cfg[x] for x in ("unet", "steps", "cfg", "neg") if x in cfg}
        render_kw["loras"] = cfg.get("loras", {})
        add = cfg.get("add", {})
        for cid in used:
            c0 = base_chars[cid]
            look = chars_by_id[cid].get("key_features") or chars_by_id[cid]["appearance"]
            job["characters"].append({
                **c0, "id": f"{cid}__{k}", "ref_b64": None, "ref_url": None, "render": render_kw,
                "ref_prompt": f"{cfg.get('ref', '')}, single person, head and shoulders, facing camera, "
                              f"{look}, {add.get(cid, '')}".strip(", ")})
        b2 = dict(bible, render_style=None)
        for i, s in enumerate(picks):
            prompt = render.shot_prompt(s, b2)
            extra = ", ".join(add[c] for c in s["characters"][:2] if c in add)
            # CLIP 只吃前 77 個 token：畫風與角色關鍵特徵放最前面
            head = ", ".join(p for p in (cfg.get("style", ""), extra) if p)
            prompt = prompt.replace(render._short(render.style_of(b2)["prompt"], 12), head)
            job["shots"].append({
                "key": f"lab_{k}_{i}", "prompt": prompt,
                "refs": [f"{c}__{k}" for c in s["characters"][:2]],
                "seed": render._shot_seed(slug, 1, s["id"]), "ip": cfg.get("ip", 0.5), **render_kw})
    return job, spec["configs"], picks


def run(slug: str, preset: str, mock: bool = False) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    job, configs, picks = build(slug, preset)
    dl = paths.BUILD_DIR / slug / "_lab"
    shutil.rmtree(dl, ignore_errors=True)
    manifest = render._run_mock(job, dl) if mock else render._run_on_kaggle(job, dl)
    try:
        font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 22)
    except OSError:
        font = ImageFont.load_default()
    cols, tw, th, rh = len(configs), 560, 320, 280
    nref = max(1, len({c["id"].split("__")[0] for c in job["characters"]}))
    rw = tw // nref
    sheet = Image.new("RGB", (cols * tw, 40 + len(picks) * th + rh), "black")
    d = ImageDraw.Draw(sheet)
    for k, cfg in enumerate(configs):
        d.text((k * tw + 8, 8), cfg["name"], fill="white", font=font)
        for i in range(len(picks)):
            f = next(iter(dl.rglob(f"lab_{k}_{i}.png")), None)
            if f:
                sheet.paste(Image.open(f).convert("RGB").resize((tw, th)), (k * tw, 40 + i * th))
        refs = [c for c in job["characters"] if c["id"].endswith(f"__{k}")]
        for j, c in enumerate(refs):
            f = next(iter(dl.rglob(f"ref_{c['id']}.jpg")), None)
            if f:
                im = Image.open(f).convert("RGB")
                im = im.resize((rw, int(im.height * rw / im.width))).crop((0, 0, rw, rh))
                sheet.paste(im, (k * tw + j * rw, 40 + len(picks) * th))
    out = paths.BUILD_DIR / slug / f"lab-{preset}.jpg"
    sheet.save(out, quality=90)
    print(f"▶ 對照圖：{out}（{len(manifest.get('images', []))} 張）")
    return out
