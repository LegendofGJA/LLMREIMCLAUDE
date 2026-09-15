"""
llm_core.py — Koneksi provider AI vision (Kagiro / Bandel).

Menyediakan:
  - `PROVIDERS`       : konfigurasi provider dari st.secrets (bukan hardcoded).
  - `fetch_models`    : ambil daftar model dari endpoint /models (fallback default).
  - `ping_model`      : tes API "hidup" dengan KIRIM completion kecil BENERAN,
                        bukan cuma HTTP 200 pada /models — sehingga model yang
                        terdaftar tapi tidak merespons akan terdeteksi.
  - `prepare_image`   : resize + re-encode JPEG agar payload aman (menghindari
                        HTTP 413 Payload Too Large dari server).
  - `call_vision`     : panggil chat/completions dengan timeout panjang + retry
                        + backoff (menghindari read timeout).
"""

import base64
import io
import json
import os
import re
import time

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ─────────────────────────────────────────────────────────────────────────
# Konfigurasi provider (secrets, tidak pernah hardcoded)
# ─────────────────────────────────────────────────────────────────────────


def _get_secret(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


PROVIDERS = {
    "Kagiro": {
        "base_url": _get_secret("KAGIRO_BASE_URL", "https://api.kagiro.net/v1"),
        "api_key": _get_secret("KAGIRO_API_KEY"),
    },
    "Bandel": {
        "base_url": _get_secret("BANDEL_BASE_URL", "https://bandelbanget.xyz/v1"),
        "api_key": _get_secret("BANDEL_API_KEY"),
    },
}
# Provider tanpa API key di-skip (supaya tidak error saat salah satu belum di-set).
PROVIDERS = {k: v for k, v in PROVIDERS.items() if v["api_key"]}

# Fallback kalau endpoint /models tidak tersedia / kosong.
FALLBACK_MODELS = [
    "qwen-vl-max",
    "qwen2.5-vl-72b-instruct",
    "qwen-vl-max-latest",
    "gpt-4o",
    "gpt-4o-mini",
]


def _auth_headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['api_key']}"}


# ─────────────────────────────────────────────────────────────────────────
# Daftar model
# ─────────────────────────────────────────────────────────────────────────


# Pola id yang lazim untuk model VISION (bisa menerima input gambar).
# Jika endpoint /models tidak memberi flag "vision"/"image", kita filter
# berdasarkan nama id supaya dropdown hanya menampilkan model vision.
_VISION_HINTS = (
    "vl",
    "vision",
    "flash-vision",
    "gpt-4o",  # gpt-4o sebenarnya multimodal, pertahankan sebagai vision hint
    "gpt-4.1",
    "gemini",
    "claude",
    "minimax-vl",
    "internvl",
    "glm-4v",
    "glm-4.5v",
    "qwen-vl",
    "qwen2.5-vl",
    "kimi-k",
    "kimi-latest",
)


def _is_vision(model_id: str) -> bool:
    m = model_id.lower()
    return any(h in m for h in _VISION_HINTS)


def _fetch_models_raw(provider_name: str) -> list:
    """Ambil objek model mentah dari /models (list of dict). Gagal -> []."""
    cfg = PROVIDERS[provider_name]
    url = f"{cfg['base_url']}/models"
    try:
        r = requests.get(url, headers=_auth_headers(cfg), timeout=12)
        if r.status_code == 200:
            data = r.json()
            models = data.get("data", data)
            if isinstance(models, list):
                return [m for m in models if isinstance(m, dict) and m.get("id")]
    except Exception:
        pass
    return []


def fetch_models(provider_name: str) -> list:
    """Ambil daftar model id dari /models. Gagal -> fallback default."""
    ids = [m["id"] for m in _fetch_models_raw(provider_name)]
    return ids or list(FALLBACK_MODELS)


