"""IndexTTS-2.5 配音：Modal 雲端 GPU 版（最多同時 10 張卡）。

由 saga/tts.py 在有 MODAL_TOKEN_ID 時自動選用。第一次會建立映像檔（約 10 分鐘，之後快取）。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

import modal

REPO = "/opt/index-tts"
GPU = os.environ.get("SAGA_MODAL_GPU", "L4")
MAX_CONTAINERS = int(os.environ.get("SAGA_MODAL_MAX", "10"))

RUNNER = r'''
import json, sys, time, traceback, inspect, gc
sys.path.insert(0, ".")
import torch
from indextts.infer_v2_5 import IndexTTS2
data = json.load(open(sys.argv[1], encoding="utf-8"))
kw = {}
sig = inspect.signature(IndexTTS2.__init__).parameters
if "use_fp16" in sig: kw["use_fp16"] = True
tts = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", **kw)
isig = inspect.signature(tts.infer).parameters
t0 = time.time(); n = 0
for ln in data["lines"]:
    args = dict(spk_audio_prompt=data["refs"][ln["voice"]], text=ln["text"], output_path=f"{data['out']}/{ln['key']}.wav")
    if "lang" in isig: args["lang"] = "ZH"
    if ln.get("emo") and "emo_vector" in isig: args["emo_vector"] = ln["emo"]
    if ln.get("speed") and "duration_factor" in isig: args["duration_factor"] = ln["speed"]
    for attempt in range(3):
        try:
            tts.infer(**args); n += 1; print("OK", ln["key"], flush=True); break
        except Exception as e:
            gc.collect(); torch.cuda.empty_cache()
            if attempt == 2:
                traceback.print_exc(); print("ERR", ln["key"], e, flush=True)
print(f"DONE {n} lines in {time.time() - t0:.0f}s", flush=True)
'''


def _warmup():
    """建映像檔時先載入一次，把輔助模型（w2v-bert、codec、BigVGAN…）下載進映像檔"""
    subprocess.run([f"{REPO}/.venv/bin/python", "-c",
                    "import sys; sys.path.insert(0, '.'); from indextts.infer_v2_5 import IndexTTS2; "
                    "IndexTTS2(cfg_path='checkpoints/config.yaml', model_dir='checkpoints')"], cwd=REPO, check=True)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "git-lfs", "ffmpeg", "curl")
    .pip_install("uv", "huggingface_hub")
    .run_commands(
        f"git clone --depth 1 https://github.com/index-tts/index-tts.git {REPO}",
        f"cd {REPO} && UV_LINK_MODE=copy uv sync --python 3.11",
        f"python -c \"from huggingface_hub import snapshot_download; "
        f"snapshot_download('IndexTeam/IndexTTS-2.5', local_dir='{REPO}/checkpoints')\"",
    )
    .run_function(_warmup, gpu=GPU)
)
app = modal.App("saga-tts", image=image)


@app.function(gpu=GPU, timeout=1800, max_containers=MAX_CONTAINERS)
def synth_chunk(lines: list[dict], refs: dict[str, bytes]) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        ref_paths = {}
        for vid, data in refs.items():
            p = Path(tmp) / f"ref_{vid}.wav"
            p.write_bytes(data)
            ref_paths[vid] = str(p)
        out = Path(tmp) / "out"
        out.mkdir()
        job = Path(tmp) / "job.json"
        job.write_text(json.dumps({"lines": lines, "refs": ref_paths, "out": str(out)}, ensure_ascii=False))
        (Path(REPO) / "saga_run.py").write_text(RUNNER)
        p = subprocess.run([f"{REPO}/.venv/bin/python", "saga_run.py", str(job)], cwd=REPO, capture_output=True, text=True)
        print(p.stdout[-3000:], p.stderr[-3000:])
        return {w.stem: w.read_bytes() for w in out.glob("*.wav")}


def synthesize(job_lines: list[dict], voice_files: dict[str, Path], dl: Path) -> dict[str, Path]:
    """把句子平均分給最多 10 張 GPU 同時配音，回傳 {key: wav 路徑}"""
    dl.mkdir(parents=True, exist_ok=True)
    refs = {v: p.read_bytes() for v, p in voice_files.items()}
    n = max(1, min(MAX_CONTAINERS, math.ceil(len(job_lines) / 12)))
    chunks = [job_lines[i::n] for i in range(n)]
    print(f"▶ Modal 配音：{len(job_lines)} 句，{n} 張 {GPU} 同時進行", flush=True)
    out: dict[str, Path] = {}
    with modal.enable_output(), app.run():
        for result in synth_chunk.map(chunks, kwargs={"refs": refs}, return_exceptions=True):
            if isinstance(result, Exception):
                print(f"  ⚠ Modal 一個分片失敗：{str(result)[:200]}", flush=True)
                continue
            for key, data in result.items():
                f = dl / f"{key}.wav"
                f.write_bytes(data)
                out[key] = f
    return out
