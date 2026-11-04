# ─────────────────────────────────────────────────────────────
# 一次性：在 Kaggle 上把所有模型與套件下載好，存成這支 notebook 的輸出。
# 之後每次算圖都把它「掛載」進來，不必再花 10 分鐘以上重新下載。
# 改了模型配方就把 render.py 的 MODELS_VERSION 加一，會自動重建。
# ─────────────────────────────────────────────────────────────
import json
import os
import subprocess
import sys
import time

T0 = time.time()
OUT = "/kaggle/working"
M = os.path.join(OUT, "models")
os.makedirs(M, exist_ok=True)


def log(*a):
    print(f"[{(time.time() - T0) / 60:5.1f}m]", *a, flush=True)


subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "huggingface_hub"], check=True)
from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

RECIPE = {
    "sdxl": ("stabilityai/stable-diffusion-xl-base-1.0", [
        "model_index.json", "scheduler/*",
        "text_encoder/config.json", "text_encoder/model.fp16.safetensors",
        "text_encoder_2/config.json", "text_encoder_2/model.fp16.safetensors",
        "tokenizer/*", "tokenizer_2/*",
        "unet/config.json", "unet/diffusion_pytorch_model.fp16.safetensors",
    ]),
    "vae": ("madebyollin/sdxl-vae-fp16-fix", ["config.json", "diffusion_pytorch_model.safetensors"]),
    "ip": ("h94/IP-Adapter", [
        "sdxl_models/ip-adapter_sdxl_vit-h.safetensors",
        "models/image_encoder/config.json", "models/image_encoder/model.safetensors",
    ]),
}
for name, (repo, patterns) in RECIPE.items():
    log("下載", repo)
    snapshot_download(repo, local_dir=os.path.join(M, name), allow_patterns=patterns)

log("下載 SDXL-Lightning 8 步 LoRA")
hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_8step_lora.safetensors", local_dir=os.path.join(M, "lightning"))

log("打包離線套件")
# --no-deps：只抓這幾個純 Python 套件，torch 等大型依賴 Kaggle 映像檔本來就有
subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "-d", os.path.join(OUT, "wheels"),
                "diffusers>=0.35", "peft>=0.15", "huggingface_hub", "ftfy"], check=True)

# 移除 huggingface 的快取暫存，避免占用輸出空間
for root, dirs, files in os.walk(M):
    for d in list(dirs):
        if d == ".cache":
            subprocess.run(["rm", "-rf", os.path.join(root, d)])

size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs)
with open(os.path.join(OUT, "saga_models.json"), "w") as f:
    json.dump({"recipe": {k: v[0] for k, v in RECIPE.items()}, "bytes": size}, f)
log(f"完成，共 {size / 1e9:.1f} GB")