def fetch_vision_models(provider_name: str) -> list:
    """Daftar model VISION saja dari /models (cepat, tanpa uji gambar).

    Gabungan dari: flag `vision: true` DAN tebakan nama id (`_is_vision`).
    Dipakai sebagai daftar default sebelum pengguna menjalankan "Scan semua
    model". Flag provider tidak selalu akurat (Kagiro menandai Gemini
    `vision: false` padahal bisa baca gambar), dan sebaliknya nama id saja
    bisa keliru. Untuk daftar yang benar-benar terverifikasi, pakai
    `scan_vision_models()`."""
    raw = _fetch_models_raw(provider_name)

    if raw:
        enabled = [m for m in raw if m.get("enabled", True) is not False] or raw
        flagged = [
            m["id"]
            for m in enabled
            if m.get("vision") is True
        ]
        hinted = [m["id"] for m in enabled if _is_vision(m["id"])]
        merged = list(dict.fromkeys(flagged + hinted))
        return merged or [m["id"] for m in enabled]

    # /models gagal total -> fallback default.
    return list(FALLBACK_MODELS)


# ─────────────────────────────────────────────────────────────────────────
# Ping API "hidup" — kirim completion asli, bukan cuma HTTP 200
# ─────────────────────────────────────────────────────────────────────────


def ping_model(provider_name: str, model: str) -> tuple:
    """Kirim completion kecil beneran ke model dan laporkan hasilnya.

    Return (ok: bool, pesan: str, ms: int|None).
    """
    cfg = PROVIDERS[provider_name]
    url = f"{cfg['base_url']}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    headers = {**_auth_headers(cfg), "Content-Type": "application/json"}
    start = time.time()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        ms = int((time.time() - start) * 1000)
        if r.status_code == 200:
            data = r.json()
            content = ""
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = ""
            if content is None or str(content).strip() == "":
                return False, f"⚠️ HTTP 200 tapi balasan kosong ({ms} ms)", ms
            return True, f"✅ Model merespons beneran ({ms} ms)", ms
        else:
            return False, f"❌ Status {r.status_code}: {r.text[:120]}", ms
    except Exception as e:
        return False, f"❌ Gagal terhubung ({e})", None


def check_all_providers() -> dict:
    """Scan semua provider: daftar model (tanpa ping per model).

    Return {provider_name: {"models": [id vision...]}}
    """
    out = {}
    for name in PROVIDERS:
        out[name] = {"models": fetch_vision_models(name)}
    return out


# ─────────────────────────────────────────────────────────────────────────
# Siapkan gambar (hindari 413 + byte kecil)
# ─────────────────────────────────────────────────────────────────────────


def prepare_image(file_bytes: bytes, max_side: int = 1600, quality: int = 85) -> bytes:
    """Resize gambar ke max_side dan re-encode jadi JPEG sehingga payload
    base64-nya tetap kecil (teks struk tetap terbaca). Gambar asli TIDAK
    diubah (dipakai untuk PDF gabungan)."""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img)  # bakar rotasi agar OCR tidak salah orientasi
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            # Flatten alpha ke latar putih (bukan hitam) supaya teks tetap terbaca.
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    except Exception:
        return file_bytes
    w, h = img.size
    longest = max(w, h)
    if longest > max_side:
        ratio = max_side / float(longest)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# Panggil vision API
# ─────────────────────────────────────────────────────────────────────────


def _extract_json(text: str):
    """Ambil JSON dari respons model yang mungkin dibungkus markdown atau
    diapit teks lain. Melempar ValueError bila memang tidak ada JSON valid —
    ini yang menangkap provider 'nakal' yang membalas HTTP 200 tapi isinya
    bukan hasil OCR (mis. kalimat acak)."""
    cleaned = re.sub(r"```(?:json)?|```", "", str(text)).strip()
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        while start != -1:
            depth = 0
            for i in range(start, len(cleaned)):
                if cleaned[i] == opener:
                    depth += 1
                elif cleaned[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except (ValueError, TypeError):
                            break
            start = cleaned.find(opener, start + 1)

    snippet = cleaned[:200].replace("\n", " ")
    raise ValueError(f"Respons model bukan JSON valid. Raw output: '{snippet}'")


def _call_vision_once(
    provider_name: str,
    model: str,
    image_bytes: bytes,
    prompt: str,
    timeout: int,
    retries: int,
):
    """Satu percobaan ke satu (provider, model) dengan retry/backoff."""
    cfg = PROVIDERS[provider_name]
    url = f"{cfg['base_url']}/chat/completions"
    headers = {**_auth_headers(cfg), "Content-Type": "application/json"}

    prepared = prepare_image(image_bytes)
    b64 = base64.b64encode(prepared).decode("utf-8")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "temperature": 0.1,
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            try:
                raw_content = r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise ValueError(
                    f"Format respons tidak dikenali dari {provider_name}: {r.text[:200]}"
                )
            if not raw_content:
                raise ValueError("Respons model kosong (empty response).")
            return _extract_json(raw_content)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))  # backoff: 2s, 4s
    raise last_err if last_err else RuntimeError("call_vision gagal")


