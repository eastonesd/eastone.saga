"""標竿片分析：把別人覺得好看的短劇片段，量化成可以跟我們對照的數據。

量化（ffmpeg，免費）：鏡頭長度與剪輯頻率、畫面動態量、響度與動態範圍、停頓、語速
質化（Gemini 看＋聽整段影片）：畫風、打光、運鏡、表演、配音語氣、混音、字幕包裝
只存文字與數據，不存對方的畫面或影片（版權）。
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path

import requests

LAB = Path(__file__).resolve().parent.parent / "lab" / "refs"


def _ff(args: list[str]) -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True).stderr


def probe(f: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(f)],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
    return {"duration": round(float(d["format"]["duration"]), 2), "width": v.get("width"), "height": v.get("height"),
            "fps": round(float(num) / max(1.0, float(den)), 2),
            "has_audio": any(s["codec_type"] == "audio" for s in d["streams"])}


def content_crop(f: Path) -> str:
    """螢幕錄影常有手機介面與黑邊，會稀釋切鏡與動態的數值：先偵測真正的影片區域。"""
    log = _ff(["-i", str(f), "-vf", "fps=2,cropdetect=limit=0.12:round=2:reset=0", "-an", "-f", "null", "-"])
    crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", log)
    if not crops:
        return ""
    w, h, x, y = map(int, crops[-1].split(":"))
    return f"crop={w}:{h}:{x}:{y}," if w * h > 0 else ""


def shots(f: Path, dur: float, crop: str = "") -> dict:
    log = _ff(["-i", str(f), "-vf", f"{crop}scale=320:-2,select='gt(scene,0.22)',showinfo", "-an", "-f", "null", "-"])
    cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", log)]
    bounds = [0.0] + [c for c in cuts if 0.3 < c < dur - 0.3] + [dur]
    lens = [round(b - a, 2) for a, b in zip(bounds, bounds[1:]) if b - a > 0.15]
    return {"cuts": len(lens) - 1, "cuts_per_min": round((len(lens) - 1) / dur * 60, 1),
            "shot_avg_s": round(statistics.mean(lens), 2), "shot_median_s": round(statistics.median(lens), 2),
            "shot_max_s": max(lens)}


def motion(f: Path, crop: str = "") -> dict:
    """畫面動態量：相鄰影格差異的平均亮度（0＝完全靜止）。轉場那一格會被中位數過濾掉。"""
    log = _ff(["-i", str(f), "-vf", f"{crop}fps=12,scale=256:-2,format=gray,tblend=all_mode=difference,signalstats,metadata=print",
               "-an", "-f", "null", "-"])
    ys = [float(x) for x in re.findall(r"lavfi\.signalstats\.YAVG=([0-9.]+)", log)]
    if not ys:
        return {}
    still = sum(1 for y in ys if y < 0.8) / len(ys)
    return {"motion_median": round(statistics.median(ys), 2), "motion_p90": round(sorted(ys)[int(len(ys) * 0.9)], 2),
            "still_ratio": round(still, 2)}


def audio(f: Path, dur: float) -> dict:
    out: dict = {}
    log = _ff(["-i", str(f), "-af", "ebur128", "-vn", "-f", "null", "-"])
    m = re.search(r"Integrated loudness:\s+I:\s+(-?[0-9.]+)", log)
    r = re.search(r"Loudness range:\s+LRA:\s+([0-9.]+)", log)
    if m:
        out["lufs"] = float(m.group(1))
    if r:
        out["lra"] = float(r.group(1))
    log = _ff(["-i", str(f), "-af", "silencedetect=noise=-35dB:d=0.25", "-vn", "-f", "null", "-"])
    sil = [float(x) for x in re.findall(r"silence_duration: ([0-9.]+)", log)]
    out["pauses"] = len(sil)
    out["pause_avg_s"] = round(statistics.mean(sil), 2) if sil else 0.0
    out["silence_ratio"] = round(sum(sil) / dur, 2)
    return out


def speech_rate(f: Path) -> dict:
    """用 faster-whisper（CPU）轉逐字稿，算每秒幾個字。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {}
    import numpy as np
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout  # 自己解碼，避開 faster-whisper 內建解碼器的版本問題
    wav = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(wav, language="zh", vad_filter=True)
    segs = list(segs)
    chars = sum(len(re.sub(r"[\s，。！？、,.!?…「」『』]", "", s.text)) for s in segs)
    talk = sum(s.end - s.start for s in segs)
    return {"chars": chars, "talk_s": round(talk, 1), "chars_per_s": round(chars / talk, 2) if talk else 0.0,
            "line_avg_chars": round(chars / len(segs), 1) if segs else 0.0,
            "transcript": "\n".join(s.text.strip() for s in segs)[:1500]}


