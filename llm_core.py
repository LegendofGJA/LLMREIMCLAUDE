"""
llm_core.py
===========
Konfigurasi provider AI vision (Kagiro / Bandel), health-check yang detail
(bukan cuma HTTP 200 -- membedakan timeout, 401/403/404/429/5xx, model kosong,
dll), dan pemanggilan API ekstraksi struk/screenshot dengan retry otomatis +
kompresi gambar (supaya tidak kena 413 Payload Too Large atau timeout).
"""

import base64
import json
import os
import re
import time

import requests
import streamlit as st

import image_utils

# ─────────────────────────────────────────────────────────────────────────
# KONFIGURASI PROVIDER
# API key TIDAK ditulis langsung di kode. Diambil dari st.secrets (file
# .streamlit/secrets.toml lokal, atau menu "Secrets" di Streamlit Community
# Cloud) dengan fallback ke environment variable.
# ─────────────────────────────────────────────────────────────────────────


def _get_secret(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def load_providers() -> dict:
    providers = {
        "Kagiro": {
            "base_url": _get_secret("KAGIRO_BASE_URL", "https://api.kagiro.net/v1"),
            "api_key": _get_secret("KAGIRO_API_KEY"),
        },
        "Bandel": {
            "base_url": _get_secret("BANDEL_BASE_URL", "https://bandelbanget.xyz/v1"),
            "api_key": _get_secret("BANDEL_API_KEY"),
        },
    }
    # provider tanpa API key otomatis di-skip supaya tidak muncul error
    # membingungkan kalau salah satu provider memang belum dikonfigurasi.
    return {name: cfg for name, cfg in providers.items() if cfg["api_key"]}


PROVIDERS = load_providers()


# ─────────────────────────────────────────────────────────────────────────
# HEALTH CHECK -- detail, bukan cuma "HTTP 200"
# ─────────────────────────────────────────────────────────────────────────


def _filter_vision_models(models_raw: list) -> list:
    """Ambil HANYA model yang mendukung input gambar (vision=true) dan tidak
    dinonaktifkan provider (enabled bukan false). Ini mencegah pengguna memilih
    model text-only untuk OCR (mis. "auto" di Bandel) yang pasti gagal karena
    gambar diabaikan. Kalau provider tidak mencantumkan flag vision sama sekali,
    kembalikan daftar kosong supaya pemanggil memakai semua model sebagai
    fallback."""
    if not isinstance(models_raw, list):
        return []
    vision = []
    for m in models_raw:
        if not isinstance(m, dict) or "id" not in m:
            continue
        if m.get("vision") is True and m.get("enabled", True) is not False:
            vision.append(m["id"])
    return vision


def test_ping_and_get_models(provider_name: str) -> dict:
    """Cek satu provider dan kembalikan diagnosis lengkap:
    {"ok": bool, "status": "<pesan detail>", "models": [...]}"""
    cfg = PROVIDERS[provider_name]
    url = f"{cfg['base_url']}/models"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}

    t0 = time.time()
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except requests.exceptions.ConnectTimeout:
        return {"ok": False, "status": "❌ Connect timeout — server tidak merespons handshake sama sekali.", "models": []}
    except requests.exceptions.ReadTimeout:
        return {"ok": False, "status": "❌ Read timeout — server konek tapi tidak balas dalam waktu wajar.", "models": []}
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "status": f"❌ Gagal konek ({type(e).__name__}) — cek base_url / DNS / server sedang down.", "models": []}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "status": f"❌ Request error: {e}", "models": []}

    elapsed = time.time() - t0
    code = response.status_code

    if code == 200:
        try:
            data = response.json()
        except ValueError:
            return {"ok": False, "status": "⚠️ HTTP 200 tapi respons bukan JSON valid — base_url mungkin tidak mengarah ke endpoint /models yang benar.", "models": []}
        models_raw = data.get("data", data) if isinstance(data, dict) else data
        all_models = (
            [m for m in models_raw if isinstance(m, dict) and "id" in m]
            if isinstance(models_raw, list)
            else []
        )
        if not all_models:
            return {"ok": False, "status": f"⚠️ HTTP 200 ({elapsed:.1f}s) tapi daftar model kosong — API key mungkin tidak punya akses ke model apa pun.", "models": []}
        model_list = _filter_vision_models(all_models) or [m["id"] for m in all_models]
        return {"ok": True, "status": f"✅ Terhubung, {len(model_list)} model vision tersedia ({elapsed:.1f}s).", "models": model_list}

    if code == 401:
        return {"ok": False, "status": "❌ 401 Unauthorized — API key salah atau sudah kedaluwarsa.", "models": []}
    if code == 403:
        return {"ok": False, "status": "❌ 403 Forbidden — API key tidak punya izin akses endpoint ini.", "models": []}
    if code == 404:
        return {"ok": False, "status": "❌ 404 Not Found — base_url kemungkinan salah, atau provider ini tidak punya endpoint /models.", "models": []}
    if code == 429:
        return {"ok": False, "status": "⚠️ 429 Too Many Requests — rate limit tercapai, coba lagi sebentar lagi.", "models": []}
    if 500 <= code < 600:
        return {"ok": False, "status": f"❌ {code} Server Error — masalah di sisi provider, bukan konfigurasi aplikasi ini.", "models": []}
    return {"ok": False, "status": f"⚠️ HTTP {code} — kode status tidak dikenali.", "models": []}