def call_vision(
    provider_name: str,
    model: str,
    image_bytes: bytes,
    prompt: str,
    timeout: int = 120,
    retries: int = 2,
    fallbacks: list | None = None,
) -> dict:
    """Kirim satu gambar + prompt ke model vision. Return objek JSON (dict/list).

    - image_bytes di-resize dulu (prepare_image) untuk hindari 413.
    - timeout panjang + retry/backoff untuk read timeout.
    - `fallbacks`: daftar (provider, model) cadangan yang dicoba otomatis bila
      provider terpilih gagal / balasannya bukan JSON valid. Ini membuat OCR
      tetap jalan walau provider terpilih ternyata rusak (mis. proxy yang
      membalas HTTP 200 tapi isinya bukan hasil OCR)."""
    plans = [(provider_name, model)]
    for fb in fallbacks or []:
        if fb not in plans:
            plans.append(tuple(fb))

    last_err = None
    for pname, m in plans:
        try:
            return _call_vision_once(pname, m, image_bytes, prompt, timeout, retries)
        except Exception as e:
            last_err = e
    raise last_err if last_err else RuntimeError("call_vision gagal")


# ─────────────────────────────────────────────────────────────────────────
# Uji kemampuan vision per model (probe)
# ─────────────────────────────────────────────────────────────────────────

_PROBE_PROMPT = (
    'Return ONLY this JSON and nothing else: '
    '{"seen":"<the exact text visible in the image>"}'
)


def _probe_image_bytes() -> bytes:
    """Gambar kecil berisi teks 'VISION-OK' untuk menguji apakah model
    benar-benar membaca gambar (bukan sekadar mengembalikan teks acak)."""
    img = Image.new("RGB", (480, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=64)
    except Exception:
        font = ImageFont.load_default()
    draw.text((30, 60), "VISION-OK", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def probe_vision(provider_name: str, model: str, timeout: int = 30) -> tuple:
    """Uji satu model: kirim gambar 'VISION-OK' lalu cek apakah model membacanya.

    Return (ok: bool, detail: str). Model yang tidak bisa/mau baca gambar
    (balas error, kosong, atau teks acak seperti provider rusak) -> ok=False."""
    try:
        parsed = _call_vision_once(
            provider_name, model, _probe_image_bytes(), _PROBE_PROMPT, timeout, 0
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"

    seen = str(parsed.get("seen", "")) if isinstance(parsed, dict) else str(parsed)
    if "VISION-OK" in seen.upper():
        return True, "OK"
    return False, f"tidak membaca gambar (balasan: {seen.strip()[:60]!r})"


def scan_vision_models(provider_name: str, timeout: int = 30, progress_cb=None) -> list:
    """Probe SEMUA model provider dan kembalikan hanya yang benar-benar bisa
    membaca gambar. Ini yang paling akurat karena tidak bergantung pada flag
    `vision` (yang ternyata sering salah).

    `progress_cb(i, total, model, ok, detail)` opsional untuk update UI.
    """
    raw = _fetch_models_raw(provider_name)
    models = [m["id"] for m in raw if m.get("enabled", True) is not False] or [
        m["id"] for m in raw
    ]
    if not models:
        models = list(FALLBACK_MODELS)

    working = []
    total = len(models)
    for i, model in enumerate(models):
        ok, detail = probe_vision(provider_name, model, timeout=timeout)
        if progress_cb:
            progress_cb(i + 1, total, model, ok, detail)
        if ok:
            working.append(model)
    return working
