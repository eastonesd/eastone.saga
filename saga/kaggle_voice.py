# ─────────────────────────────────────────────────────────────
# 在 Kaggle GPU 上跑 IndexTTS-2.5 配音。
#   聲線來源：AISHELL-3（Apache 2.0）說話者的錄音，作為音色參考
#   __JOB_B64__ 由 saga/tts.py 換成工作清單：
#   {"job_id", "voices": {voice_id: {"speaker": "SSB0710"}},
#    "lines": [{"key", "voice", "text", "emo": [8 floats] | null, "speed": 1.0}]}
# ─────────────────────────────────────────────────────────────
import base64
import json
import os
import subprocess
import sys
import time
import traceback

T0 = time.time()
OUT = "/kaggle/working"
JOB = json.loads(base64.b64decode("__JOB_B64__").decode("utf-8"))
manifest = {"job_id": JOB["job_id"], "done": [], "errors": [], "refs": []}


def log(*a):
    print(f"[{(time.time() - T0) / 60:5.1f}m]", *a, flush=True)


def save():
    manifest["elapsed_min"] = round((time.time() - T0) / 60, 1)
    with open(f"{OUT}/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def sh(cmd, **kw):
    log("$", cmd if isinstance(cmd, str) else " ".join(cmd))
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=True, **kw)


save()
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
if JOB.get("hf_token"):
    os.environ["HF_TOKEN"] = JOB["hf_token"]

# ── 1. 安裝 IndexTTS（官方要求 Python 3.10–3.11，用 uv 建獨立環境）────────
REPO = "/kaggle/temp/index-tts"
sh([sys.executable, "-m", "pip", "install", "-q", "uv", "huggingface_hub", "hf_transfer"])
if not os.path.exists(REPO):
    sh(["git", "clone", "--depth", "1", "https://github.com/index-tts/index-tts.git", REPO])
sh("uv python install 3.11 -q", cwd=REPO)
sh("uv sync --python 3.11 -q", cwd=REPO, env=dict(os.environ, UV_LINK_MODE="copy"))
from huggingface_hub import HfApi, hf_hub_download, snapshot_download  # noqa: E402

snapshot_download("IndexTeam/IndexTTS-2.5", local_dir=f"{REPO}/checkpoints", max_workers=16)
log("IndexTTS 安裝完成")

# ── 2. 準備聲線參考（每位說話者取 3 句、約 9 秒，串成一段）─────────────
REFDIR = "/kaggle/temp/refs"
os.makedirs(REFDIR, exist_ok=True)
for vid, v in JOB["voices"].items():
    if v.get("ref_url"):  # 聲線庫裡已有的參考音檔
        ref = f"{REFDIR}/{vid}.wav"
        try:
            sh(["curl", "-sSfL", "--retry", "3", "-o", ref, v["ref_url"]])
            v["ref"] = ref
        except Exception:
            manifest["errors"].append(f"{vid}: 下載參考音檔失敗 {v['ref_url']}")
        continue
    spk = v["speaker"]
    parts = []
    for split in ("train", "test"):
        try:
            files = sorted(f.path for f in HfApi().list_repo_tree("AISHELL/AISHELL-3", path_in_repo=f"{split}/wav/{spk}",
                                                                 repo_type="dataset") if f.path.endswith(".wav"))
        except Exception:
            continue
        for name in files[:3]:
            parts.append(hf_hub_download("AISHELL/AISHELL-3", name, repo_type="dataset", local_dir="/kaggle/temp/aishell"))
        if parts:
            break
    if not parts:
        manifest["errors"].append(f"{vid}: 找不到 {spk} 的錄音")
        continue
    lst = f"{REFDIR}/{vid}.txt"
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    ref = f"{REFDIR}/{vid}.wav"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
        "-af", "silenceremove=start_periods=1:start_threshold=-45dB,loudnorm", "-ar", "22050", "-ac", "1", ref])
    v["ref"] = ref
    sh(["cp", ref, f"{OUT}/ref_{vid}.wav"])
    manifest["refs"].append(f"ref_{vid}.wav")
save()

