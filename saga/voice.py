"""配音部：每句台詞一個音檔，並量出時長，供剪輯對齊字幕。

任何一句配音失敗都不會讓整批崩潰：依序換乾淨文字、換備用聲線，最後以靜音代替並記錄警告。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from pathlib import Path

from . import paths, prompts

# （砰！）、[音效]、【旁白】這類舞台指示不念出來
_STAGE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_SPEAKABLE = re.compile(r"[一-鿿A-Za-z0-9]")
BACKUP_VOICES = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"]


def spoken_text(text: str) -> str:
    """去掉舞台指示與多餘標點，回傳要念的文字（可能是空字串＝純音效）。"""
    t = _STAGE.sub("", text)
    t = re.sub(r"\.{2,}|…+", "……", t)
    t = re.sub(r"[\"'“”‘’*_#~<>&]", "", t).strip(" ，、")
    return t if _SPEAKABLE.search(t) else ""


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _silence(out: Path, dur: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{dur:.2f}", "-c:a", "libmp3lame", "-q:a", "6", str(out)],
        check=True,
    )


def _mock_tts(text: str, out: Path) -> None:
    _silence(out, max(0.8, len(text) * 0.22))  # 每字約 0.22 秒，測試時間軸用


async def _edge_tts(text: str, voice: str, out: Path) -> str:
    """回傳 'ok' 或失敗原因；絕不拋例外。"""
    import edge_tts

    last = ""
    for v in [voice, *[b for b in BACKUP_VOICES if b != voice]]:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text, v, rate="+8%").save(str(out))
                if out.exists() and out.stat().st_size > 500:
                    return "ok" if v == voice else f"改用備用聲線 {v}"
            except Exception as e:  # 網路偶發錯誤、限流
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(2 * (attempt + 1))
    out.unlink(missing_ok=True)
    return f"失敗（{last[:80]}）"


def voice_episode(slug: str, ep: int, *, mock: bool = False, concurrency: int = 4) -> dict:
    bible = paths.read_json(paths.bible_path(slug))
    episode = paths.read_json(paths.episode_path(slug, ep))
    voice_of = {c["id"]: prompts.VOICES[c["voice"]] for c in bible["characters"]}
    narrator = prompts.VOICES[bible["narrator_voice"]]
    audio_dir = paths.build_ep_dir(slug, ep) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    jobs, sfx = [], 0
    for shot in episode["shots"]:
        for i, line in enumerate(shot["lines"]):
            say = spoken_text(line["text"])
            voice = voice_of.get(line["speaker"], narrator)
            tag = hashlib.md5(f"{voice}|{say}".encode()).hexdigest()[:8]
            out = audio_dir / f"{shot['id']}_{i}_{tag}.mp3"
            line["audio"] = out.name
            if not say:  # 純音效：不念，留 0.8 秒的拍子
                line["sfx"] = True
                sfx += 1
                if not out.exists():
                    _silence(out, 0.8)
            elif not out.exists():
                jobs.append((say, voice, out))

    warnings = []
    if jobs:
        if mock:
            for text, _, out in jobs:
                _mock_tts(text, out)
        else:
            async def run_all():
                sem = asyncio.Semaphore(concurrency)

                async def one(text, voice, out):
                    async with sem:
                        return text, out, await _edge_tts(text, voice, out)
                return await asyncio.gather(*(one(*j) for j in jobs))
            for text, out, status in asyncio.run(run_all()):
                if status != "ok":
                    warnings.append(f"「{text[:12]}」{status}")
                if not out.exists():  # 最後防線：靜音，讓剪輯照常進行
                    _silence(out, max(0.8, len(text) * 0.22))

    for shot in episode["shots"]:
        for line in shot["lines"]:
            line["duration"] = round(audio_duration(audio_dir / line["audio"]), 3)
    paths.write_json(audio_dir / "timing.json", episode)
    print(f"  ✓ 第 {ep} 集配音（{len(jobs)} 句新錄、{sfx} 個音效拍）", flush=True)
    for w in warnings:
        print(f"  ⚠ 第 {ep} 集配音：{w}", flush=True)
    return episode
