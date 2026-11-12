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

UNETS = {  # 可選的主模型 UNet：lightning＝8 步快速版；其餘為完整品質版（要搭配 CFG）
    "lightning": f"{M}/lightning/sdxl_lightning_8step_unet.safetensors",
    "sdxl": f"{M}/unet_sdxl/unet/diffusion_pytorch_model.fp16.safetensors",
    "jugg": f"{M}/unet_jugg/unet/diffusion_pytorch_model.fp16.safetensors",
}

def load(ip=True, unet_key="lightning"):
    config = UNet2DConditionModel.load_config(f"{M}/sdxl", subfolder="unet")
    with torch.device("meta"):
        unet = UNet2DConditionModel.from_config(config)
    state = {k: v.to("cuda", torch.float16) for k, v in load_file(UNETS[unet_key]).items()}
    unet.load_state_dict(state, assign=True)
    del state
    vae = AutoencoderKL.from_pretrained(f"{M}/vae", torch_dtype=torch.float16)
    pipe = StableDiffusionXLPipeline.from_pretrained(f"{M}/sdxl", unet=unet, vae=vae, torch_dtype=torch.float16, variant="fp16")
    if unet_key == "lightning":
        pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    else:
        from diffusers import DPMSolverMultistepScheduler
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True,
                                                                 algorithm_type="dpmsolver++")
    if ip:
        pipe.load_ip_adapter(f"{M}/ip", subfolder="sdxl_models", weight_name="ip-adapter-plus-face_sdxl_vit-h.safetensors",
                             image_encoder_folder="models/image_encoder", torch_dtype=torch.float16)
    pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    return pipe

LORAS = dict(JOB.get("loras") or {})
if JOB.get("style_lora"):
    LORAS.setdefault("style", JOB["style_lora"])

def add_loras(pipe, names):
    for n in names:
        if n in LORAS:
            pipe.load_lora_weights(LORAS[n]["repo"], weight_name=LORAS[n]["file"], adapter_name=n)

def apply_loras(pipe, weights):
    names = [n for n, w in weights.items() if n in LORAS]
    if names:
        pipe.set_adapters(names, adapter_weights=[float(weights[n]) for n in names])

def gen_kw(r):  # 每張圖的算圖參數；r 可帶 steps / cfg / neg
    kw = dict(num_inference_steps=r.get("steps", JOB.get("steps", 8)), guidance_scale=r.get("cfg", 0.0))
    if kw["guidance_scale"] > 1 and r.get("neg"):
        kw["negative_prompt"] = r["neg"]
    return kw

def lora_weights(r, default_style=None):
    if r.get("loras") is not None:
        return dict(r["loras"])
    if "style" in LORAS:
        return {"style": r.get("lora", default_style if default_style is not None else LORAS["style"].get("scale", 0.8))}
    return {}


if mode == "refs":
    base_rr = JOB.get("ref_render") or {}
    todo = [c for c in JOB["characters"] if not (c.get("ref_b64") or c.get("ref_url"))][shard::nshards]
    groups = {}
    for c in todo:  # 每個角色可帶自己的算圖設定（畫風實驗用）
        rr = dict(base_rr, **(c.get("render") or {}))
        groups.setdefault(rr.get("unet", "lightning"), []).append((c, rr))
    pipe = None
    for ukey, items in groups.items():
        if pipe is not None:
            del pipe; torch.cuda.empty_cache()
        pipe = load(ip=False, unet_key=ukey)
        add_loras(pipe, sorted({n for _, rr in items for n in lora_weights(rr)}))
        for c, rr in items:
            apply_loras(pipe, lora_weights(rr))
            img = pipe(prompt=c["ref_prompt"], width=896, height=1152,
                       generator=torch.Generator("cuda").manual_seed(c["seed"]), **gen_kw(rr)).images[0]
            img.convert("RGB").resize((448, 576)).save(f"{OUT}/ref_{c['id']}.jpg", quality=92)
            log("定妝照", c["id"])

