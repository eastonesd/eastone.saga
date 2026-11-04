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
MODELS_TEMPLATE = Path(__file__).with_name("kaggle_models.py")
VIDEO_MODEL = os.environ.get("SAGA_VIDEO_MODEL", "Wan-AI/Wan2.2-TI2V-5B-Diffusers")
KERNEL_SLUG = os.environ.get("SAGA_KERNEL_SLUG", "saga-render")
MODELS_VERSION = 1  # 改了 kaggle_models.py 的模型配方就加一，會自動重建模型包
MODELS_SLUG = f"saga-models-v{MODELS_VERSION}"
ACCELERATOR = os.environ.get("SAGA_ACCELERATOR", "NvidiaTeslaT4")  # Kaggle 的 T4 ×2

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
        "video_model": VIDEO_MODEL,
        "width": 768, "height": 1344, "steps": 8, "ip_scale": 0.6,
        "time_budget_min": int(os.environ.get("SAGA_TIME_BUDGET_MIN", "240")),
        "characters": characters,
        "shots": shots,
        "video_shots": video_shots,
    }


# ── Kaggle ──────────────────────────────────────────────────

def _kaggle_env() -> dict:
    env = dict(os.environ)
    key = env.get("KAGGLE_KEY", "")
    if key.startswith("KGAT_") and not env.get("KAGGLE_API_TOKEN"):
        env["KAGGLE_API_TOKEN"] = key  # 新版 token 格式
    return env


def _kaggle(*args: str, check: bool = True) -> str:
    r = subprocess.run(["kaggle", *args], capture_output=True, text=True, env=_kaggle_env())
    out = (r.stdout or "") + (r.stderr or "")
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args)} 失敗：{out[-1500:]}")
    return out


def _user() -> str:
    user = os.environ.get("KAGGLE_USERNAME")
    if not user:
        raise RuntimeError("缺少 KAGGLE_USERNAME（請到 GitHub Secrets 設定）")
    return user


def _push(slug: str, code: str, *, gpu: bool, sources: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "kernel.py").write_text(code, encoding="utf-8")
        Path(tmp, "kernel-metadata.json").write_text(json.dumps({
            "id": f"{_user()}/{slug}", "title": slug, "code_file": "kernel.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": gpu, "enable_internet": True,
            **({"machine_shape": ACCELERATOR} if gpu else {}),
            "dataset_sources": [], "competition_sources": [], "kernel_sources": sources,
        }), encoding="utf-8")
        args = ["kernels", "push", "-p", tmp] + (["--accelerator", ACCELERATOR] if gpu else [])
        out = _kaggle(*args).strip()
        if "push error" in out.lower():
            raise RuntimeError(f"Kaggle 送出失敗：{out[-800:]}")
        print("  " + out.splitlines()[-1], flush=True)


def _status(kernel_id: str) -> str:
    return _kaggle("kernels", "status", kernel_id, check=False).lower()


def _wait(kernel_id: str, label: str, max_min: int, require_active: bool = True) -> None:
    t0 = time.time()
    seen_active = not require_active
    time.sleep(30)
    last = ""
    while True:
        st = _status(kernel_id)
        mins = (time.time() - t0) / 60
        if any(w in st for w in ("running", "queued", "new_script", "pending")):
            seen_active = True
        if ("error" in st or "cancel" in st) and (seen_active or mins > 5):
            logs = _kaggle("kernels", "logs", kernel_id, check=False)
            raise RuntimeError(f"Kaggle {label} 失敗：{st.strip()}\n--- Kaggle 日誌結尾 ---\n{logs[-3000:]}")
        if "complete" in st and (seen_active or mins > 5):
            return
        if mins > max_min:
            raise RuntimeError(f"Kaggle {label} 超過 {max_min} 分鐘未完成")
        brief = st.strip().split('"')[-2] if '"' in st else st.strip()[-40:]
        if brief != last or int(mins) % 5 == 0:
            print(f"  …{label}：{brief}（{mins:.0f} 分）", flush=True)
            last = brief
        time.sleep(30)


def ensure_models() -> str:
    """確認 Kaggle 上的模型包存在；沒有就建一次（約 15～25 分鐘，之後永久沿用）。"""
    kid = f"{_user()}/{MODELS_SLUG}"
    st = _status(kid)
    if "complete" in st:
        return kid
    if any(w in st for w in ("running", "queued")):
        print("▶ 模型包建置中，等待完成…", flush=True)
    else:
        print("▶ 第一次使用：在 Kaggle 上建置模型包（只需一次）", flush=True)
        _push(MODELS_SLUG, MODELS_TEMPLATE.read_text(encoding="utf-8"), gpu=False, sources=[])
    _wait(kid, "模型包", 60)
    return kid


def _run_on_kaggle(job: dict, download_to: Path) -> dict:
    models = ensure_models()
    kernel_id = f"{_user()}/{KERNEL_SLUG}"
    payload = base64.b64encode(json.dumps(job, ensure_ascii=False).encode()).decode()
    code = KERNEL_TEMPLATE.read_text(encoding="utf-8").replace("__JOB_B64__", payload)
    print(f"▶ 送出 Kaggle 算圖：{len(job['shots'])} 張圖、{len(job['video_shots'])} 段影片（{ACCELERATOR}）", flush=True)
    t0 = time.time()
    _push(KERNEL_SLUG, code, gpu=True, sources=[models])
    _wait(kernel_id, "算圖", job["time_budget_min"] + 60)
    print(f"  Kaggle 完成，耗時 {(time.time() - t0) / 60:.1f} 分鐘", flush=True)

    download_to.mkdir(parents=True, exist_ok=True)
    _kaggle("kernels", "output", kernel_id, "-p", str(download_to), "-o")
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
