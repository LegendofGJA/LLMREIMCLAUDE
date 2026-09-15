# Scan Reimburse Struk

Aplikasi Streamlit untuk mengekstrak data struk (bensin, parkir, Teazzi/drink)
dari foto, mengurutkannya kronologis berdasarkan tanggal transaksi, mengisi
template Excel reimburse, dan menggabungkan foto struk asli menjadi satu PDF
tanpa kompresi.

## Setup lokal

```bash
git clone <url-repo-ini>
cd <folder-repo>
pip install -r requirements.txt
```

Buat file secrets (jangan pernah commit file ini — sudah masuk `.gitignore`):

```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Lalu isi `.streamlit/secrets.toml` dengan API key asli kamu:

```toml
KAGIRO_API_KEY = "mk-...."
BANDEL_API_KEY = "sk-qwen-...."
```

Jalankan:

```bash
streamlit run app.py
```

## Deploy ke Streamlit Community Cloud

1. Push repo ini ke GitHub (tanpa `.streamlit/secrets.toml` — otomatis ter-skip oleh `.gitignore`).
2. Buat app baru di [share.streamlit.io](https://share.streamlit.io), arahkan ke `app.py`.
3. Di menu **App → Settings → Secrets**, tempel isi yang sama seperti `secrets.toml` kamu.
4. Deploy.

## Sebelum push pertama kali — pastikan key lama sudah dicabut

File `app.py` versi awal sempat menyimpan API key langsung di kode (hardcoded)
sebelum direfaktor ke `st.secrets`. Kalau kamu sempat menjalankan/menyimpan
versi itu di folder lain, atau riwayat commit lokal git kamu ada yang memuat
key tersebut:

- **Revoke / rotate** kedua API key (Kagiro & Bandel) dari dashboard masing-masing provider, buat key baru.
- Pastikan `git log -p` di repo ini tidak memuat key lama sebelum di-push (repo baru dari file-file ini seharusnya bersih, tapi cek dulu kalau kamu menggabungkan dengan folder kerja lama).

## Struktur file

- `app.py` — aplikasi utama (UI Streamlit + orkestrasi proses)
- `llm_core.py` — koneksi provider (Kagiro/Bandel), ping model sungguhan, call vision (retry + timeout)
- `extract_core.py` — prompt + aturan ekstraksi per kategori, dedup Flazz vs struk parkir, skip Top Up
- `excel_core.py` — isi data ke template Excel (header, periode E6/E7, total)
- `pdf_core.py` — gabung gambar asli jadi satu PDF tanpa kompresi
- `gps_core.py` — baca lokasi GPS dari EXIF foto struk
- `requirements.txt` — dependency Python
- `FORM_REIMBURSE_template.xlsx` — template Excel yang diisi otomatis
- `.streamlit/secrets.toml.example` — contoh format secrets (bukan secrets asli)
- `.gitignore` — mengecualikan `secrets.toml` dan file lain dari repo
