# ─────────────────────────────────────────────────────────────
# 送到 Kaggle 免費 GPU（T4 ×2）上執行的算圖程式。
# 主程式負責調度；每張 GPU 各開一個子程序平行算圖。
# 模型來自預先建好的 saga-models notebook（掛載，不重新下載）。
# __JOB_B64__ 會由 saga/render.py 換成這次的工作清單。
# ─────────────────────────────────────────────────────────────
import base64
import glob
import json
import os
import subprocess
import sys
import time

T0 = time.time()
OUT = "/kaggle/working"
JOB_PATH = "/tmp/saga_job.json"
WORKER_PATH = "/tmp/saga_worker.py"


def log(*a):
    print(f"[{(time.time() - T0) / 60:5.1f}m]", *a, flush=True)


WORKER_SRC = r'''
import json, os, sys, time, traceback
mode, shard, nshards = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
JOB = json.load(open("/tmp/saga_job.json", encoding="utf-8"))
M, OUT, T0 = JOB["_models_dir"], "/kaggle/working", float(JOB["_t0"])
tag = f"[GPU{os.environ.get('CUDA_VISIBLE_DEVICES')}]"
def log(*a): print(f"[{(time.time() - T0) / 60:5.1f}m]", tag, *a, flush=True)
def out_of_time(): return (time.time() - T0) / 60 > JOB.get("time_budget_min", 240)

import torch
from PIL import Image
from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLPipeline

def load(ip=True):
    vae = AutoencoderKL.from_pretrained(f"{M}/vae", torch_dtype=torch.float16)
    pipe = StableDiffusionXLPipeline.from_pretrained(f"{M}/sdxl", vae=vae, torch_dtype=torch.float16, variant="fp16")
    pipe.load_lora_weights(f"{M}/lightning", weight_name="sdxl_lightning_8step_lora.safetensors")
    pipe.fuse_lora()
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    if ip:
        pipe.load_ip_adapter(f"{M}/ip", subfolder="sdxl_models", weight_name="ip-adapter_sdxl_vit-h.safetensors",
                             image_encoder_folder="models/image_encoder", torch_dtype=torch.float16)
    pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    return pipe

KW = dict(num_inference_steps=JOB.get("steps", 8), guidance_scale=0.0)

if mode == "refs":
    pipe = load(ip=False)
    for c in JOB["characters"]:
        if c.get("ref_b64"):
            continue
        img = pipe(prompt=c["ref_prompt"], width=896, height=1152,
                   generator=torch.Generator("cuda").manual_seed(c["seed"]), **KW).images[0]
        img.convert("RGB").resize((448, 576)).save(f"{OUT}/ref_{c['id']}.jpg", quality=92)
        log("定妝照", c["id"])

elif mode == "shots":
    pipe = load()
    refs = {}
    for c in JOB["characters"]:
        p = f"{OUT}/ref_{c['id']}.jpg"
        if os.path.exists(p):
            refs[c["id"]] = Image.open(p).convert("RGB")
    blank = Image.new("RGB", (448, 576), (128, 128, 128))
    mine = JOB["shots"][shard::nshards]
    W, H = JOB.get("width", 768), JOB.get("height", 1344)
    done = 0
    for s in mine:
        if out_of_time():
            log("時間預算用完"); break
        try:
            ref = refs.get(s.get("ref"))
            pipe.set_ip_adapter_scale(JOB.get("ip_scale", 0.6) if ref is not None else 0.0)
            img = pipe(prompt=s["prompt"], width=W, height=H, ip_adapter_image=ref if ref is not None else blank,
                       generator=torch.Generator("cuda").manual_seed(s["seed"]), **KW).images[0]
            img.save(f"{OUT}/{s['key']}.png")
            done += 1
            if done % 10 == 0 or done == len(mine):
                log(f"{done}/{len(mine)}")
        except Exception:
            traceback.print_exc()

elif mode == "video":
    from diffusers import WanImageToVideoPipeline
    from diffusers.utils import export_to_video
    vp = WanImageToVideoPipeline.from_pretrained(JOB["video_model"], torch_dtype=torch.float16)
    vp.enable_model_cpu_offload()
    for v in JOB.get("video_shots", []):
        src = f"{OUT}/{v['key']}.png"
        if out_of_time() or not os.path.exists(src):
            continue
        try:
            img = Image.open(src).convert("RGB").resize((480, 832))
            frames = vp(image=img, prompt=v["prompt"], height=832, width=480, num_frames=49,
                        num_inference_steps=JOB.get("video_steps", 20), guidance_scale=5.0).frames[0]
            export_to_video(frames, f"{OUT}/{v['key']}.mp4", fps=16)
            log("影片", v["key"])
        except Exception:
            traceback.print_exc()
'''

