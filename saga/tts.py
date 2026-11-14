"""IndexTTS-2.5 配音（在 Kaggle GPU 上跑）＋ AISHELL-3 聲線庫。"""
from __future__ import annotations

import base64
import json
import os
import shutil
import uuid
from pathlib import Path



KERNEL_TEMPLATE = Path(__file__).with_name("kaggle_voice.py")
VOICE_SLUG = os.environ.get("SAGA_VOICE_SLUG", "saga-voice")

# 8 維情緒向量：[開心, 憤怒, 悲傷, 害怕, 厭惡, 憂鬱, 驚訝, 平靜]
EMO = {
    "calm": [0, 0, 0, 0, 0, 0, 0, 0.8],
    "angry": [0, 0.85, 0, 0, 0.1, 0, 0, 0],
    "sad": [0, 0, 0.8, 0, 0, 0.2, 0, 0],
    "happy": [0.8, 0, 0, 0, 0, 0, 0, 0],
    "afraid": [0, 0, 0, 0.8, 0, 0, 0.1, 0],
    "surprised": [0, 0, 0, 0, 0, 0, 0.85, 0],
    "disdain": [0, 0.3, 0, 0, 0.6, 0, 0, 0],
}

# AISHELL-3 候選聲線（spk-info：B=14–25 歲、C=26–40 歲、D=41 歲以上）
CANDIDATES = [
    ("m01", "SSB0710", "成熟男聲", "26–40 歲"), ("m02", "SSB0261", "成熟男聲", "26–40 歲"),
    ("m03", "SSB0407", "成熟男聲", "26–40 歲"), ("m04", "SSB1630", "成熟男聲", "26–40 歲"),
    ("m05", "SSB0590", "成熟男聲", "26–40 歲"), ("m06", "SSB0784", "成熟男聲", "26–40 歲"),
    ("m07", "SSB0434", "中年男聲", "41 歲以上"),
    ("m08", "SSB1365", "青年男聲", "14–25 歲"), ("m09", "SSB0273", "青年男聲", "14–25 歲"),
    ("m10", "SSB1863", "青年男聲", "14–25 歲"), ("m11", "SSB0073", "青年男聲", "14–25 歲"),
    ("f01", "SSB0534", "成熟女聲", "26–40 歲"), ("f02", "SSB0993", "成熟女聲", "26–40 歲"),
    ("f03", "SSB0666", "成熟女聲", "26–40 歲"), ("f04", "SSB1781", "成熟女聲", "26–40 歲"),
    ("f05", "SSB1837", "青年女聲", "14–25 歲"), ("f06", "SSB0578", "青年女聲", "14–25 歲"),
    ("f07", "SSB0016", "青年女聲", "14–25 歲"), ("f08", "SSB1110", "中年女聲", "41 歲以上"),
]


def run_job(job: dict, dl: Path) -> dict:
    from . import render

    job["hf_token"] = os.environ.get("HF_TOKEN") or None
    payload = base64.b64encode(json.dumps(job, ensure_ascii=False).encode()).decode()
    code = KERNEL_TEMPLATE.read_text(encoding="utf-8").replace("__JOB_B64__", payload)
    kid = f"{render._user()}/{VOICE_SLUG}"
    print(f"▶ 送出 Kaggle 配音：{len(job['lines'])} 句", flush=True)
    render._push(VOICE_SLUG, code, gpu=True, sources=[])
    render._wait(kid, "配音", 120)
    dl.mkdir(parents=True, exist_ok=True)
    render._kaggle("kernels", "output", kid, "-p", str(dl), "-o")
    m = json.loads(next(dl.rglob("manifest.json")).read_text(encoding="utf-8"))
    if m.get("job_id") != job["job_id"]:
        raise RuntimeError("下載到舊的配音輸出（job_id 不符）")
    for e in m.get("errors", [])[:10]:
        print("  ⚠", e, flush=True)
    print(f"  ✓ {len(m.get('done', []))} 句完成；{m.get('speed', '')}", flush=True)
    return m