# ── 3. 合成（在 uv 環境裡跑，逐句輸出 wav）──────────────────────────────
lines = [ln for ln in JOB["lines"] if JOB["voices"].get(ln["voice"], {}).get("ref")]
with open("/kaggle/temp/lines.json", "w", encoding="utf-8") as f:
    json.dump({"lines": lines, "refs": {k: v["ref"] for k, v in JOB["voices"].items() if v.get("ref")}}, f, ensure_ascii=False)

RUNNER = r'''
import json, os, sys, time, traceback, inspect
sys.path.insert(0, ".")
from indextts.infer_v2_5 import IndexTTS2
shard, nshards = int(sys.argv[1]), int(sys.argv[2])
data = json.load(open("/kaggle/temp/lines.json", encoding="utf-8"))
data["lines"] = data["lines"][shard::nshards]
kw = {}
sig = inspect.signature(IndexTTS2.__init__).parameters
if "use_fp16" in sig: kw["use_fp16"] = True          # T4 不支援 bf16，用 fp16
if "use_bf16" in sig: kw["use_bf16"] = False
tts = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", **kw)
infer_sig = inspect.signature(tts.infer).parameters
t0 = time.time(); n = 0
for ln in data["lines"]:
    out = f"/kaggle/working/{ln['key']}.wav"
    args = dict(spk_audio_prompt=data["refs"][ln["voice"]], text=ln["text"], output_path=out)
    if "lang" in infer_sig: args["lang"] = "ZH"
    if ln.get("emo") and "emo_vector" in infer_sig: args["emo_vector"] = ln["emo"]
    if ln.get("speed") and "duration_factor" in infer_sig: args["duration_factor"] = ln["speed"]
    for attempt in range(3):
        try:
            tts.infer(**args)
            n += 1
            print("OK", ln["key"], flush=True)
            break
        except Exception as e:
            import torch, gc
            gc.collect(); torch.cuda.empty_cache()   # 記憶體不足 → 清空後重試
            if attempt == 2:
                traceback.print_exc()
                print("ERR", ln["key"], e, flush=True)
print(f"DONE {n} lines in {time.time() - t0:.0f}s", flush=True)
'''
with open(f"{REPO}/saga_run.py", "w", encoding="utf-8") as f:
    f.write(RUNNER)
try:
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_ENABLE_HF_TRANSFER"}  # uv 環境沒裝 hf_transfer
    gpus = len(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip().splitlines()) or 1
    per_gpu = JOB.get("workers_per_gpu", 1)  # 實測每個程序約 7.8GB，T4 只放得下一個
    n = max(1, min(gpus * per_gpu, len(lines)))
    # 暖機：先單獨載入一次，讓輔助模型（w2v-bert、codec、BigVGAN…）下載完成，避免多個程序同時下載互相踩壞
    warm = subprocess.run(["uv", "run", "--python", "3.11", "python", "-c",
                           "import sys; sys.path.insert(0, '.'); from indextts.infer_v2_5 import IndexTTS2; "
                           "IndexTTS2(cfg_path='checkpoints/config.yaml', model_dir='checkpoints'); print('WARM OK')"],
                          cwd=REPO, capture_output=True, text=True, env=dict(env, CUDA_VISIBLE_DEVICES="0"))
    log("暖機", "成功" if "WARM OK" in warm.stdout else f"失敗：{warm.stderr[-800:]}")
    procs = []
    for i in range(n):
        e = dict(env, CUDA_VISIBLE_DEVICES=str(i % gpus))
        procs.append(subprocess.Popen(["uv", "run", "--python", "3.11", "python", "saga_run.py", str(i), str(n)],
                                      cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e))
        time.sleep(15)  # 錯開載入
    log(f"{n} 個配音程序（GPU ×{gpus}）")
    for p in procs:
        out, err = p.communicate()
        print(out[-6000:], err[-3000:], flush=True)
        for line in out.splitlines():
            if line.startswith("OK "):
                manifest["done"].append(line.split()[1])
            elif line.startswith("ERR "):
                manifest["errors"].append(line[4:200])
            elif line.startswith("DONE"):
                manifest.setdefault("speed", []).append(line)
        if p.returncode != 0:
            manifest["errors"].append(f"runner exit {p.returncode}: {err[-600:]}")
except Exception:
    manifest["errors"].append(traceback.format_exc()[-600:])
save()
log("完成", len(manifest["done"]), "句；錯誤", len(manifest["errors"]))
