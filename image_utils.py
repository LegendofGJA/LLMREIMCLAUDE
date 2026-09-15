"""
image_utils.py
==============
Utilitas gambar yang dipakai bersama:
  - compress_for_api(): kompres/resize gambar HANYA untuk dikirim ke API vision
    (supaya tidak kena 413 Payload Too Large / timeout). File asli yang dipakai
    untuk PDF/Excel TIDAK disentuh oleh fungsi ini.
  - extract_gps(): baca koordinat GPS dari EXIF foto (kalau ada).
  - reverse_geocode(): ubah koordinat GPS jadi nama lokasi lewat OpenStreetMap
    Nominatim (gratis, tanpa API key), dipakai sebagai fallback description
    untuk struk/foto parkir yang tidak mencantumkan nama tempat.
"""

import io
import time

import requests
from PIL import Image, ImageOps


def compress_for_api(image_bytes: bytes, max_dim: int = 1600, quality: int = 82, max_bytes: int = 1_500_000) -> bytes:
    """Resize + kompres gambar sebelum dikirim ke provider vision API.
    Tujuannya cuma mempercepat & menghindari 413 Payload Too Large -- TIDAK
    memengaruhi file yang dipakai untuk PDF gabungan (itu tetap pakai
    image_bytes asli, lihat pdf_core.py)."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)  # bakar rotasi supaya OCR tidak salah orientasi
        if img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        scale = min(1.0, max_dim / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        q = quality
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()

        while len(data) > max_bytes and q > 35:
            q -= 10
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=q, optimize=True)
            data = buf.getvalue()

        return data
    except Exception:
        # kalau gagal diproses (format aneh dsb), kirim saja bytes asli --
        # lebih baik gagal di panggilan API dengan pesan jelas daripada
        # gagal senyap di sini.
        return image_bytes


def _dms_to_decimal(dms, ref) -> float:
    degrees, minutes, seconds = dms
    decimal = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def extract_gps(image_bytes: bytes):
    """Kembalikan (lat, lon) dari EXIF foto, atau None kalau tidak ada GPS."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif = img.getexif()
        if not exif:
            return None
        gps_ifd = exif.get_ifd(0x8825)  # GPS IFD tag
        if not gps_ifd:
            return None
        lat = gps_ifd.get(2)
        lat_ref = gps_ifd.get(1)
        lon = gps_ifd.get(4)
        lon_ref = gps_ifd.get(3)
        if not (lat and lon and lat_ref and lon_ref):
            return None
        return (_dms_to_decimal(lat, lat_ref), _dms_to_decimal(lon, lon_ref))
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float, cache: dict | None = None, timeout: int = 8):
    """Ubah koordinat jadi nama lokasi ringkas via OpenStreetMap Nominatim.
    `cache` (dict) opsional supaya koordinat yang sama tidak query berulang
    kali dalam satu sesi proses -- juga membantu menaati rate limit Nominatim
    (maks. 1 request/detik)."""
    key = (round(lat, 5), round(lon, 5))
    if cache is not None and key in cache:
        return cache[key]

    label = None
    try:
        headers = {"User-Agent": "ReimburseStrukApp/1.0 (personal use, contact via app owner)"}
        params = {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1}
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        name = data.get("name") or ""
        addr = data.get("address", {}) or {}
        parts = [
            p
            for p in [
                name,
                addr.get("road"),
                addr.get("suburb") or addr.get("village") or addr.get("city_district"),
            ]
            if p
        ]
        label = ", ".join(dict.fromkeys(parts)) if parts else data.get("display_name")
    except Exception:
        label = None
    finally:
        time.sleep(1)  # hormati rate limit Nominatim (maks 1 request/detik)

    if cache is not None:
        cache[key] = label
    return label