# ── 正式生產 ───────────────────────────────────────────────────

VOICE_DIR = Path(__file__).resolve().parent.parent / "assets" / "voices"
VOICE_BASE_URL = os.environ.get(
    "SAGA_VOICE_BASE_URL",
    f"https://raw.githubusercontent.com/{os.environ.get('GITHUB_REPOSITORY', 'eastonesd/saga.ai')}/main/assets/voices")
_META = {vid: (kind, age) for vid, _, kind, age in CANDIDATES}


def available_voices() -> list[str]:
    return sorted(p.stem for p in VOICE_DIR.glob("*.wav"))


def auto_cast(bible: dict) -> dict:
    """依角色性別／年齡從聲線庫自動分配（盡量不重複），旁白用中年男聲或成熟女聲。"""
    have = available_voices()
    used: set[str] = set()

    def pick(prefer: list[str]) -> str:
        for v in prefer + have:
            if v in have and v not in used:
                used.add(v)
                return v
        return have[0]

    cast = {}
    for c in bible["characters"]:
        female = str(c.get("gender", "")).lower().startswith("f")
        age = int(c.get("age") or 28)
        want = "中年" if age >= 41 else "成熟" if age >= 26 else "青年"
        sex = "f" if female else "m"
        pref = [v for v in have if v.startswith(sex) and want in _META.get(v, ("", ""))[0]]
        pref += [v for v in have if v.startswith(sex)]  # 同性別的其他聲線當備選
        cast[c["id"]] = pick(pref)
    cast["narrator"] = pick(["m07", "f03", "m01"])
    return cast


def synthesize(items: list[dict], dl: Path) -> dict[str, Path]:
    """items：[{key, voice(聲線庫 id), text, emo(EMO 的 key 或 None), speed}] → {key: wav 路徑}"""
    if not items:
        return {}
    if os.environ.get("MODAL_TOKEN_ID"):
        try:
            from . import modal_tts
            lines = [{"key": it["key"], "voice": it["voice"], "text": it["text"],
                      "emo": EMO.get(it.get("emo") or ""), "speed": it.get("speed")} for it in items]
            got = modal_tts.synthesize(lines, {v: VOICE_DIR / f"{v}.wav" for v in {it["voice"] for it in items}},
                                       dl / "modal")
            missing = [it for it in items if it["key"] not in got]
            print(f"  ✓ Modal 完成 {len(got)}／{len(items)} 句", flush=True)
            if not missing:
                return got
            print(f"  … 剩 {len(missing)} 句改交 Kaggle", flush=True)
            got.update(synthesize_kaggle(missing, dl / "kaggle"))
            return got
        except Exception as e:  # Modal 出狀況 → 改用 Kaggle
            print(f"  ⚠ Modal 配音失敗，改用 Kaggle：{str(e)[:300]}", flush=True)
    return synthesize_kaggle(items, dl)


def synthesize_kaggle(items: list[dict], dl: Path) -> dict[str, Path]:
    if not items:
        return {}
    voices = sorted({it["voice"] for it in items})
    job = {
        "job_id": uuid.uuid4().hex[:12],
        # 參考音檔從公開 repo 下載（直接塞進程式碼會超過 Kaggle 的大小上限）
        "voices": {v: {"ref_url": f"{VOICE_BASE_URL}/{v}.wav"} for v in voices},
        "lines": [{"key": it["key"], "voice": it["voice"], "text": it["text"],
                   "emo": EMO.get(it.get("emo") or ""), "speed": it.get("speed")} for it in items],
        "workers_per_gpu": int(os.environ.get("SAGA_TTS_WORKERS_PER_GPU", "1")),
    }
    shutil.rmtree(dl, ignore_errors=True)
    run_job(job, dl)
    return {p.stem: p for p in dl.rglob("*.wav") if not p.name.startswith("ref_")}
