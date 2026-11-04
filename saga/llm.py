"""LLM 呼叫層：預設用 Google Gemini 免費額度，--mock 時產生假資料供測試。"""
from __future__ import annotations

import json
import os
import re
import time

import requests

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_URL = GEMINI_BASE + "/models/{model}:generateContent"
# 查不到模型清單時的備援
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview", "gemini-2.5-flash"]
_EXCLUDE = ("image", "tts", "audio", "live", "embed", "vision", "computer", "robotics", "aqa", "learnlm", "nano")
_model_cache: list[str] | None = None


class LLMError(RuntimeError):
    pass


def _rank(name: str) -> tuple:
    """越前面越好：Flash（非 lite）> Flash-lite > Pro；新版本優先；正式版優先於預覽版。"""
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    ver = float(m.group(1)) if m else (0.5 if "latest" in name else 0.0)
    tier = 0 if ("flash" in name and "lite" not in name) else 1 if "flash" in name else 2
    unstable = any(t in name for t in ("preview", "exp"))
    return (tier, -ver, unstable, name)


def _available_models(key: str) -> list[str]:
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    names: list[str] = []
    try:
        r = requests.get(f"{GEMINI_BASE}/models", params={"key": key, "pageSize": 1000}, timeout=30)
        r.raise_for_status()
        for m in r.json().get("models", []):
            n = m["name"].removeprefix("models/")
            if ("generateContent" in m.get("supportedGenerationMethods", [])
                    and n.startswith("gemini") and not any(x in n for x in _EXCLUDE)):
                names.append(n)
    except requests.RequestException as e:
        print(f"  查詢模型清單失敗（{e}），改用備援清單", flush=True)
    names = sorted(set(names), key=_rank) or FALLBACK_MODELS
    pinned = os.environ.get("GEMINI_MODEL")
    _model_cache = ([pinned] if pinned else []) + [n for n in names if n != pinned]
    print(f"  可用模型（依優先序）：{', '.join(_model_cache[:6])}", flush=True)
    return _model_cache


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.loads(text[start:])


def _gemini(prompt: str, temperature: float) -> dict:
    global _model_cache
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("缺少 GEMINI_API_KEY（到 https://aistudio.google.com/apikey 免費申請）")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    errors = []
    for model in list(_available_models(key))[:8]:
        for attempt in range(3):
            try:
                r = requests.post(GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=240)
            except requests.RequestException as e:
                errors.append(f"{model}: {e}")
                time.sleep(5)
                continue
            if r.ok:
                try:
                    data = r.json()
                    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                    result = _extract_json(text)
                except (json.JSONDecodeError, KeyError, IndexError) as e:
                    errors.append(f"{model}: 回傳格式錯誤 {e}")
                    continue
                # 這個模型能用，之後優先用它
                _model_cache = [model] + [m for m in _model_cache if m != model]
                return result
            msg = r.text[:300].replace("\n", " ")
            errors.append(f"{model}: HTTP {r.status_code} {msg}")
            print(f"  {model} → HTTP {r.status_code}：{msg[:160]}", flush=True)
            if r.status_code in (400, 403, 404):
                break  # 模型不存在或金鑰不能用 → 換下一個
            if r.status_code == 429 and ("limit: 0" in msg or "PerDay" in msg or "per day" in msg.lower()):
                break  # 這個模型沒有免費額度或今天用完 → 換下一個
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(15 * (attempt + 1))
                continue
            break
    raise LLMError("所有 Gemini 模型都失敗：\n" + "\n".join(errors[-8:]))


def generate_json(prompt: str, *, kind: str, mock: bool = False, context: dict | None = None,
                  temperature: float = 0.9) -> dict:
    if mock:
        from . import mock as _mock
        return _mock.fake(kind, context or {})
    return _gemini(prompt, temperature)