def measure(f: Path, whisper: bool = True) -> dict:
    p = probe(f)
    crop = content_crop(f)
    m = {**p, "crop": crop.rstrip(","), **shots(f, p["duration"], crop), **motion(f, crop)}
    if p["has_audio"]:
        m.update(audio(f, p["duration"]))
        if whisper:
            try:
                m.update(speech_rate(f))
            except Exception as e:  # 語速只是其中一項，失敗不拖垮整份分析
                print(f"  ⚠ 語速分析失敗：{e}", flush=True)
    return m


# ── Gemini：看＋聽整段影片 ──────────────────────────────────

PROMPT = """你是資深短劇導演兼後期總監。這是一段觀眾覺得好看的短劇片段。
提供者說他喜歡：「{like}」。請優先針對這一點深入分析。

我們的團隊用 AI 生產短劇（SDXL 算圖＋圖轉影片＋AI 配音＋FFmpeg 剪輯），目前的弱點是：配音像在念稿、畫面動態少、質感不夠。
請用繁體中文，依下列標題輸出，每項都要具體到「我們可以直接調的參數或做法」，不要空泛形容：

## 畫風與質感
（材質、打光方向與色溫、調色、景深、構圖習慣）
## 運鏡與動態
（每種運鏡出現的時機、角色身上哪些部位在動、動作幅度、特效）
## 剪輯節奏
（切鏡時機、轉場手法、開頭前 3 秒怎麼抓人）
## 配音與表演
（語速、停頓、音高起伏、氣聲／吼聲／哽咽等技巧、情緒如何跟劇情走）
## 混音與音效
（人聲與配樂的比例、空間感（殘響）、音效種類與出現點）
## 字幕與包裝
（字體、大小、位置、特效字、角色名牌）
## 我們最該先學的三件事
（依投資報酬率排序，每件寫明要改哪個環節）"""


def _gemini_models(key: str) -> list[str]:
    from .llm import Gemini
    return [m for m in Gemini(key).models() if "flash" in m or "pro" in m]


def gemini_review(f: Path, like: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if os.environ.get("BENCH_GEMINI") != "1":
        return "（質化分析改由賈維斯親自看片：在對話裡說「分析標竿 #編號」）"
    if not key:
        return "（沒有 GEMINI_API_KEY，略過質化分析）"
    size = f.stat().st_size
    r = requests.post("https://generativelanguage.googleapis.com/upload/v1beta/files", params={"key": key},
                      headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                               "X-Goog-Upload-Header-Content-Length": str(size),
                               "X-Goog-Upload-Header-Content-Type": "video/mp4", "Content-Type": "application/json"},
                      json={"file": {"display_name": f.name}}, timeout=60)
    r.raise_for_status()
    up = requests.post(r.headers["X-Goog-Upload-URL"], headers={
        "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"}, data=f.read_bytes(), timeout=600)
    up.raise_for_status()
    fobj = up.json()["file"]
    for _ in range(60):  # 等 Gemini 處理影片
        if fobj.get("state") == "ACTIVE":
            break
        time.sleep(5)
        fobj = requests.get(f"https://generativelanguage.googleapis.com/v1beta/{fobj['name']}",
                            params={"key": key}, timeout=30).json()
    errors = []
    for model in _gemini_models(key):
        body = {"contents": [{"role": "user", "parts": [
            {"file_data": {"mime_type": fobj.get("mimeType", "video/mp4"), "file_uri": fobj["uri"]}},
            {"text": PROMPT.format(like=like or "整體")}]}],
            "generationConfig": {"temperature": 0.4}}
        g = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": key}, json=body, timeout=300)
        if g.ok:
            try:
                return "".join(p.get("text", "") for p in g.json()["candidates"][0]["content"]["parts"]) + \
                    f"\n\n<sub>分析模型：{model}</sub>"
            except (KeyError, IndexError):
                errors.append(f"{model}: 空回應")
                continue
        errors.append(f"{model}: HTTP {g.status_code}")
    return "（質化分析失敗：" + "；".join(errors[-3:]) + "）"


