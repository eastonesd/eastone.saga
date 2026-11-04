# ─────────────────────────────────────────────────────────────
# 這支程式會被打包送到 Kaggle 的免費 GPU 上執行（不在本機跑）。
# __JOB_B64__ 會由 saga/render.py 換成這次的算圖工作清單。
# ─────────────────────────────────────────────────────────────
import base64
import gc
import json
import os
import subprocess
import sys
import time
import traceback

T0 = time.time()
JOB = json.loads(base64.b64decode("__JOB_B64__").decode("utf-8"))
OUT = "/kaggle/working"
os.makedirs(OUT, exist_ok=True)
manifest = {"job_id": JOB["job_id"], "refs": [], "images": [], "videos": [], "errors": [], "partial": False}


def log(*a):
    print(f"[{(time.time() - T0) / 60:6.1f}m]", *a, flush=True)


def save_manifest():
    manifest["elapsed_min"] = round((time.time() - T0) / 60, 1)
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def out_of_time():
    return (time.time() - T0) / 60 > JOB.get("time_budget_min", 480)


save_manifest()
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U",
                "diffusers>=0.34", "transformers", "accelerate", "ftfy"], check=False)

import torch  # noqa: E402
from diffusers import AutoencoderKL, StableDiffusionXLPipeline  # noqa: E402
from PIL import Image  # noqa: E402

DEV = "cuda"
log("GPU:", torch.cuda.get_device_name(0))

# ── 1. 載入 SDXL（fp16 VAE 修正版，避免 T4 上出現黑圖）
vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
try:
    pipe = StableDiffusionXLPipeline.from_pretrained(JOB["model"], vae=vae, torch_dtype=torch.float16,
                                                     variant="fp16", use_safetensors=True)
except Exception:
    pipe = StableDiffusionXLPipeline.from_pretrained(JOB["model"], vae=vae, torch_dtype=torch.float16,
                                                     use_safetensors=True)
pipe = pipe.to(DEV)
pipe.enable_vae_tiling()
NEG = JOB.get("negative_prompt", "")
STEPS = JOB.get("steps", 28)
CFG = JOB.get("cfg", 6.0)

# ── 2. 角色定妝照（只做還沒有的角色）
refs = {}
for c in JOB["characters"]:
    path = os.path.join(OUT, f"ref_{c['id']}.jpg")
    if c.get("ref_b64"):
        with open(path, "wb") as f:
            f.write(base64.b64decode(c["ref_b64"]))
    else:
        log("定妝照：", c["id"])
        img = pipe(prompt=c["ref_prompt"], negative_prompt=NEG, width=896, height=1152,
                   num_inference_steps=STEPS, guidance_scale=CFG,
                   generator=torch.Generator(DEV).manual_seed(c["seed"])).images[0]
        img.convert("RGB").resize((448, 576)).save(path, quality=92)
        manifest["refs"].append(os.path.basename(path))
    refs[c["id"]] = Image.open(path).convert("RGB")
save_manifest()

# ── 3. 分鏡圖：IP-Adapter 用定妝照鎖住角色長相
pipe.load_ip_adapter("h94/IP-Adapter", subfolder="sdxl_models", weight_name="ip-adapter_sdxl.bin")
blank = Image.new("RGB", (448, 576), (128, 128, 128))
W, H = JOB.get("width", 768), JOB.get("height", 1344)
for i, s in enumerate(JOB["shots"]):
    if out_of_time():
        manifest["partial"] = True
        log("時間預算用完，下次執行會接續")
        break
    name = f"{s['key']}.png"
    try:
        ref = refs.get(s.get("ref"))
        pipe.set_ip_adapter_scale(JOB.get("ip_scale", 0.55) if ref is not None else 0.0)
        img = pipe(prompt=s["prompt"], negative_prompt=NEG, width=W, height=H,
                   ip_adapter_image=ref if ref is not None else blank, num_inference_steps=STEPS, guidance_scale=CFG,
                   generator=torch.Generator(DEV).manual_seed(s["seed"])).images[0]
        img.save(os.path.join(OUT, name))
        manifest["images"].append(name)
        log(f"圖 {i + 1}/{len(JOB['shots'])}", s["key"])
    except Exception as e:
        manifest["errors"].append(f"{s['key']}: {e}")
        traceback.print_exc()
    if i % 5 == 0:
        save_manifest()
save_manifest()

# ── 4.（實驗性）關鍵鏡頭做成真影片
video_shots = [v for v in JOB.get("video_shots", []) if f"{v['key']}.png" in manifest["images"]]
if video_shots and not out_of_time():
    del pipe
    gc.collect()
    torch.cuda.empty_cache()
    try:
        from diffusers import WanImageToVideoPipeline
        from diffusers.utils import export_to_video

        log("載入影片模型：", JOB["video_model"])
        vp = WanImageToVideoPipeline.from_pretrained(JOB["video_model"], torch_dtype=torch.float16)
        vp.enable_model_cpu_offload()
        for v in video_shots:
            if out_of_time():
                manifest["partial"] = True
                break
            try:
                img = Image.open(os.path.join(OUT, f"{v['key']}.png")).convert("RGB").resize((480, 832))
                frames = vp(image=img, prompt=v["prompt"], negative_prompt=NEG, height=832, width=480,
                            num_frames=49, num_inference_steps=JOB.get("video_steps", 20),
                            guidance_scale=5.0).frames[0]
                name = f"{v['key']}.mp4"
                export_to_video(frames, os.path.join(OUT, name), fps=16)
                manifest["videos"].append(name)
                log("影片", v["key"])
            except Exception as e:
                manifest["errors"].append(f"video {v['key']}: {e}")
                traceback.print_exc()
            save_manifest()
    except Exception as e:
        manifest["errors"].append(f"video model: {e}")
        traceback.print_exc()

save_manifest()
log("完成", json.dumps({k: len(v) if isinstance(v, list) else v for k, v in manifest.items()}, ensure_ascii=False))