# ── 1. 準備 ──────────────────────────────────────────────
JOB = json.loads(base64.b64decode("__JOB_B64__").decode("utf-8"))
markers = glob.glob("/kaggle/input/**/saga_models.json", recursive=True)
if not markers:
    sys.exit("找不到 saga-models 模型包（kernel_sources 沒有掛載成功）")
base = os.path.dirname(markers[0])
JOB["_models_dir"] = os.path.join(base, "models")
JOB["_t0"] = T0
with open(JOB_PATH, "w", encoding="utf-8") as f:
    json.dump(JOB, f, ensure_ascii=False)
with open(WORKER_PATH, "w", encoding="utf-8") as f:
    f.write(WORKER_SRC)
log("模型包：", base)

# 離線安裝需要的新版套件（幾秒鐘）
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-index",
                "--find-links", os.path.join(base, "wheels"), "diffusers", "peft", "huggingface_hub", "ftfy"],
               check=False)

gpus = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip().splitlines()
n = max(1, len(gpus))
log(f"GPU ×{n}：", "; ".join(g.split(" (")[0] for g in gpus))


def run(mode, shard=0, nshards=1, gpu=0):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu), PYTHONUNBUFFERED="1")
    return subprocess.Popen([sys.executable, WORKER_PATH, mode, str(shard), str(nshards)], env=env)


# ── 2. 角色定妝照（只做還沒有的） ─────────────────────────
for c in JOB["characters"]:
    if c.get("ref_b64"):
        with open(f"{OUT}/ref_{c['id']}.jpg", "wb") as f:
            f.write(base64.b64decode(c["ref_b64"]))
if any(not c.get("ref_b64") for c in JOB["characters"]):
    run("refs").wait()

# ── 3. 分鏡圖：每張 GPU 各分一半 ───────────────────────────
if JOB["shots"]:
    procs = [run("shots", i, n, i) for i in range(n)]
    for p in procs:
        p.wait()

# ── 4.（選用）關鍵鏡頭影片 ────────────────────────────────
if JOB.get("video_shots") and (time.time() - T0) / 60 < JOB.get("time_budget_min", 240):
    run("video").wait()

# ── 5. 清單 ──────────────────────────────────────────────
images = sorted(os.path.basename(p) for p in glob.glob(f"{OUT}/ep*_s*.png"))
videos = sorted(os.path.basename(p) for p in glob.glob(f"{OUT}/ep*_s*.mp4"))
wanted = {s["key"] for s in JOB["shots"]}
manifest = {
    "job_id": JOB["job_id"],
    "refs": [f"ref_{c['id']}.jpg" for c in JOB["characters"] if not c.get("ref_b64")
             and os.path.exists(f"{OUT}/ref_{c['id']}.jpg")],
    "images": images,
    "videos": videos,
    "partial": len(wanted - {i[:-4] for i in images}) > 0,
    "errors": [],
    "elapsed_min": round((time.time() - T0) / 60, 1),
    "gpus": n,
}
with open(f"{OUT}/manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)
log(f"完成：{len(images)} 張圖、{len(videos)} 段影片，共 {manifest['elapsed_min']} 分鐘")
