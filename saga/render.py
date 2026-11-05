"""美術部：把分鏡變成算圖工作單，送到 Kaggle 免費 GPU，跑完把圖抓回來。"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from . import paths

KERNEL_TEMPLATE = Path(__file__).with_name("kaggle_kernel.py")
VIDEO_MODEL = os.environ.get("SAGA_VIDEO_MODEL", "Wan-AI/Wan2.2-TI2V-5B-Diffusers")
KERNEL_SLUG = os.environ.get("SAGA_KERNEL_SLUG", "saga-render")
ACCELERATOR = os.environ.get("SAGA_ACCELERATOR", "NvidiaTeslaT4")  # Kaggle 的 T4 ×2

CAMERA = {
    "close-up": "close-up shot, face focus",
    "medium": "medium shot, waist up",
    "wide": "wide shot, full body, establishing",
    "over-shoulder": "over-the-shoulder shot",
}


def _short(text: str, words: int) -> str:
    """SDXL 的文字編碼器只讀前 77 個 token：每段限制字數，重要的放前面。"""
    return " ".join(str(text or "").replace("\n", " ").split()[:words]).rstrip(",")


# 畫風預設：提示詞前綴＋（選用）畫風 LoRA。bible.render_style 指定；沒指定就依 style_choice 判斷
STYLES = {
    "donghua3d": {
        "prompt": "3d chinese donghua CG animation, semi-realistic anime, dramatic rim light, dark moody cinematic lighting",
        "ref": "3d chinese donghua CG character portrait, semi-realistic anime, detailed face, rim light",
        "lora": {"repo": "MarkBW/2.5Dstyle-xl", "file": "2.5DRealistic.safetensors", "scale": 0.6},  # 實測 0.6 最佳
    },
    "realistic": {
        "prompt": "cinematic film still, realistic photo, dramatic lighting",
        "ref": "portrait photo, realistic",
        "lora": None,
    },
}


def style_of(bible: dict) -> dict:
    key = bible.get("render_style")
    if key not in STYLES:
        key = "donghua3d" if re.search(r"國漫|3D|動漫|卡通|漫", str(bible.get("style_choice", ""))) else "realistic"
    return STYLES[key]


def shot_prompt(shot: dict, bible: dict) -> str:
    chars = {c["id"]: c for c in bible["characters"]}
    locs = {l["id"]: l for l in bible["locations"]}
    ids = [c for c in shot["characters"] if c in chars]
    look = lambda c, n: _short(chars[c].get("key_features") or chars[c]["appearance"], n)  # noqa: E731
    if len(ids) >= 2:  # 雙人：明確左右站位，避免兩人特徵混在一起
        who = f"exactly two people, on the left {look(ids[0], 11)}, on the right {look(ids[1], 11)}"
    elif ids:
        who = f"solo, one person only, {look(ids[0], 17)}"
    else:
        who = ""
    parts = [
        CAMERA.get(shot.get("camera"), "medium shot"),
        _short(style_of(bible)["prompt"], 12),
        who,
        _short(shot.get("visual", ""), 14),
        _short(locs.get(shot["location"], {}).get("visual", ""), 8),
    ]
    return ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))


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
            "ref_prompt": f"{style_of(bible)['ref']}, single person, head and shoulders, facing camera, "
                          f"{_short(c['appearance'], 24)}, neutral background",
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
                shots.append({"key": key, "prompt": prompt, "refs": s["characters"][:2],
                              "seed": _shot_seed(slug, ep, s["id"])})
            if video and s.get("key") and not (shot_dir / f"{s['id']}.mp4").exists():
                video_shots.append({"key": key, "prompt": prompt + ", subtle natural motion, cinematic"})
    return {
        "job_id": uuid.uuid4().hex[:12],
        "slug": slug,
        "video_model": VIDEO_MODEL,
        "width": 1344, "height": 768, "steps": 8, "ip_scale": 0.55,   # 16:9 橫式
        "style_lora": style_of(bible)["lora"],
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


def _kaggle_error_tail(kernel_id: str, n: int = 25) -> str:
    raw = _kaggle("kernels", "logs", kernel_id, check=False)
    lines = []
    try:
        for item in json.loads(raw[raw.index("["):]):
            lines.extend(str(item.get("data", "")).splitlines())
    except (ValueError, TypeError):
        lines = raw.splitlines()
    keep = [l for l in lines if re.search(r"Error|Exception|Traceback|⚠|exit|File \"/tmp/saga", l)]
    return "\n".join((keep or lines)[-n:])


def _run_on_kaggle(job: dict, download_to: Path) -> dict:
    kernel_id = f"{_user()}/{KERNEL_SLUG}"
    payload = base64.b64encode(json.dumps(job, ensure_ascii=False).encode()).decode()
    code = KERNEL_TEMPLATE.read_text(encoding="utf-8").replace("__JOB_B64__", payload)
    print(f"▶ 送出 Kaggle 算圖：{len(job['shots'])} 張圖、{len(job['video_shots'])} 段影片（{ACCELERATOR}）", flush=True)
    t0 = time.time()
    _push(KERNEL_SLUG, code, gpu=True, sources=[])
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
        d.text((90, 620), f"refs: {s.get('refs')}", fill="white", font=font)
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
    if job["shots"] and not manifest.get("images") and not mock:
        # 一張都沒有＝程式壞了，不是時間不夠：把 Kaggle 日誌中的錯誤印出來並停止接力
        raise RuntimeError("Kaggle 一張圖都沒產出。錯誤摘要：\n" + _kaggle_error_tail(f"{_user()}/{KERNEL_SLUG}"))
    print(f"▶ 算圖完成：{len(manifest.get('images', []))} 張圖、{len(manifest.get('videos', []))} 段影片"
          + ("（時間不夠，部分未完成，再跑一次會接續）" if manifest.get("partial") else ""), flush=True)
    return manifest