def check_all_providers() -> dict:
    """Cek SEMUA provider (tidak short-circuit di provider pertama yang
    berhasil), supaya status setiap provider selalu bisa ditampilkan."""
    return {name: test_ping_and_get_models(name) for name in PROVIDERS}


def pick_best_provider(results: dict):
    """Pilih provider pertama yang 'ok' dari hasil check_all_providers()."""
    for name, r in results.items():
        if r["ok"]:
            return name
    return next(iter(results), None)


# ─────────────────────────────────────────────────────────────────────────
# EKSTRAKSI VISION (struk fisik & screenshot Flazz)
# ─────────────────────────────────────────────────────────────────────────


def encode_image(file_bytes: bytes) -> str:
    return base64.b64encode(file_bytes).decode("utf-8")


EXTRACTION_PROMPT = """Kamu adalah sistem OCR untuk struk belanja Indonesia.
Analisis gambar struk ini dan kembalikan HANYA JSON murni (tanpa markdown, tanpa teks tambahan) dengan struktur persis berikut:

{
  "date": "YYYY-MM-DD",
  "type": "bensin" | "parkir" | "drink",
  "nominal": <angka total akhir struk, tanpa titik/koma/Rp>,
  "fuel_type": "<nama jenis BBM apa adanya di struk, contoh: Pertalite / Pertamax / Pertamax Turbo, atau null jika bukan bensin>",
  "liters": <jumlah liter sebagai angka, atau null jika tidak ada / bukan bensin>,
  "drink_name": "<jenis/menu minuman yang dibeli, atau null jika bukan drink>",
  "outlet_name": "<nama toko/outlet, contoh Teazzi, atau null jika bukan drink>",
  "location_name": "<nama tempat/lokasi parkir, atau null jika bukan parkir. Jika parkir tapi nama tempat tidak tertera, isi dengan '-'>"
}

Aturan klasifikasi "type":
- Struk pom bensin / SPBU -> "bensin"
- Struk parkir -> "parkir"
- Struk Teazzi atau minuman lain -> "drink"

Ambil "nominal" sebagai TOTAL AKHIR yang benar-benar dibayar pada struk.
Tanggal WAJIB diambil dari tanggal transaksi yang tertera di struk, bukan diasumsikan.
Kembalikan JSON murni saja, tidak ada teks lain sebelum atau sesudahnya."""

EXTRACTION_PROMPT_FLAZZ = """Kamu adalah sistem OCR untuk screenshot riwayat transaksi kartu e-money (Flazz/BCA dsb).
Gambar ini berisi DAFTAR beberapa transaksi sekaligus (bukan satu struk tunggal). Untuk SETIAP baris
transaksi yang terlihat di gambar -- termasuk yang terpotong di ujung atas/bawah layar selama tanggalnya
masih terbaca -- ekstrak:

- date: gabungkan tanggal-bulan-tahun yang ditampilkan terpisah di layar, hasil akhir format YYYY-MM-DD
- type: "Parking" jika baris berjudul Parking/Parkir, "Top Up" jika baris top up, atau nama kategori lain apa adanya jika berbeda
- nominal: angka rupiah transaksi tersebut, tanpa titik/koma/IDR/Rp

Kembalikan HANYA JSON array murni (tanpa markdown, tanpa teks tambahan), contoh:
[{"date": "2026-07-10", "type": "Parking", "nominal": 8000}, {"date": "2026-07-09", "type": "Parking", "nominal": 7000}]

ABAIKAN baris "Balance" / info saldo di bagian atas layar, itu bukan transaksi.
Kembalikan array JSON murni saja, tidak ada teks lain sebelum atau sesudahnya."""