elif mode == "shots":
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
    _masks = {}

    def duo_masks_for(w, h):  # 每種畫面尺寸各自的左右分區
        if (w, h) not in _masks:
            left = Image.new("L", (w, h), 0); left.paste(255, (0, 0, w // 2 + w // 10, h))
            right = Image.new("L", (w, h), 0); right.paste(255, (w // 2 - w // 10, 0, w, h))
            m = maskproc.preprocess([left, right], height=h, width=w)
            _masks[(w, h)] = [m.reshape(1, m.shape[0], m.shape[2], m.shape[3])]
        return _masks[(w, h)]
    done = 0
    TIMES = {}
    groups = {}
    for s in mine:  # 依主模型分組，每組只載入一次
        groups.setdefault(s.get("unet", "lightning"), []).append(s)
    pipe = None
    for ukey, todo in groups.items():
      if pipe is not None:
        del pipe; torch.cuda.empty_cache()
      pipe = load(unet_key=ukey)
      add_loras(pipe, sorted({n for s in todo for n in lora_weights(s)}))
      log(f"主模型 {ukey}：{len(todo)} 張")
      for s in todo:
          if out_of_time():
              log("時間預算用完"); break
          try:
              t_shot = time.time()
              apply_loras(pipe, lora_weights(s))
              KW = gen_kw(s)
              scale = s.get("ip", JOB.get("ip_scale", 0.5))
              ids = [r for r in (s.get("refs") or [s.get("ref")]) if r in refs]
              gen = torch.Generator("cuda").manual_seed(s["seed"])
              sw, sh = s.get("w", W), s.get("h", H)  # 海報等特殊尺寸
              if len(ids) >= 2:  # 雙人鏡頭：左半邊鎖第一人、右半邊鎖第二人
                  pipe.set_ip_adapter_scale([[scale, scale]])
                  img = pipe(prompt=s["prompt"], width=sw, height=sh, ip_adapter_image=[[refs[ids[0]], refs[ids[1]]]],
                             cross_attention_kwargs={"ip_adapter_masks": duo_masks_for(sw, sh)}, generator=gen, **KW).images[0]
              else:
                  ref = refs.get(ids[0]) if ids else None
                  pipe.set_ip_adapter_scale(scale if ref is not None else 0.0)
                  img = pipe(prompt=s["prompt"], width=sw, height=sh, ip_adapter_image=ref if ref is not None else blank,
                             generator=gen, **KW).images[0]
              img.save(f"{OUT}/{s['key']}.png")
              TIMES[s["key"]] = round(time.time() - t_shot, 1)
              json.dump(TIMES, open(f"{OUT}/timing_shots_{shard}.json", "w"))
              done += 1
              if done % 10 == 0 or done == len(mine):
                  log(f"{done}/{len(mine)}")
          except Exception:
              traceback.print_exc()

elif mode == "video":
    # Wan 2.2 TI2V 5B 圖生影片。尺寸由工作單指定（預設橫式 832×480，長寬須為 32 的倍數），
    # 首幀先等比縮放再置中裁切，避免舊版硬拉成 480×832 直式造成變形。
    import numpy as np
    from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
    from diffusers.utils import export_to_video
    dt = torch.float16 if JOB.get("video_dtype", "fp16") == "fp16" else torch.bfloat16
    vae = AutoencoderKLWan.from_pretrained(JOB["video_model"], subfolder="vae", torch_dtype=torch.float32)
    vp = WanImageToVideoPipeline.from_pretrained(JOB["video_model"], vae=vae, torch_dtype=dt)
    vp.enable_model_cpu_offload()
    vp.set_progress_bar_config(disable=True)
    NEG = JOB.get("video_neg") or ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
                                   "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，"
                                   "手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")
    info = {}
    for v in JOB.get("video_shots", [])[shard::nshards]:
        src = f"{OUT}/{v['key']}.png"
        if out_of_time() or not os.path.exists(src):
            info[v["key"]] = {"error": "沒有首幀或時間用完"}; continue
        t1 = time.time()
        try:
            W, H = v.get("w", 832), v.get("h", 480)
            img = Image.open(src).convert("RGB")
            ow, oh = img.size
            k = max(W / ow, H / oh)
            img = img.resize((round(ow * k), round(oh * k)), Image.LANCZOS)
            l, t = (img.width - W) // 2, (img.height - H) // 2
            img = img.crop((l, t, l + W, t + H))
            nf = v.get("frames", 121)
            frames = vp(image=img, prompt=v["prompt"], negative_prompt=NEG, height=H, width=W, num_frames=nf,
                        num_inference_steps=v.get("steps", JOB.get("video_steps", 30)),
                        guidance_scale=v.get("cfg", 5.0),
                        generator=torch.Generator("cpu").manual_seed(v.get("seed", 0))).frames[0]
            arr = np.asarray(frames)
            bad = bool(np.isnan(arr).any() or arr.max() - arr.min() < 0.02)
            export_to_video(frames, f"{OUT}/{v['key']}.mp4", fps=v.get("fps", 24))
            info[v["key"]] = {"src_size": [ow, oh], "out_size": [W, H], "frames": nf, "fps": v.get("fps", 24),
                              "seconds": round(nf / v.get("fps", 24), 2), "gen_sec": round(time.time() - t1, 1),
                              "blank_or_nan": bad}
            log("影片", v["key"], info[v["key"]])
        except Exception as e:
            traceback.print_exc()
            info[v["key"]] = {"error": f"{type(e).__name__}: {str(e)[:300]}", "gen_sec": round(time.time() - t1, 1)}
        json.dump(info, open(f"{OUT}/video_info_{shard}.json", "w"), ensure_ascii=False)
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
_need_sdxl = bool(JOB.get("shots")) or any(not (c.get("ref_b64") or c.get("ref_url")) for c in JOB.get("characters", []))
for v in JOB.get("video_shots", []):  # 另一輪選好的首幀
    if v.get("image_url"):
        import urllib.request
        urllib.request.urlretrieve(v["image_url"], f"{OUT}/{v['key']}.png")
for name, (repo, patterns) in (RECIPE.items() if _need_sdxl else []):
    try:
        snapshot_download(repo, local_dir=f"{M}/{name}", allow_patterns=patterns, max_workers=16)
    except Exception:  # hf_transfer 不可用時退回一般下載
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        snapshot_download(repo, local_dir=f"{M}/{name}", allow_patterns=patterns, max_workers=16)
if _need_sdxl:
    hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_8step_unet.safetensors", local_dir=f"{M}/lightning")
_need = {s.get("unet", "lightning") for s in JOB.get("shots", [])} | {(JOB.get("ref_render") or {}).get("unet", "lightning")}
for key, repo in (("sdxl", "stabilityai/stable-diffusion-xl-base-1.0"), ("jugg", "RunDiffusion/Juggernaut-XL-v9")):
    if key in _need and _need_sdxl:
        hf_hub_download(repo, "unet/diffusion_pytorch_model.fp16.safetensors", local_dir=f"{M}/unet_{key}")
        log("下載主模型", key)
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
    if c.get("ref_url"):
        try:
            import urllib.request
            urllib.request.urlretrieve(c["ref_url"], f"{OUT}/ref_{c['id']}.jpg")
        except Exception as e:  # 下載失敗 → 當作沒有定妝照，現場重做
            log("⚠ 定妝照下載失敗，重做：", c["id"], e)
            c["ref_url"] = None
    elif c.get("ref_b64"):
        with open(f"{OUT}/ref_{c['id']}.jpg", "wb") as f:
            f.write(base64.b64decode(c["ref_b64"]))
with open(JOB_PATH, "w", encoding="utf-8") as f:  # 子程序讀的是這份，同步更新
    json.dump(JOB, f, ensure_ascii=False)
if any(not (c.get("ref_b64") or c.get("ref_url")) for c in JOB["characters"]):
    rp = []
    for i in range(n):  # 定妝照也分兩張 GPU
        rp.append(run("refs", i, n, i))
        time.sleep(20)
    wait_all(*rp)

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
images = sorted(os.path.basename(p) for p in glob.glob(f"{OUT}/ep*_s*.png") + glob.glob(f"{OUT}/poster_v*.png"))
videos = sorted(os.path.basename(p) for p in glob.glob(f"{OUT}/ep*_s*.mp4"))
wanted = {s["key"] for s in JOB["shots"]}
manifest = {
    "job_id": JOB["job_id"],
    "refs": [f"ref_{c['id']}.jpg" for c in JOB["characters"] if not (c.get("ref_b64") or c.get("ref_url"))
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
