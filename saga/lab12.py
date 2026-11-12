"""12 秒短劇品質試拍（lab12-performance）：同一段原創分鏡，A＝現行產線、B＝改良產線，並排比較。

依《颯枷執行單｜12 秒短劇品質試拍》分四段，每段一個 GitHub Actions 工作（lab12.yml）：
  stills    Kaggle：共用定妝照 → A（Lightning 8 步）與 B（完整 SDXL 28 步、CFG 5）各三鏡 ＋ 驗收參考圖 → 匿名對照圖
  voice     Modal：IndexTTS-2.5 兩條旁白（現行設定／表演版）→ 響度對齊 → 隨機標成 X、Y
  video     Kaggle：Wan 2.2 TI2V 5B，用選定首幀生成動作（可只跑指定鏡頭，例如先驗鏡 01）
  assemble  FFmpeg：A 版（靜幀運鏡＋現行旁白）、B 版（動作素材＋表演版旁白）、純人聲版
所有成品放在 GitHub Release「lab12」，不碰正式劇集、排程與上架。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path

from . import paths, render

SLUG = "lab12-performance"
TAG = "lab12"
OUT = paths.BUILD_DIR / SLUG
REPO = os.environ.get("GITHUB_REPOSITORY", "eastonesd/eastone.saga")
W, H, FPS = 1920, 1080, 30

GIRL = "young East Asian woman in her early twenties, shoulder-length black hair with side bangs, white collared blouse, dark navy ribbon bow tie"
BOY = "young East Asian man in his early twenties, short black hair, light grey cardigan over white shirt"
STYLE = "cinematic film still, photorealistic, natural skin texture"
NEG = ("cartoon, anime, 3d render, doll, plastic skin, text, watermark, logo, extra fingers, fused fingers, "
       "deformed hands, extra limbs, duplicate person, lowres, blurry, oversaturated")

# 三鏡：秒數為試拍規格；first＝動畫首幀（動作起點）；motion＝給 Wan 的動作順序
SHOTS = [
    {"n": 1, "name": "教室・抬眼微笑", "sec": 2.8, "refs": ["girl"], "pan": "zoom_in",
     "first": f"{STYLE}, young woman, shoulder-length black hair, white blouse, navy bow tie, sitting at a classroom desk, "
              "head lowered looking down at the desk, warm sunlight from window on the left, medium close-up, shallow depth of field",
     "motion": "A young woman in a white blouse and a navy ribbon bow tie sits at a classroom desk in warm window light from the left. "
               "She first looks down at the desk, then slowly raises her eyes toward the person across from her, and finally gives a "
               "small gentle smile. Static camera, her face stays consistent, realistic subtle motion."},
    {"n": 2, "name": "門邊・碰手鬆開", "sec": 4.4, "refs": ["boy", "girl"], "pan": "pan_right",
     "first": f"{STYLE}, classroom doorway, young man standing on the left side, young woman on the right side near the open door "
              "turning toward the door, white blouse, navy bow tie, grey cardigan, warm afternoon light, medium wide shot",
     "motion": "At a classroom doorway, the young woman on the right turns and starts walking out through the door. The young man on "
               "the left reaches out his right hand; their hands touch briefly, then let go. She walks out of the door, and his hand "
               "stays hanging in the air for a moment. Static camera, the man stays on the left and the woman on the right until she "
               "leaves, realistic hands."},
    {"n": 3, "name": "書桌・拿起文件", "sec": 4.8, "refs": ["boy"], "pan": "zoom_in",
     "first": f"{STYLE}, young man, short black hair, grey cardigan, sitting at a wooden desk at night, warm desk lamp on the right, "
              "a single sheet of folded letter paper lying on the desk near his right hand, medium shot",
     "motion": "A young man sits at a wooden desk under a warm desk lamp on the right. With his right hand he picks up a single sheet of "
               "paper from the desk, his eyes follow the paper as he reads it, then he puts the same sheet back down on the desk. "
               "Static camera, steady lamp light, realistic hand and paper."},
]
EXTRA = [  # 驗收用參考圖（不進影片）：半身服裝、場景光線
    {"key": "epR_s1", "label": "女生半身服裝", "refs": ["girl"],
     "prompt": f"{STYLE}, young woman, waist-up portrait, {GIRL}, standing in a classroom, soft window light"},
    {"key": "epR_s2", "label": "男生半身服裝", "refs": ["boy"],
     "prompt": f"{STYLE}, young man, waist-up portrait, {BOY}, standing in a classroom, soft window light"},
    {"key": "epR_s3", "label": "教室光線", "refs": [],
     "prompt": f"{STYLE}, empty classroom, wooden desks, warm late-afternoon sunlight streaming through windows on the left, no people"},
]
# 第 2 輪首幀：第 1 輪兩組都「正面盯鏡頭」（IP-Adapter 臉部參考＋定妝照正面），動作起點不成立 →
# 臉部參考權重 0.55→0.35、提示詞把姿勢放最前面、B 組負面加入看鏡頭
FIRST2 = {
    1: f"young woman looking down at an open notebook on her desk, head lowered, eyes downcast, three-quarter view, {STYLE}, "
       "shoulder-length black hair, white blouse, navy bow tie, classroom, warm golden sunlight from window on the left",
    2: f"side view at a classroom doorway, young woman on the right turning away toward the open door, young man on the left "
       f"lifting his right hand toward her, {STYLE}, white blouse, navy bow tie, light grey cardigan, warm afternoon light, medium wide shot",
    3: f"young man sitting at a wooden desk at night looking down at a single sheet of paper lying flat on the desk, three-quarter view, "
       f"{STYLE}, grey cardigan, warm desk lamp on the right side of the desk, dark room, medium shot",
}
NOSTARE = ", looking at viewer, eye contact, facing camera, posing, symmetrical portrait"
CONFIGS = {  # 兩套生產設定
    "A": {"label": "現行 Lightning 8 步", "unet": "lightning", "steps": 8, "cfg": 0, "sampler": "Euler（trailing）"},
    "B": {"label": "完整 SDXL 28 步", "unet": "sdxl", "steps": 28, "cfg": 5, "neg": NEG, "sampler": "DPM++ 2M Karras"},
}

# 旁白：X/Y 揭曉前不顯示哪條是哪條
NARRATOR = "m01"  # 同一位成熟男聲（AISHELL-3 SSB0710）
LINES = ["那天，她回頭對我笑了。", "我以為，她還會回來。", "後來，我才看懂那封信。"]
# 表演版：句子拆分、情緒向量 [開心, 憤怒, 悲傷, 害怕, 厭惡, 憂鬱, 驚訝, 平靜]、句後停頓、起點（秒）
PERF = [
    {"text": "那天，她回頭對我笑了。", "emo": [0.25, 0, 0.1, 0, 0, 0.25, 0, 0.4], "at": 0.35, "note": "回憶：溫柔帶一點惆悵"},
    {"text": "我以為，她還會回來。", "emo": [0.3, 0, 0.1, 0, 0, 0.1, 0.05, 0.45], "at": 4.2, "note": "仍有期待；尾字延伸到鬆手之後"},
    {"text": "後來，", "emo": [0, 0, 0.35, 0, 0, 0.45, 0, 0.2], "at": 7.45, "note": "收低，之後停頓"},
    {"text": "我才看懂那封信。", "emo": [0, 0, 0.6, 0, 0, 0.3, 0, 0.1], "at": None, "gap": 0.5, "note": "失落；「那封信」落在拿起紙"},
]
PERF_TEMPO = 0.93          # 表演版整體放慢（後製 atempo，音高不變）
PLAIN_AT = [0.35, 3.75, 7.6]


def _sh(*a: str) -> str:
    r = subprocess.run(list(a), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{a[0]} 失敗：{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def _ff(*a: str) -> None:
    _sh("ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *a)


def _dur(p: Path) -> float:
    return float(_sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)).strip())


# ── Release 存取（成品集中在 lab12 這個 tag） ──────────────────────
def _release_ready() -> None:
    if subprocess.run(["gh", "release", "view", TAG, "-R", REPO], capture_output=True).returncode != 0:
        _sh("gh", "release", "create", TAG, "-R", REPO, "--prerelease", "--title", "12 秒品質試拍（lab12）",
            "--notes", "試拍成品，不是正式劇集。")


def upload(*files: Path) -> None:
    _release_ready()
    _sh("gh", "release", "upload", TAG, "-R", REPO, "--clobber", *[str(f) for f in files if f.exists()])


def fetch(*names: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for n in names:
        subprocess.run(["gh", "release", "download", TAG, "-R", REPO, "-p", n, "-D", str(OUT), "--clobber"],
                       capture_output=True)


def asset_url(name: str) -> str:
    return f"https://github.com/{REPO}/releases/download/{TAG}/{name}"


def _seed(name: str) -> int:
    return int(uuid.uuid5(uuid.NAMESPACE_URL, f"{SLUG}/{name}").int % (2**31 - 1))


def _load_report() -> dict:
    fetch("lab12-report.json")
    p = OUT / "lab12-report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_report(rep: dict) -> Path:
    p = OUT / "lab12-report.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    upload(p)
    return p


def _commit_sha() -> str:
    return os.environ.get("GITHUB_SHA") or _sh("git", "rev-parse", "HEAD").strip()


# ── 1. 靜幀 ───────────────────────────────────────────────────
def stills(rnd: int = 1) -> None:
    from PIL import Image, ImageDraw

    chars = []
    for cid, look in (("girl", GIRL), ("boy", BOY)):  # 兩組共用同一份定妝照（以 B 的品質設定生成一次）
        chars.append({"id": cid, "seed": _seed(cid), "ref_url": asset_url(f"lab12-ref_{cid}.jpg") if rnd > 1 else None, "ref_b64": None,
                      "render": {k: CONFIGS["B"][k] for k in ("unet", "steps", "cfg", "neg")},
                      "ref_prompt": f"{STYLE}, single person, head and shoulders, facing camera, {look}, neutral grey background"})
    shots = []
    for g, cfg in CONFIGS.items():
        rk = {k: cfg[k] for k in ("unet", "steps", "cfg", "neg") if k in cfg}
        if rnd >= 2 and "neg" in rk:
            rk["neg"] = rk["neg"] + NOSTARE
        for s in SHOTS:
            shots.append({"key": f"ep{g}_s{s['n']}", "prompt": FIRST2[s["n"]] if rnd >= 2 else s["first"], "refs": s["refs"],
                          "seed": _seed(f"shot{s['n']}"), "ip": 0.35 if rnd >= 2 else 0.55, "loras": {}, **rk})
    for e in (EXTRA if rnd == 1 else []):
        shots.append({"key": e["key"], "prompt": e["prompt"], "refs": e["refs"], "seed": _seed(e["key"]), "ip": 0.5,
                      "loras": {}, **{k: CONFIGS["B"][k] for k in ("unet", "steps", "cfg", "neg")}})
    job = {"job_id": uuid.uuid4().hex[:12], "slug": SLUG, "video_model": render.VIDEO_MODEL,
           "width": 1344, "height": 768, "steps": 8, "ip_scale": 0.55, "time_budget_min": 120,
           "characters": chars, "shots": shots, "video_shots": [], "loras": {}}
    dl = OUT / "_stills"
    shutil.rmtree(dl, ignore_errors=True)
    manifest = render._run_on_kaggle(job, dl)
    times = {}
    for f in dl.rglob("timing_shots_*.json"):
        times.update(json.loads(f.read_text()))

    # 對照圖：兩欄三列，隱去模型名；左右隨機
    order = ["A", "B"]
    random.Random(job["job_id"]).shuffle(order)
    tw, th = 960, 549
    sheet = Image.new("RGB", (2 * tw + 30, 70 + 3 * (th + 20)), (16, 16, 20))
    d = ImageDraw.Draw(sheet)
    font = _font(34)
    for col, g in enumerate(order):
        d.text((col * (tw + 30) + 12, 16), f"第 {'一二'[col]} 欄", fill="white", font=font)
        for i, s in enumerate(SHOTS):
            f = next(iter(dl.rglob(f"ep{g}_s{s['n']}.png")), None)
            if f:
                sheet.paste(Image.open(f).convert("RGB").resize((tw, th)), (col * (tw + 30), 70 + i * (th + 20)))
    pre = "" if rnd == 1 else f"r{rnd}-"
    files = [OUT / f"lab12-{pre}stills-sheet.jpg"]
    sheet.save(files[0], quality=92)
    for f in list(dl.rglob("ep*_s*.png")) + ([] if rnd > 1 else list(dl.rglob("ref_*.jpg"))):
        dst = OUT / f"lab12-{pre}{f.name}"
        shutil.copy(f, dst)
        files.append(dst)
    upload(*files)
    rep = _load_report()
    rep.setdefault("commit", _commit_sha())
    rep.update({f"stills{'' if rnd == 1 else f'_r{rnd}'}": {"ip": 0.35 if rnd >= 2 else 0.55,
        "job_id": job["job_id"], "kaggle_min": manifest.get("elapsed_min"), "gpus": manifest.get("gpus"),
        "errors": manifest.get("errors"), "sheet_order": order,
        "configs": {g: {k: v for k, v in c.items() if k != "neg"} | {"neg": bool(c.get("neg"))} for g, c in CONFIGS.items()},
        "seeds": {f"shot{s['n']}": _seed(f"shot{s['n']}") for s in SHOTS},
        "sec_per_image": times,
        "size": "1344×768", "shared_refs": True}})
    _save_report(rep)
    print(f"▶ 靜幀完成：{len(manifest.get('images', []))} 張；Kaggle {manifest.get('elapsed_min')} 分鐘；對照圖欄位順序 {order}")


def _font(size: int):
    from PIL import ImageFont
    r = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK TC:bold"], capture_output=True, text=True)
    try:
        return ImageFont.truetype(r.stdout.strip(), size)
    except OSError:
        return ImageFont.load_default()


# ── 2. 旁白 X / Y ─────────────────────────────────────────────
def voice() -> None:
    from . import tts

    items = [{"key": f"plain_{i}", "voice": NARRATOR, "text": t, "emo": None} for i, t in enumerate(LINES)]
    tts.EMO.update({f"perf_{i}": p["emo"] for i, p in enumerate(PERF)})  # 自訂情緒向量
    items += [{"key": f"perf_{i}", "voice": NARRATOR, "text": p["text"], "emo": f"perf_{i}"} for i, p in enumerate(PERF)]
    dl = OUT / "_tts"
    shutil.rmtree(dl, ignore_errors=True)
    wavs = tts.synthesize(items, dl)
    engine = "IndexTTS-2.5（Modal L4）" if os.environ.get("MODAL_TOKEN_ID") else "IndexTTS-2.5（Kaggle）"
    missing = [it["key"] for it in items if it["key"] not in wavs]
    if missing:
        raise RuntimeError(f"有句子沒有合成成功：{missing}（本輪不以 Edge 補錄，避免混入不同引擎）")

    def trim(src: Path, dst: Path, tempo: float = 1.0) -> Path:
        af = ("silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
              "silenceremove=start_periods=1:start_threshold=-50dB,areverse")
        if tempo != 1.0:
            af += f",atempo={tempo}"
        _ff("-i", str(src), "-af", af, "-ar", "48000", "-ac", "1", str(dst))
        return dst

    def lay(parts: list[tuple[Path, float]], dst: Path) -> list[dict]:
        """把句子放到時間軸上（秒），整條做響度對齊（-16 LUFS）"""
        ins, flt, cues = [], [], []
        for k, (f, at) in enumerate(parts):
            ins += ["-i", str(f)]
            flt.append(f"[{k}:a]adelay={int(at * 1000)}:all=1[a{k}]")
            cues.append({"start": round(at, 2), "end": round(at + _dur(f), 2)})
        mix = "".join(f"[a{k}]" for k in range(len(parts)))
        _ff(*ins, "-filter_complex", ";".join(flt) + f";{mix}amix=inputs={len(parts)}:normalize=0,"
            f"apad=whole_dur=12,atrim=0:12,loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", str(dst))
        return cues

    t = OUT / "_trim"
    t.mkdir(parents=True, exist_ok=True)
    plain = [(trim(wavs[f"plain_{i}"], t / f"plain_{i}.wav"), PLAIN_AT[i]) for i in range(3)]
    perf_f = [trim(wavs[f"perf_{i}"], t / f"perf_{i}.wav", PERF_TEMPO) for i in range(len(PERF))]
    perf, at = [], 0.0
    for i, p in enumerate(PERF):
        at = p["at"] if p.get("at") is not None else at + _dur(perf_f[i - 1]) + p.get("gap", 0.5)
        perf.append((perf_f[i], at))
    # 「那封信」要落在拿起紙（第三鏡約 9.6～10.6 秒）：最後一句若太早結束就往後推
    last_end = perf[-1][1] + _dur(perf[-1][0])
    if last_end < 11.0:
        push = min(11.0 - last_end, 0.6)
        perf[-1] = (perf[-1][0], perf[-1][1] + push)
    elif last_end > 11.8:   # 不可超出 12 秒：最後兩段一起往前移（「後來」最早 7.25 秒）
        shift = min(last_end - 11.8, perf[-2][1] - 7.25)
        perf[-2], perf[-1] = (perf[-2][0], perf[-2][1] - shift), (perf[-1][0], perf[-1][1] - shift)
    cue_plain = lay(plain, OUT / "_voice_plain.wav")
    cue_perf = lay(perf, OUT / "_voice_perf.wav")
    perf_text = [p["text"] for p in PERF]

    labels = ["plain", "perf"]
    random.Random(uuid.uuid4().hex).shuffle(labels)
    xy = dict(zip(("X", "Y"), labels))
    files = []
    for k, which in xy.items():
        f = OUT / f"lab12-voice-{k}.m4a"
        _ff("-i", str(OUT / f"_voice_{which}.wav"), "-c:a", "aac", "-b:a", "192k", str(f))
        files.append(f)
    # 合成用的原始檔（檔名中性，不在試聽前打開即可）
    for which in ("plain", "perf"):
        f = OUT / f"lab12-voice-src-{which}.wav"
        shutil.copy(OUT / f"_voice_{which}.wav", f)
        files.append(f)
    upload(*files)
    rep = _load_report()
    rep["voice"] = {"engine": engine, "narrator": NARRATOR, "edge_fallback": [],
                    "plain": {"setting": "現行逐句情緒（旁白三句皆無標記 → 不帶情緒向量）、原速、句間不另設停頓",
                              "cues": [dict(c, text=LINES[i]) for i, c in enumerate(cue_plain)]},
                    "perf": {"setting": f"逐句情緒向量＋整體 {PERF_TEMPO}× 放慢（後製 atempo）＋「後來，」後停頓 "
                                        f"{PERF[3]['gap']} 秒；語速參數未直接送進推論端（IndexTTS-2.5 介面不保證支援），改由後製控制",
                             "detail": [{k: p[k] for k in ("text", "emo", "note")} for p in PERF],
                             "cues": [dict(c, text=perf_text[i]) for i, c in enumerate(cue_perf)]},
                    "xy_answer": xy, "loudness": "整段 -16 LUFS、True Peak -1.5 dB，無背景音樂"}
    _save_report(rep)
    print("▶ 旁白完成：X、Y 已上傳（揭曉對照存在報告 voice.xy_answer）")


# ── 3. 動作素材 ───────────────────────────────────────────────
def _frame(v: str, n: int) -> str:
    """pick 值 "B" 或 "B2"（第 2 輪 B 組）→ release 檔名"""
    g, r = v[0], v[1:]
    return f"lab12-{'' if r in ('', '1') else f'r{r}-'}ep{g}_s{n}.png"


def video(pick: str, only: str) -> None:
    """pick：每鏡選用的首幀，例如 "1:B,2:A,3:B"；only：要生成的鏡頭，例如 "1" 或 "1,2,3"。"""
    choice = dict(x.split(":") for x in pick.split(","))
    want = [int(x) for x in only.split(",")]
    vs = []
    for s in SHOTS:
        if s["n"] not in want:
            continue
        g = choice.get(str(s["n"]), "B")
        frames = int(round(s["sec"] * 24 / 4)) * 4 + 1   # Wan 要 4n+1 格，24 fps
        vs.append({"key": f"epV_s{s['n']}", "prompt": s["motion"] + " Locked-off tripod shot, the camera does not move, no new people enter the frame.", "image_url": asset_url(_frame(g, s["n"])),
                   "w": 832, "h": 480, "frames": frames, "fps": 24, "steps": 30, "cfg": 5.0, "seed": _seed(f"video{s['n']}")})
    job = {"job_id": uuid.uuid4().hex[:12], "slug": SLUG, "video_model": render.VIDEO_MODEL, "width": 1344, "height": 768,
           "time_budget_min": 300, "characters": [], "shots": [], "video_shots": vs}
    dl = OUT / "_video"
    shutil.rmtree(dl, ignore_errors=True)
    err = None
    try:
        manifest = render._run_on_kaggle(job, dl)
    except Exception as e:  # 失敗也要把設定與錯誤記下來
        manifest, err = {}, str(e)[-1500:]
    info = {}
    for f in dl.rglob("video_info_*.json"):
        info.update(json.loads(f.read_text(encoding="utf-8")))
    files = []
    for f in dl.rglob("epV_s*.mp4"):
        dst = OUT / f"lab12-{f.name}"
        shutil.copy(f, dst)
        files.append(dst)
    if files:
        upload(*files)
    tail = ""
    if True:   # 一律記下 Kaggle 日誌重點（步數、顯存峰值、錯誤）
        try:
            tail = render._kaggle_error_tail(f"{os.environ.get('KAGGLE_USERNAME', '')}/{os.environ.get('SAGA_KERNEL_SLUG', 'saga-lab12')}", 40)
        except Exception as e:  # noqa: BLE001
            tail = f"取不到日誌：{e}"
        print("▶ Kaggle 日誌重點：\n" + tail)
        err = err or (f"{len(vs) - len(files)} 段沒有產出影片" if len(files) < len(vs) else None)
    rep = _load_report()
    runs = rep.setdefault("video_runs", [])
    runs.append({"job_id": job["job_id"], "pick": choice, "shots": want, "model": render.VIDEO_MODEL,
                 "kaggle_min": manifest.get("elapsed_min"), "info": info, "error": err, "log_tail": tail[-3000:],
                 "settings": {v["key"]: {k: v[k] for k in ("w", "h", "frames", "fps", "steps", "cfg", "seed")} for v in vs}})
    rep.setdefault("video_pick", {}).update(choice)
    _save_report(rep)
    if err:
        raise RuntimeError(err)
    print(f"▶ 動作素材：{len(files)} 段；{json.dumps(info, ensure_ascii=False)}")


# ── 4. 合成 ───────────────────────────────────────────────────
def _ass(cues: list[dict], path: Path) -> None:
    from .assemble import _ass_header, _ass_time
    body = "".join(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'] + 0.25)},Line,,0,0,0,,{c['text']}\n"
                   for c in cues)
    path.write_text(_ass_header(W, H) + body, encoding="utf-8")


def _join(cues: list[dict]) -> list[dict]:
    """表演版把第三句拆成兩段，字幕合回一行"""
    out = []
    for c in cues:
        if out and out[-1]["text"].endswith("，") and c["start"] - out[-1]["end"] < 1.2:
            out[-1] = {"start": out[-1]["start"], "end": c["end"], "text": out[-1]["text"] + c["text"]}
        else:
            out.append(dict(c))
    return out


def assemble() -> None:
    from .assemble import _ass_arg, _motion_filter
    rep = _load_report()
    pick = rep.get("video_pick", {})
    rnd = max((p[1:] or "1") for p in pick.values()) if pick else "1"
    a_frame = {s["n"]: _frame("A" + rnd, s["n"]) for s in SHOTS}      # 基準版用同一輪的 A 組靜幀
    names = list(a_frame.values()) + [f"lab12-epV_s{s['n']}.mp4" for s in SHOTS]
    names += [_frame(pick.get(str(s["n"]), "B"), s["n"]) for s in SHOTS]
    fetch(*names, "lab12-voice-src-plain.wav", "lab12-voice-src-perf.wav")
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    seg = OUT / "_seg"
    shutil.rmtree(seg, ignore_errors=True)
    seg.mkdir(parents=True)
    usage = {}

    def still_clip(src: Path, s: dict, dst: Path) -> None:
        n = round(s["sec"] * FPS)
        _ff("-i", str(src), "-vf", _motion_filter(s["pan"], n, W, H) + ",format=yuv420p", "-frames:v", str(n), *enc, str(dst))

    def motion_clip(src: Path, s: dict, dst: Path) -> dict:
        clip = _dur(src)
        slow = min(max(s["sec"] / clip, 1.0), 1.25)   # 最多放慢 1.25 倍；仍不夠才定格補尾，並記錄
        hold = max(0.0, s["sec"] - clip * slow)
        vf = (f"setpts={slow:.4f}*PTS,scale={W}:{H}:flags=lanczos:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},tpad=stop_mode=clone:stop_duration={hold + 0.1:.2f},format=yuv420p")
        _ff("-i", str(src), "-vf", vf, "-t", f"{s['sec']:.3f}", *enc, str(dst))
        return {"source_sec": round(clip, 2), "slowdown": round(slow, 3), "freeze_tail_sec": round(hold, 2)}

    def build(tag: str, parts: list[Path], wav: Path, cues: list[dict], subs: bool = True) -> Path:
        lst = seg / f"{tag}.txt"
        lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        silent = seg / f"{tag}_v.mp4"
        _sh("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent))
        out = OUT / f"lab12-{tag}.mp4"
        vf = []
        if subs:
            ass = seg / f"{tag}.ass"
            _ass(cues, ass)
            vf = ["-vf", f"subtitles={_ass_arg(ass)}"]
        _ff("-i", str(silent), "-i", str(wav), *vf, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", "12", "-movflags", "+faststart", str(out))
        return out

    a_parts, b_parts = [], []
    for s in SHOTS:
        pa = seg / f"A{s['n']}.mp4"
        still_clip(OUT / a_frame[s["n"]], s, pa)
        a_parts.append(pa)
        pb = seg / f"B{s['n']}.mp4"
        mv = OUT / f"lab12-epV_s{s['n']}.mp4"
        if mv.exists():
            usage[s["n"]] = {"source": mv.name, **motion_clip(mv, s, pb)}
        else:   # 沒有動作素材 → 該鏡列為未完成，暫以選定首幀運鏡代替
            still_clip(OUT / _frame(pick.get(str(s["n"]), "B"), s["n"]), s, pb)
            usage[s["n"]] = {"source": "靜幀運鏡（動作素材未完成）", "incomplete": True}
        b_parts.append(pb)
    v = rep.get("voice", {})
    outs = [build("A", a_parts, OUT / "lab12-voice-src-plain.wav", v.get("plain", {}).get("cues", [])),
            build("B", b_parts, OUT / "lab12-voice-src-perf.wav", _join(v.get("perf", {}).get("cues", []))),
            build("B-voiceonly", b_parts, OUT / "lab12-voice-src-perf.wav", [], subs=False)]
    probe = {}
    for o in outs:
        j = json.loads(_sh("ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                           "-of", "json", str(o)))
        probe[o.name] = j
    upload(*outs)
    rep["assemble"] = {"b_usage": usage, "ffprobe": probe}
    _save_report(rep)
    print("▶ 合成完成：", ", ".join(o.name for o in outs))
    print(json.dumps(usage, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["stills", "voice", "video", "assemble"])
    ap.add_argument("--pick", default="1:B,2:B,3:B")
    ap.add_argument("--shots", default="1")
    ap.add_argument("--round", type=int, default=1)
    a = ap.parse_args()
    {"stills": lambda: stills(a.round), "voice": voice, "assemble": assemble}.get(a.stage, lambda: video(a.pick, a.shots))()