def _vision_chat_raw(provider_name: str, model: str, image_bytes: bytes, prompt: str, timeout: int = 60, max_retries: int = 2) -> str:
    """Panggil chat completion vision dengan gambar TERKOMPRESI (khusus untuk
    API call ini saja -- tidak memengaruhi file asli yang dipakai di PDF).
    Otomatis retry saat timeout, dan mengompres lebih agresif kalau kena 413."""
    cfg = PROVIDERS[provider_name]
    api_url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    compressed = image_utils.compress_for_api(image_bytes)
    last_err = None

    for attempt in range(max_retries + 1):
        base64_img = encode_image(compressed)
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}},
                    ],
                }
            ],
            "temperature": 0.1,
        }
        try:
            res = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            res.raise_for_status()
            message = res.json()["choices"][0]["message"]
            content_text = (message.get("content") or "").strip()
            if not content_text:
                content_text = (message.get("reasoning_content") or "").strip()
            if not content_text:
                raise ValueError("Provider mengembalikan respons kosong (tidak ada konten teks).")
            return content_text
        except requests.exceptions.HTTPError as e:
            last_err = e
            status_code = e.response.status_code if e.response is not None else None
            if status_code == 413 and attempt < max_retries:
                # masih kegedean -> kompres lebih agresif lalu coba lagi
                compressed = image_utils.compress_for_api(image_bytes, max_dim=1000, quality=55, max_bytes=700_000)
                continue
            raise
        except requests.exceptions.Timeout as e:
            last_err = e
            if attempt < max_retries:
                continue  # retry sekali lagi sebelum menyerah
            raise
        except requests.exceptions.RequestException as e:
            last_err = e
            raise

    raise last_err


def _extract_json(text: str):
    """Ambil JSON dari respons model yang mungkin dibungkus markdown atau
    diapit teks lain. Melempar ValueError yang jelas kalau memang tidak ada
    JSON sama sekali -- ini yang menangkap provider 'nakal' yang membalas
    HTTP 200 tapi isinya bukan hasil OCR (mis. kalimat acak)."""
    cleaned = re.sub(r"```(?:json)?", "", str(text)).strip()
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
                            return json.loads(cleaned[start:i + 1])
                        except (ValueError, TypeError):
                            break
            start = cleaned.find(opener, start + 1)

    snippet = cleaned[:200].replace("\n", " ")
    raise ValueError(f"Respons model bukan JSON valid — gambar kemungkinan tidak diproses. Respons: {snippet!r}")


def _provider_plans(provider_name: str, model: str, results: dict | None) -> list:
    """Susun urutan (provider, model) yang akan dicoba: provider terpilih lebih
    dulu, lalu provider lain yang sehat memakai model vision pertamanya. Ini
    yang membuat OCR tetap jalan walau provider terpilih ternyata rusak (mis.
    proxy yang membalas HTTP 200 tapi isinya bukan hasil OCR)."""
    plans = []
    if provider_name and model:
        plans.append((provider_name, model))
    for pname, r in (results or {}).items():
        if pname == provider_name or not r.get("ok"):
            continue
        models = r.get("models") or []
        if models:
            plans.append((pname, models[0]))
    return plans


def call_vision_api(provider_name: str, model: str, image_bytes: bytes, results: dict | None = None) -> dict:
    """Ekstraksi satu foto struk fisik (bensin/parkir/drink) -> satu dict transaksi.
    Mencoba provider terpilih dulu, lalu fallback ke provider sehat lainnya
    kalau responsnya tidak valid (lihat `_provider_plans`)."""
    plans = _provider_plans(provider_name, model, results) or [(provider_name, model)]
    last_err = None
    for pname, m in plans:
        try:
            content_text = _vision_chat_raw(pname, m, image_bytes, EXTRACTION_PROMPT)
            data = _extract_json(content_text)
            if not isinstance(data, dict):
                raise ValueError("Respons OCR bukan objek JSON — gambar kemungkinan tidak diproses model.")
            return data
        except Exception as e:  # coba provider berikutnya, jangan langsung menyerah
            last_err = e
    raise last_err


def call_vision_api_flazz(provider_name: str, model: str, image_bytes: bytes, results: dict | None = None) -> list:
    """Ekstraksi satu screenshot riwayat Flazz/e-money -> list beberapa transaksi sekaligus."""
    plans = _provider_plans(provider_name, model, results) or [(provider_name, model)]
    last_err = None
    for pname, m in plans:
        try:
            content_text = _vision_chat_raw(pname, m, image_bytes, EXTRACTION_PROMPT_FLAZZ)
            parsed = _extract_json(content_text)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        return v
                return []
            if isinstance(parsed, list):
                return parsed
            raise ValueError("Respons OCR Flazz bukan JSON array.")
        except Exception as e:
            last_err = e
    raise last_err
