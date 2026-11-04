"""配音部：每句台詞一個音檔，並量出時長，供剪輯對齊字幕。"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path

from . import paths, prompts


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _mock_tts(text: str, out: Path) -> None:
    # 每個字約 0.22 秒的靜音，用來測試時間軸
    dur = max(0.8, len(text) * 0.22)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{dur:.2f}", "-c:a", "libmp3lame", "-q:a", "6", str(out)],
        check=True,
    )


async def _edge_tts(text: str, voice: str, out: Path) -> None:
    import edge_tts

    for attempt in range(3):
        try:
            await edge_tts.Communicate(text, voice, rate="+8%").save(str(out))
            return
        except Exception as e:  # 網路偶發錯誤
            if attempt == 2:
                raise
            print(f"  TTS 重試（{e}）", flush=True)
            await asyncio.sleep(3)


def voice_episode(slug: str, ep: int, *, mock: bool = False, concurrency: int = 8) -> dict:
    bible = paths.read_json(paths.bible_path(slug))
    episode = paths.read_json(paths.episode_path(slug, ep))
    voice_of = {c["id"]: prompts.VOICES[c["voice"]] for c in bible["characters"]}
    narrator = prompts.VOICES[bible["narrator_voice"]]
    audio_dir = paths.build_ep_dir(slug, ep) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for shot in episode["shots"]:
        for i, line in enumerate(shot["lines"]):
            voice = voice_of.get(line["speaker"], narrator)
            tag = hashlib.md5(f"{voice}|{line['text']}".encode()).hexdigest()[:8]
            out = audio_dir / f"{shot['id']}_{i}_{tag}.mp3"
            line["audio"] = out.name
            if not out.exists():
                jobs.append((line["text"], voice, out))

    if jobs:
        if mock:
            for text, _, out in jobs:
                _mock_tts(text, out)
        else:
            async def run_all():
                sem = asyncio.Semaphore(concurrency)

                async def one(text, voice, out):
                    async with sem:
                        await _edge_tts(text, voice, out)
                await asyncio.gather(*(one(*j) for j in jobs))
            asyncio.run(run_all())

    for shot in episode["shots"]:
        for line in shot["lines"]:
            line["duration"] = round(audio_duration(audio_dir / line["audio"]), 3)
    paths.write_json(audio_dir / "timing.json", episode)
    print(f"  ✓ 第 {ep} 集配音（{len(jobs)} 句新錄）", flush=True)
    return episode
