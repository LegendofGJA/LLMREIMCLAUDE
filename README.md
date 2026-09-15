# Scan Reimburse Struk

Aplikasi Streamlit untuk mengekstrak data struk (bensin, parkir, Teazzi/drink)
dari foto + screenshot Flazz/e-money, mengurutkannya kronologis, mengisi
template Excel reimburse, dan menggabungkan foto struk + screenshot Flazz asli
menjadi satu PDF tanpa kompresi.

## Struktur file

```
reimburse-app/
├── app.py                          → UI utama (Streamlit)
├── llm_core.py                     → Provider config, health-check detail, vision API + retry/kompresi
├── excel_core.py                   → Aturan kategori, dedupe Flazz, sorting, tulis ke template Excel
├── pdf_core.py                     → Gabung PDF lossless (fix EXIF orientation invalid)
├── image_utils.py                  → GPS EXIF, reverse-geocode, kompresi khusus untuk API call
├── requirements.txt
├── README.md
├── .gitignore
├── FORM_REIMBURSE_template.xlsx
└── .streamlit/
    └── secrets.toml.example        → contoh format secrets (isi asli di secrets.toml, JANGAN commit)
```

## Setup lokal

```bash
git clone <url-repo-ini>
cd <folder-repo>
pip install -r requirements.txt

mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# lalu edit .streamlit/secrets.toml, isi KAGIRO_API_KEY / BANDEL_API_KEY dengan key asli

streamlit run app.py
```

## Deploy ke Streamlit Community Cloud

1. Push repo ini ke GitHub (`.streamlit/secrets.toml` otomatis ter-skip oleh `.gitignore`).
2. Buat app baru di [share.streamlit.io](https://share.streamlit.io), arahkan ke `app.py`.
3. Di **App → Settings → Secrets**, isi sama seperti `secrets.toml` lokal kamu.
4. Deploy.

## Yang baru / diperbaiki dari versi sebelumnya

- **File dipecah per tanggung jawab** (`llm_core.py`, `excel_core.py`, `pdf_core.py`, `image_utils.py`) alih-alih satu `app.py` raksasa, supaya lebih mudah di-maintain.
- **Health check provider lebih detail**: bukan cuma "HTTP 200", tapi membedakan connect timeout, read timeout, 401/403/404/429/5xx, HTTP 200 dengan body bukan JSON, dan HTTP 200 dengan daftar model kosong. Semua provider selalu dicek (tidak berhenti di provider pertama yang berhasil), jadi status tiap provider selalu tampil.
- **Fix `img2pdf.ExifOrientationError`**: beberapa foto HP menyimpan tag EXIF Orientation yang tidak valid (contoh nyata: bernilai `0`, padahal EXIF standar cuma mengenal 1–8). Fix-nya pakai `rotation=img2pdf.Rotation.ifvalid` — tag rotasi yang tidak valid diabaikan, byte gambar tetap 100% utuh (bukan direkompres).
- **Fix 413 Payload Too Large & timeout saat OCR**: sebelum dikirim ke API vision, gambar dikompres/di-resize dulu (khusus untuk panggilan API — file asli yang dipakai di PDF tidak disentuh). Kalau masih kena 413, otomatis dikompres lebih agresif dan dicoba ulang. Timeout juga di-retry otomatis sebelum dianggap gagal.
- **Excel & PDF sekarang independen**: kalau salah satu gagal dibuat, tombol download yang lain tetap muncul (sebelumnya satu error menghentikan seluruh script sehingga kedua tombol hilang).
- **Screenshot Flazz sekarang ikut masuk ke PDF gabungan**, tidak cuma foto struk fisik.
- **Fallback lokasi dari GPS EXIF foto**: kalau struk parkir tidak mencantumkan nama tempat, sistem coba baca koordinat GPS dari metadata foto lalu cari nama lokasinya via OpenStreetMap Nominatim (gratis, tanpa API key). Kalau foto tidak punya GPS atau lookup gagal, tetap `-` untuk diisi manual.

## Sebelum push ke publik — soal API key & data pribadi

Kalau kamu memilih untuk tetap memakai versi kode/template lama yang menyimpan
API key atau data pribadi langsung di file, itu keputusanmu. Untuk versi ini
(refactor `st.secrets`), pastikan:

- `secrets.toml` (isi asli) TIDAK ikut ter-commit — cek dengan `git status` sebelum push pertama.
- Kalau API key sempat "bocor" di versi lama yang sudah kamu publikasikan, sebaiknya tetap **rotate/revoke** key tersebut dari dashboard Kagiro & Bandel.