# ── 從 Issue 內文取得影片 ─────────────────────────────────

VIDEO_URL = re.compile(r"user-attachments/assets/|\.(mp4|mov|webm|m4v)(\?|$)|(cdn|media)\.discordapp\.(com|net)/", re.I)
MAX_BYTES = 200 * 1024 * 1024


def parse_issue(body: str) -> dict:
    def sec(name: str) -> str:
        m = re.search(rf"###\s*{name}\s*\n+(.*?)(?=\n###|\n---|\n由 A\.R\.T\.H\.U\.R\.|\Z)", body, re.S)
        v = m.group(1).strip() if m else ""
        return "" if v.startswith("_No response_") else v
    urls = re.findall(r"https://[^\s)\]>\"']+", body)
    video = next((u for u in urls if VIDEO_URL.search(u)), "")
    like = re.sub(r"https?://\S+|<[^>]+>|!\[[^\]]*\]\([^)]*\)", "", sec("喜歡哪裡")).replace("\n", " ").strip()
    return {"video": video, "like": like[:300], "source": sec("出處")[:120]}


def download(url: str, out: Path) -> Path:
    if not url.startswith("https://"):
        raise ValueError("只接受 https 影片網址")
    headers = {}
    host = re.sub(r"^https://([^/]+)/.*$", r"\1", url)
    if host in ("github.com", "api.github.com") and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"
    with requests.get(url, headers=headers, stream=True, timeout=120, allow_redirects=True) as r:
        r.raise_for_status()
        got = 0
        with out.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                got += len(chunk)
                if got > MAX_BYTES:
                    raise ValueError("影片超過 200MB")
                f.write(chunk)
    probe(out)  # 不是影片會在這裡失敗
    return out


# ── 報告 ──────────────────────────────────────────────────

ROWS = [  # (欄位, 中文, 說明)
    ("duration", "片長（秒）", ""),
    ("cuts_per_min", "每分鐘切鏡", "越高節奏越快"),
    ("shot_avg_s", "平均鏡頭長（秒）", ""),
    ("motion_median", "畫面動態量", "相鄰影格差異；0＝靜止圖"),
    ("still_ratio", "靜止影格比例", "越低代表一直在動"),
    ("chars_per_s", "語速（字／秒）", ""),
    ("line_avg_chars", "每句字數", ""),
    ("pause_avg_s", "平均停頓（秒）", ""),
    ("silence_ratio", "靜音比例", ""),
    ("lufs", "整體響度（LUFS）", "平台建議約 -14"),
    ("lra", "響度起伏（LU）", "越大越有戲劇張力"),
]


def report(theirs: dict, ours: dict, review: str, like: str, source: str) -> str:
    lines = ["## 🎯 標竿分析", ""]
    if source:
        lines.append(f"**出處**：{source}　")
    lines += [f"**喜歡的地方**：{like or '（未填）'}", "", "### 數據對照", "",
              "| 指標 | 標竿 | 我們 | 說明 |", "|---|---|---|---|"]
    for k, name, note in ROWS:
        a, b = theirs.get(k, "—"), ours.get(k, "—")
        lines.append(f"| {name} | {a} | {b} | {note} |")
    if theirs.get("transcript"):
        lines += ["", "<details><summary>逐字稿（自動辨識）</summary>", "", theirs["transcript"], "", "</details>"]
    lines += ["", "### 導演視角", "", review]
    return "\n".join(lines)


def run(video: Path, ours_video: Path | None, like: str, source: str, ref_id: str) -> str:
    theirs = measure(video)
    ours = measure(ours_video, whisper=True) if ours_video and ours_video.exists() else {}
    review = gemini_review(video, like)
    LAB.mkdir(parents=True, exist_ok=True)
    rec = {"id": ref_id, "like": like, "source": source, "metrics": {k: v for k, v in theirs.items() if k != "transcript"},
           "ours": {k: v for k, v in ours.items() if k != "transcript"}, "review": review}
    (LAB / f"{ref_id}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return report(theirs, ours, review, like, source)
