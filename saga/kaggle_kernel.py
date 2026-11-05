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
from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
from safetensors.torch import load_file

def load(ip=True):
    # SDXL-Lightning 8 步完整 UNet：直接以 fp16 載入 GPU，不經過 LoRA／peft
    config = UNet2DConditionModel.load_config(f"{M}/sdxl", subfolder="unet")
    with torch.device("meta"):
        unet = UNet2DConditionModel.from_config(config)
    state = {k: v.to("cuda", torch.float16) for k, v in load_file(f"{M}/lightning/sdxl_lightning_8step_unet.safetensors").items()}
    unet.load_state_dict(state, assign=True)
    del state
    vae = AutoencoderKL.from_pretrained(f"{M}/vae", torch_dtype=torch.float16)
    pipe = StableDiffusionXLPipeline.from_pretrained(f"{M}/sdxl", unet=unet, vae=vae, torch_dtype=torch.float16, variant="fp16")
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    if ip:
        pipe.load_ip_adapter(f"{M}/ip", subfolder="sdxl_models", weight_name="ip-adapter-plus-face_sdxl_vit-h.safetensors",
                             image_encoder_folder="models/image_encoder", torch_dtype=torch.float16)
    pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    return pipe

KW = dict(num_inference_steps=JOB.get("steps", 8), guidance_scale=0.0)

if mode == "refs":
    pipe = load(ip=False)
    if JOB.get("style_lora"):
        st = JOB["style_lora"]
        pipe.load_lora_weights(st["repo"], weight_name=st["file"], adapter_name="style")
        pipe.set_adapters(["style"], adapter_weights=[st.get("scale", 0.8)])
    for c in JOB["characters"]:
        if c.get("ref_b64"):
            continue
        img = pipe(prompt=c["ref_prompt"], width=896, height=1152,
                   generator=torch.Generator("cuda").manual_seed(c["seed"]), **KW).images[0]
        img.convert("RGB").resize((448, 576)).save(f"{OUT}/ref_{c['id']}.jpg", quality=92)
        log("定妝照", c["id"])

