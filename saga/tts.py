"""IndexTTS-2.5 配音（在 Kaggle GPU 上跑）＋ AISHELL-3 聲線庫。

voicebench：每個候選聲線念兩句（平靜／憤怒），做成試聽帶 build/voicebench.mp4
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
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
SAMPLE = {
    "m": [("calm", "五年了，我回來了。"), ("angry", "你以為你掌控了一切？你不過是個可憐蟲！")],
    "f": [("calm", "陳先生，一切都準備好了。"), ("angry", "你給我站住！今天不說清楚，誰都別想走！")],
}


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


def voicebench(out_dir: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    job = {"job_id": uuid.uuid4().hex[:12], "voices": {}, "lines": []}
    for vid, spk, kind, age in CANDIDATES:
        job["voices"][vid] = {"speaker": spk}
        for i, (emo, text) in enumerate(SAMPLE[vid[0]]):
            job["lines"].append({"key": f"{vid}_{i}", "voice": vid, "text": text, "emo": EMO[emo]})
    dl = out_dir / "_voice"
    shutil.rmtree(dl, ignore_errors=True)
    run_job(job, dl)

    def font(size):
        r = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK TC:bold"], capture_output=True, text=True)
        return ImageFont.truetype(r.stdout.strip(), size) if r.stdout.strip() else ImageFont.load_default()

    seg_dir = out_dir / "_segs"
    shutil.rmtree(seg_dir, ignore_errors=True)
    seg_dir.mkdir(parents=True)
    segs = []
    for n, (vid, spk, kind, age) in enumerate(CANDIDATES, 1):
        wavs = [next(iter(dl.rglob(f"{vid}_{i}.wav")), None) for i in range(2)]
        if not all(wavs):
            continue
        img = Image.new("RGB", (1920, 1080), (10, 12, 30))
        d = ImageDraw.Draw(img)
        d.text((140, 300), f"聲線 {vid.upper()}", font=font(120), fill=(232, 235, 247))
        d.text((140, 470), f"{kind}　{age}", font=font(64), fill=(63, 216, 255))
        d.text((140, 580), f"AISHELL-3 · {spk}", font=font(40), fill=(145, 152, 196))
        for i, (emo, text) in enumerate(SAMPLE[vid[0]]):
            d.text((140, 720 + i * 80), f"{'平靜' if emo == 'calm' else '憤怒'}：{text}", font=font(46), fill=(232, 235, 247))
        png = seg_dir / f"{vid}.png"
        img.save(png)
        seg = seg_dir / f"{n:02d}_{vid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png),
                        "-i", str(wavs[0]), "-i", str(wavs[1]), "-filter_complex",
                        "[1:a]apad=pad_dur=0.6[a1];[a1][2:a]concat=n=2:v=0:a=1,apad=pad_dur=0.8,"
                        "aresample=44100,aformat=channel_layouts=stereo[a]",
                        "-map", "0:v", "-map", "[a]", "-shortest", "-r", "10", "-c:v", "libx264",
                        "-preset", "veryfast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", str(seg)], check=True)
        segs.append(seg)
    if not segs:
        raise RuntimeError("沒有任何聲線合成成功，請看上方 Kaggle 錯誤")
    lst = seg_dir / "list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
    out = out_dir / "voicebench.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    # 保留參考音檔，選定後可直接放進聲線庫
    refs = out_dir / "voice_refs"
    refs.mkdir(exist_ok=True)
    for f in dl.rglob("ref_*.wav"):
        shutil.copy(f, refs / f.name)
    print(f"▶ 試聽帶：{out}（{len(segs)} 個聲線）", flush=True)
    return out
