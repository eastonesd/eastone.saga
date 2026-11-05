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


WARNINGS: list[str] = []  # 給 workflow 回報用


def guess_emo(text: str) -> str | None:
    """劇本沒標情緒時的粗略判斷"""
    if re.search(r"[？?][！!]|[！!][？?]|不可能|怎麼會", text):
        return "surprised"
    if re.search(r"[！!]", text):
        return "angry"
    if re.search(r"……|…", text) and re.search(r"對不起|求|為什麼|別走", text):
        return "sad"
    return None


def _plan(slug: str, ep: int, engine: str, cast: dict, edge_voice: dict, edge_narrator: str):
    """回傳（episode, [(line, say, voice, emo, out_path)]），各引擎檔名不同，換聲線或台詞會自動重錄"""
    episode = paths.read_json(paths.episode_path(slug, ep))
    audio_dir = paths.build_ep_dir(slug, ep) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    plan = []
    for shot in episode["shots"]:
        for i, line in enumerate(shot["lines"]):
            say = spoken_text(line["text"])
            emo = line.get("emo") if line.get("emo") in ("calm", "angry", "sad", "happy", "afraid", "surprised",
                                                          "disdain") else guess_emo(line["text"])
            if engine == "indextts":
                voice = cast.get(line["speaker"]) or cast.get("narrator")
                tag = hashlib.md5(f"idx|{voice}|{emo}|{say}".encode()).hexdigest()[:8]
            else:
                voice = edge_voice.get(line["speaker"], edge_narrator)
                tag = hashlib.md5(f"{voice}|{say}".encode()).hexdigest()[:8]
            out = audio_dir / f"{shot['id']}_{i}_{tag}.mp3"
            line["audio"] = out.name
            plan.append((line, say, voice, emo, out))
    return episode, plan


def voice_batch(slug: str, eps: list[int], *, mock: bool = False, wait_gpu=None) -> None:
    """一整批的配音。IndexTTS 一次送 Kaggle；失敗或缺漏的句子自動改用 Edge-TTS 補上。"""
    from . import tts

    bible = paths.read_json(paths.bible_path(slug))
    cfg = bible.get("tts") or {}
    engine = cfg.get("engine", "edge")
    cast = cfg.get("cast") or {}
    edge_voice = {c["id"]: prompts.VOICES.get(c.get("voice"), "zh-CN-YunxiNeural") for c in bible["characters"]}
    edge_narrator = prompts.VOICES.get(bible.get("narrator_voice"), "zh-CN-XiaoxiaoNeural")
    plans = {ep: _plan(slug, ep, engine, cast, edge_voice, edge_narrator) for ep in eps}

    if engine == "indextts" and not mock:
        todo = [(ep, k, say, voice, emo, out) for ep, (_, plan) in plans.items()
                for k, (line, say, voice, emo, out) in enumerate(plan) if say and not out.exists()]
        if todo:
            items = [{"key": f"e{ep}_{k}", "voice": voice, "text": say, "emo": emo}
                     for ep, k, say, voice, emo, out in todo]
            print(f"▶ IndexTTS 配音：{len(items)} 句（聲線：{', '.join(sorted(set(cast.values())))}）", flush=True)
            try:
                try:
                    wavs = tts.synthesize(items, paths.BUILD_DIR / slug / "_tts")
                except Exception as e:
                    # Kaggle 同時只開放有限的 GPU 工作階段：等算圖那邊結束再試一次
                    if wait_gpu and re.search(r"session|concurren|limit|maximum|quota", str(e), re.I):
                        print(f"  … GPU 工作階段已滿，等算圖完成後再配音（{str(e)[:80]}）", flush=True)
                        wait_gpu()
                        wavs = tts.synthesize(items, paths.BUILD_DIR / slug / "_tts")
                    else:
                        raise
                for (ep, k, say, voice, emo, out), it in zip(todo, items):
                    w = wavs.get(it["key"])
                    if w:
                        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(w),
                                        "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                                               "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                                               "loudnorm=I=-16:TP=-1.5",
                                        "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "3", str(out)], check=True)
                ok = sum(1 for _, _, _, _, _, o in todo if o.exists())
                print(f"  ✓ IndexTTS 完成 {ok}／{len(todo)} 句", flush=True)
                if ok < len(todo):
                    WARNINGS.append(f"IndexTTS 只完成 {ok}／{len(todo)} 句，其餘改用備援 Edge 配音")
            except Exception as e:  # Kaggle 出狀況 → 整批改用 Edge-TTS，不中斷生產
                print(f"  ⚠ IndexTTS 失敗，改用 Edge-TTS 補上：{e}", flush=True)
                WARNINGS.append(f"IndexTTS 配音失敗，本批改用備援 Edge 配音（{str(e)[:120]}）")

    for ep, (episode, plan) in plans.items():
        _finish_episode(slug, ep, episode, plan, mock=mock, cast=cast, edge_voice=edge_voice, edge_narrator=edge_narrator)


def _finish_episode(slug, ep, episode, plan, *, mock, cast, edge_voice, edge_narrator, concurrency=4):
    audio_dir = paths.build_ep_dir(slug, ep) / "audio"
    jobs, sfx, fallback = [], 0, 0
    for line, say, voice, emo, out in plan:
        if not say:  # 純音效：不念，留 0.8 秒的拍子
            line["sfx"] = True
            sfx += 1
            if not out.exists():
                _silence(out, 0.8)
        elif not out.exists():
            ev = voice if str(voice).startswith("zh-") else edge_voice.get(line["speaker"], edge_narrator)
            fallback += 0 if (mock or str(voice).startswith("zh-")) else 1
            jobs.append((say, ev, out))

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

    for line, *_ in plan:
        line["duration"] = round(audio_duration(audio_dir / line["audio"]), 3)
    paths.write_json(audio_dir / "timing.json", episode)
    print(f"  ✓ 第 {ep} 集配音（Edge 補錄 {len(jobs)} 句" + (f"，其中 {fallback} 句為 IndexTTS 失敗後備援" if fallback else "")
          + f"、{sfx} 個音效拍）", flush=True)
    for w in warnings:
        print(f"  ⚠ 第 {ep} 集配音：{w}", flush=True)
    return episode


def voice_episode(slug: str, ep: int, *, mock: bool = False) -> dict:
    voice_batch(slug, [ep], mock=mock)
    return paths.read_json(paths.build_ep_dir(slug, ep) / "audio" / "timing.json")
