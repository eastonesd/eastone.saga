"""美術部：把分鏡變成算圖工作單，送到 Kaggle 免費 GPU，跑完把圖抓回來。"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from . import paths

KERNEL_TEMPLATE = Path(__file__).with_name("kaggle_kernel.py")
IMAGE_MODEL = os.environ.get("SAGA_IMAGE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
VIDEO_MODEL = os.environ.get("SAGA_VIDEO_MODEL", "Wan-AI/Wan2.2-TI2V-5B-Diffusers")
KERNEL_SLUG = os.environ.get("SAGA_KERNEL_SLUG", "saga-render")

CAMERA = {
    "close-up": "close-up shot, face focus",
    "medium": "medium shot, waist up",
    "wide": "wide shot, full body, establishing",
    "over-shoulder": "over-the-shoulder shot",
}


def shot_prompt(shot: dict, bible: dict) -> str:
    chars = {c["id"]: c for c in bible["characters"]}
    locs = {l["id"]: l for l in bible["locations"]}
    looks = [chars[c]["appearance"] for c in shot["characters"][:2] if c in chars]
    parts = [
        CAMERA.get(shot.get("camera"), "medium shot"),
        *looks,
        shot.get("visual", ""),
        locs.get(shot["location"], {}).get("visual", ""),
        bible.get("style_prompt", ""),
        "vertical 9:16 composition",
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _shot_seed(slug: str, ep: int, sid: str) -> int:
    return int(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/{ep}/{sid}").int % (2**31 - 1))


def build_job(slug: str, eps: list[int], *, video: bool = False) -> dict:
    bible = paths.read_json(paths.bible_path(slug))
    refs = paths.refs_dir(slug)
    characters = []
    for c in bible["characters"]:
        ref = refs / f"{c['id']}.jpg"
        characters.append({
            "id": c["id"],
            "seed": c["seed"],
            "ref_prompt": f"character reference sheet, front view portrait, upper body, neutral expression, "
                          f"plain light grey background, {c['appearance']}, {bible.get('style_prompt', '')}",
            "ref_b64": base64.b64encode(ref.read_bytes()).decode() if ref.exists() else None,
        })
    shots, video_shots = [], []
    for ep in eps:
        episode = paths.read_json(paths.episode_path(slug, ep))
        shot_dir = paths.build_ep_dir(slug, ep) / "shots"
        for s in episode["shots"]:
            key = f"ep{ep:02d}_{s['id']}"
            prompt = shot_prompt(s, bible)
            if not (shot_dir / f"{s['id']}.png").exists():
                shots.append({"key": key, "prompt": prompt, "ref": (s["characters"] or [None])[0],
                              "seed": _shot_seed(slug, ep, s["id"])})
            if video and s.get("key") and not (shot_dir / f"{s['id']}.mp4").exists():
                video_shots.append({"key": key, "prompt": prompt + ", subtle natural motion, cinematic"})
    return {
        "job_id": uuid.uuid4().hex[:12],
        "slug": slug,
        "model": IMAGE_MODEL,
        "video_model": VIDEO_MODEL,
        "negative_prompt": bible.get("negative_prompt", ""),
        "width": 768, "height": 1344, "steps": 28, "cfg": 6.0, "ip_scale": 0.55,
        "time_budget_min": int(os.environ.get("SAGA_TIME_BUDGET_MIN", "480")),
        "characters": characters,
        "shots": shots,
        "video_shots": video_shots,
    }


# ── Kaggle ──────────────────────────────────────────────────

def _kaggle(*args: str, check: bool = True) -> str:
    r = subprocess.run(["kaggle", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args)} 失敗：{r.stderr or r.stdout}")
    return (r.stdout or "") + (r.stderr or "")


def _run_on_kaggle(job: dict, download_to: Path) -> dict:
    user = os.environ.get("KAGGLE_USERNAME")
    if not user:
        raise RuntimeError("缺少 KAGGLE_USERNAME / KAGGLE_KEY")
    kernel_id = f"{user}/{KERNEL_SLUG}"
    with tempfile.TemporaryDirectory() as tmp:
        payload = base64.b64encode(json.dumps(job, ensure_ascii=False).encode()).decode()
        code = KERNEL_TEMPLATE.read_text(encoding="utf-8").replace("__JOB_B64__", payload)
        Path(tmp, "kernel.py").write_text(code, encoding="utf-8")
        Path(tmp, "kernel-metadata.json").write_text(json.dumps({
            "id": kernel_id, "title": KERNEL_SLUG, "code_file": "kernel.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": True, "enable_internet": True,
            "dataset_sources": [], "competition_sources": [], "kernel_sources": [],
        }), encoding="utf-8")
        print(f"▶ 送出 Kaggle 算圖：{len(job['shots'])} 張圖、{len(job['video_shots'])} 段影片", flush=True)
        print(_kaggle("kernels", "push", "-p", tmp).strip(), flush=True)

    t0 = time.time()
    seen_active = False
    time.sleep(60)
    while True:
        status = _kaggle("kernels", "status", kernel_id, check=False).lower()
        mins = (time.time() - t0) / 60
        if any(w in status for w in ("running", "queued")):
            seen_active = True
        if "error" in status or "cancel" in status:
            _kaggle("kernels", "output", kernel_id, "-p", str(download_to), check=False)
            raise RuntimeError(f"Kaggle 執行失敗，log 在 {download_to}：{status.strip()}")
        if "complete" in status and (seen_active or mins > 10):
            break
        if mins > 11 * 60:
            raise RuntimeError("Kaggle 超過 11 小時未完成")
        print(f"  …Kaggle 執行中（{mins:.0f} 分）", flush=True)
        time.sleep(90)

    download_to.mkdir(parents=True, exist_ok=True)
    _kaggle("kernels", "output", kernel_id, "-p", str(download_to))
    manifests = list(download_to.rglob("manifest.json"))
    if not manifests:
        raise RuntimeError("Kaggle 輸出中找不到 manifest.json")
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    if manifest.get("job_id") != job["job_id"]:
        raise RuntimeError("下載到的是舊版本的輸出（job_id 不符），請重跑")
    return manifest


# ── 測試用：本機產生示意圖 ─────────────────────────────────────

def _find_cjk_font() -> str | None:
    r = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK TC:bold"], capture_output=True, text=True)
    return r.stdout.strip() or None


def _run_mock(job: dict, out: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    out.mkdir(parents=True, exist_ok=True)
    font_path = _find_cjk_font()
    font = ImageFont.truetype(font_path, 34) if font_path else ImageFont.load_default()
    manifest = {"job_id": job["job_id"], "refs": [], "images": [], "videos": [], "errors": [], "partial": False}
    for c in job["characters"]:
        if not c["ref_b64"]:
            img = Image.new("RGB", (448, 576), (60 + c["seed"] % 120, 80, 140))
            ImageDraw.Draw(img).text((20, 260), c["id"], fill="white", font=font)
            name = f"ref_{c['id']}.jpg"
            img.save(out / name, quality=90)
            manifest["refs"].append(name)
    for s in job["shots"]:
        hue = s["seed"] % 255
        img = Image.new("RGB", (job["width"], job["height"]))
        d = ImageDraw.Draw(img)
        for y in range(job["height"]):
            t = y / job["height"]
            d.line([(0, y), (job["width"], y)], fill=(int(hue * (1 - t)), int(60 + 100 * t), int(200 - hue * t * 0.5)))
        d.rectangle([60, 520, job["width"] - 60, 820], outline="white", width=4)
        d.text((90, 560), s["key"], fill="white", font=font)
        d.text((90, 620), f"ref: {s['ref']}", fill="white", font=font)
        d.text((90, 680), s["prompt"][:28], fill="white", font=font)
        img.save(out / f"{s['key']}.png")
        manifest["images"].append(f"{s['key']}.png")
    return manifest


def render(slug: str, eps: list[int], *, mock: bool = False, video: bool = False) -> dict:
    job = build_job(slug, eps, video=video)
    if not job["shots"] and not job["video_shots"]:
        print("▶ 算圖：全部鏡頭都已存在，略過", flush=True)
        return {"images": [], "videos": [], "errors": [], "partial": False}
    dl = paths.BUILD_DIR / slug / f"_kaggle_{job['job_id']}"
    manifest = _run_mock(job, dl) if mock else _run_on_kaggle(job, dl)

    def find(name: str) -> Path | None:
        return next(iter(dl.rglob(name)), None)

    refs = paths.refs_dir(slug)
    refs.mkdir(parents=True, exist_ok=True)
    for name in manifest.get("refs", []):
        f = find(name)
        if f:
            shutil.copy(f, refs / name.removeprefix("ref_"))
    for name in manifest.get("images", []) + manifest.get("videos", []):
        f = find(name)
        if not f:
            continue
        ep_tag, sid = Path(name).stem.split("_", 1)
        dest = paths.build_ep_dir(slug, int(ep_tag[2:])) / "shots"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest / f"{sid}{Path(name).suffix}")
    shutil.rmtree(dl, ignore_errors=True)
    for e in manifest.get("errors", []):
        print("  ⚠", e, flush=True)
    print(f"▶ 算圖完成：{len(manifest.get('images', []))} 張圖、{len(manifest.get('videos', []))} 段影片"
          + ("（時間不夠，部分未完成，再跑一次會接續）" if manifest.get("partial") else ""), flush=True)
    return manifest