elif mode == "shots":
    pipe = load()
    style = JOB.get("style_lora")
    if style:  # 畫風 LoRA（例如 3D 國漫風），每個鏡頭可個別調強度
        pipe.load_lora_weights(style["repo"], weight_name=style["file"], adapter_name="style")
    from diffusers.image_processor import IPAdapterMaskProcessor
    maskproc = IPAdapterMaskProcessor()

    def face(img):  # 只給鎖臉模組看臉，避免把定妝照的背景、構圖一起帶進去
        w, h = img.size
        return img.crop((int(w * 0.18), int(h * 0.04), int(w * 0.82), int(h * 0.62)))

    refs = {}
    for c in JOB["characters"]:
        p = f"{OUT}/ref_{c['id']}.jpg"
        if os.path.exists(p):
            refs[c["id"]] = face(Image.open(p).convert("RGB"))
    blank = Image.new("RGB", (224, 224), (128, 128, 128))
    mine = JOB["shots"][shard::nshards]
    W, H = JOB.get("width", 1344), JOB.get("height", 768)
    left = Image.new("L", (W, H), 0); left.paste(255, (0, 0, W // 2 + W // 10, H))
    right = Image.new("L", (W, H), 0); right.paste(255, (W // 2 - W // 10, 0, W, H))
    duo_masks = maskproc.preprocess([left, right], height=H, width=W)
    duo_masks = [duo_masks.reshape(1, duo_masks.shape[0], duo_masks.shape[2], duo_masks.shape[3])]
    scale = JOB.get("ip_scale", 0.5)
    done = 0
    for s in mine:
        if out_of_time():
            log("時間預算用完"); break
        try:
            if style:
                pipe.set_adapters(["style"], adapter_weights=[s.get("lora", style.get("scale", 0.8))])
            ids = [r for r in (s.get("refs") or [s.get("ref")]) if r in refs]
            gen = torch.Generator("cuda").manual_seed(s["seed"])
            if len(ids) >= 2:  # 雙人鏡頭：左半邊鎖第一人、右半邊鎖第二人
                pipe.set_ip_adapter_scale([[scale, scale]])
                img = pipe(prompt=s["prompt"], width=W, height=H, ip_adapter_image=[[refs[ids[0]], refs[ids[1]]]],
                           cross_attention_kwargs={"ip_adapter_masks": duo_masks}, generator=gen, **KW).images[0]
            else:
                ref = refs.get(ids[0]) if ids else None
                pipe.set_ip_adapter_scale(scale if ref is not None else 0.0)
                img = pipe(prompt=s["prompt"], width=W, height=H, ip_adapter_image=ref if ref is not None else blank,
                           generator=gen, **KW).images[0]
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

# ── 1. 準備：模型直接從 Hugging Face 下載到本機硬碟 ─────────────
# 實測：Kaggle 掛載資料約 12 MB/s（10GB 要 14 分鐘），HF 直接下載 > 200 MB/s（不到 1 分鐘）
JOB = json.loads(base64.b64decode("__JOB_B64__").decode("utf-8"))
M = "/kaggle/temp/models"
JOB["_models_dir"] = M
JOB["_t0"] = T0
with open(JOB_PATH, "w", encoding="utf-8") as f:
    json.dump(JOB, f, ensure_ascii=False)
with open(WORKER_PATH, "w", encoding="utf-8") as f:
    f.write(WORKER_SRC)

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "-U",
                "diffusers>=0.35", "huggingface_hub", "ftfy", "hf_transfer"], check=False)
# Kaggle 內建的舊版 torchao 會讓 peft 載入 LoRA 時報錯；移除後 peft 會自動略過它
subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchao"], check=False)
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

RECIPE = {
    "sdxl": ("stabilityai/stable-diffusion-xl-base-1.0", [
        "model_index.json", "scheduler/*",
        "text_encoder/config.json", "text_encoder/model.fp16.safetensors",
        "text_encoder_2/config.json", "text_encoder_2/model.fp16.safetensors",
        "tokenizer/*", "tokenizer_2/*", "unet/config.json"]),
    "vae": ("madebyollin/sdxl-vae-fp16-fix", ["config.json", "diffusion_pytorch_model.safetensors"]),
    "ip": ("h94/IP-Adapter", ["sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors",
                              "models/image_encoder/config.json", "models/image_encoder/model.safetensors"]),
}
for name, (repo, patterns) in RECIPE.items():
    try:
        snapshot_download(repo, local_dir=f"{M}/{name}", allow_patterns=patterns, max_workers=16)
    except Exception:  # hf_transfer 不可用時退回一般下載
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        snapshot_download(repo, local_dir=f"{M}/{name}", allow_patterns=patterns, max_workers=16)
hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_8step_unet.safetensors", local_dir=f"{M}/lightning")
log("模型下載完成")

gpus = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip().splitlines()
n = max(1, len(gpus))
log(f"GPU ×{n}：", "; ".join(g.split(" (")[0] for g in gpus))


ERRORS = []


def run(mode, shard=0, nshards=1, gpu=0):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu), PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, WORKER_PATH, mode, str(shard), str(nshards)], env=env)
    p.label = f"{mode}#{shard}@GPU{gpu}"
    return p


def wait_all(*procs):
    for p in procs:
        if p.wait() != 0:
            ERRORS.append(f"{p.label} 異常結束（exit {p.returncode}），詳見 Kaggle 日誌")
            log("⚠", ERRORS[-1])


# ── 2. 角色定妝照（只做還沒有的） ─────────────────────────
for c in JOB["characters"]:
    if c.get("ref_b64"):
        with open(f"{OUT}/ref_{c['id']}.jpg", "wb") as f:
            f.write(base64.b64decode(c["ref_b64"]))
if any(not c.get("ref_b64") for c in JOB["characters"]):
    wait_all(run("refs"))

# ── 3. 分鏡圖：每張 GPU 各分一半 ───────────────────────────
if JOB["shots"]:
    procs = []
    for i in range(n):
        procs.append(run("shots", i, n, i))
        time.sleep(20)  # 錯開載入，避免兩個程序同時吃滿記憶體
    wait_all(*procs)

# ── 4.（選用）關鍵鏡頭影片 ────────────────────────────────
if JOB.get("video_shots") and (time.time() - T0) / 60 < JOB.get("time_budget_min", 240):
    wait_all(run("video"))

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
    "errors": ERRORS,
    "elapsed_min": round((time.time() - T0) / 60, 1),
    "gpus": n,
}
with open(f"{OUT}/manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)
log(f"完成：{len(images)} 張圖、{len(videos)} 段影片，共 {manifest['elapsed_min']} 分鐘")
