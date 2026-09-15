"""
app.py
======
Scan & Ekstraksi Reimburse Struk -- halaman utama.
Modul pendukung: llm_core.py (provider + vision API), excel_core.py (aturan
kategori + template Excel), pdf_core.py (gabung PDF lossless), image_utils.py
(GPS EXIF + kompresi khusus API).
"""

from datetime import date

import pandas as pd
import streamlit as st

import excel_core
import image_utils
import llm_core
import pdf_core

st.set_page_config(page_title="Scan Reimburse Struk", page_icon="🧾", layout="wide")

st.markdown(
    """
    <h2>🧾 Aplikasi Scan & Ekstraksi Reimburse Struk</h2>
    <p>Upload foto struk bensin, parkir, dan Teazzi (drink) sekaligus untuk satu bulan.
    Sistem akan mengekstrak data, mengurutkannya berdasarkan tanggal transaksi,
    mengisinya ke template Excel, dan menggabungkan foto struk + screenshot Flazz
    asli ke satu PDF tanpa kompresi.</p>
    """,
    unsafe_allow_html=True,
)

TEMPLATE_PATH = "FORM_REIMBURSE_template.xlsx"

if not llm_core.PROVIDERS:
    st.error(
        "Belum ada API key yang dikonfigurasi. Buat file `.streamlit/secrets.toml` "
        "(lihat `.streamlit/secrets.toml.example`) atau set environment variable "
        "KAGIRO_API_KEY / BANDEL_API_KEY, lalu jalankan ulang aplikasi."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────────────────
# SIDEBAR -- data pemohon
# ─────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Data Pemohon")
    name = st.text_input("Name", value="")
    department = st.text_input("Department", value="")
    purpose = st.text_input("Purpose", value="Reimburse")
    bank_acc = st.text_input("Bank Acc.", value="")
    st.caption("Periode (kolom E6 & E7) diisi otomatis: awal & akhir bulan dari tanggal transaksi yang ter-upload.")

# ─────────────────────────────────────────────────────────────────────────
# HEALTH CHECK PROVIDER -- detail, bukan cuma HTTP 200
# ─────────────────────────────────────────────────────────────────────────
st.subheader("Pemilihan provider OCR")

if "provider_check_results" not in st.session_state:
    st.session_state.provider_check_results = llm_core.check_all_providers()

col_refresh, col_mode = st.columns([1, 3])
with col_refresh:
    if st.button("🔄 Cek ulang semua provider"):
        st.session_state.provider_check_results = llm_core.check_all_providers()

results = st.session_state.provider_check_results
best_provider = llm_core.pick_best_provider(results)

mode = st.radio(
    "Mode pemilihan",
    ["Auto (pakai provider pertama yang aktif)", "Pilih manual"],
    horizontal=False,
    key="provider_mode",
)

if mode.startswith("Auto"):
    provider_name = best_provider
else:
    provider_name = st.selectbox("Pilih Provider OCR", list(llm_core.PROVIDERS.keys()))

if provider_name:
    r = results.get(provider_name, {})
    st.markdown(f"**Provider aktif:** {provider_name} — {r.get('status', '?')}")
else:
    st.error("Semua provider gagal terhubung. Lihat detail di bawah.")

with st.expander("📋 Status detail semua provider", expanded=not bool(best_provider)):
    for pname, r in results.items():
        st.write(f"**{pname}**: {r.get('status', '(belum dicek)')}")

available_models = results.get(provider_name, {}).get("models", []) if provider_name else []
selected_model = st.selectbox("Pilih Model Vision", available_models if available_models else ["Model tidak tersedia"])

# ─────────────────────────────────────────────────────────────────────────
# UPLOAD
# ─────────────────────────────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "Unggah foto-foto struk (Bensin, Parkir, Teazzi) sekaligus untuk sebulan",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key="struk_uploader",
)

flazz_files = st.file_uploader(
    "Unggah screenshot riwayat kartu Flazz/e-money (opsional) — untuk parkir yang dibayar kartu",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key="flazz_uploader",
)

st.info(
    """
**Aturan ekstraksi & format output:**
1. **Sorting kronologis:** hasil di Excel diurutkan dari tanggal transaksi paling awal ke akhir (bukan urutan upload).
2. **Bensin:** Pertalite → description cukup nama BBM. Pertamax/jenis lain → description berisi jenis BBM + jumlah liter, nominal tetap total akhir struk.
3. **Drink (Teazzi):** description berisi jenis minuman & nama outlet, nominal total akhir.
4. **Parkir (dari foto struk):** description berisi nama tempat. Kalau tidak tertera di struk, sistem coba baca
   koordinat GPS dari EXIF foto dan cari nama lokasinya otomatis; kalau itu juga tidak ada, jadi `-` untuk diisi manual.
5. **Screenshot Flazz/e-money:** setiap baris "Parking" dicek terhadap struk parkir yang sudah difoto —
   kalau tanggal & nominalnya sama persis, baris itu **di-skip** (tidak dobel input). Baris "Parking" yang
   tidak match struk manapun tetap dimasukkan (description `-` kecuali fallback GPS berhasil).
   Baris **"Top Up" selalu di-skip**, tidak pernah dimasukkan.
6. **Periode E6/E7:** otomatis diisi awal & akhir bulan berdasarkan tanggal transaksi.
7. **PDF gabungan:** foto struk fisik **dan** screenshot Flazz digabung ke satu PDF, tanpa kompresi/downsizing.
    """
)

if "extracted_items" not in st.session_state:
    st.session_state.extracted_items = []
if "pdf_image_bytes_list" not in st.session_state:
    st.session_state.pdf_image_bytes_list = []
if "geocode_cache" not in st.session_state:
    st.session_state.geocode_cache = {}

# ─────────────────────────────────────────────────────────────────────────
# PROSES
# ─────────────────────────────────────────────────────────────────────────
if st.button("🚀 Mulai Proses OCR, Sorting, Generate Excel & PDF", type="primary"):
    if not uploaded_files and not flazz_files:
        st.warning("Silakan unggah minimal satu foto struk atau screenshot Flazz terlebih dahulu.")
    elif not provider_name or not available_models or selected_model == "Model tidak tersedia":
        st.error("Provider tidak aktif atau model tidak ditemukan. Gagal memproses.")
    else:
        extracted_items = []       # dari foto struk fisik
        flazz_items_raw = []       # dari screenshot Flazz (sebelum dedupe)
        pdf_image_bytes_list = []  # struk + flazz screenshot, untuk PDF gabungan
        failures = []

        total_files = len(uploaded_files) + len(flazz_files)
        progress_bar = st.progress(0)
        done = 0

        with st.spinner("Sedang memproses foto struk dengan AI Vision..."):
            for file in uploaded_files:
                img_bytes = file.getvalue()
                pdf_image_bytes_list.append(img_bytes)
                try:
                    parsed = llm_core.call_vision_api(provider_name, selected_model, img_bytes, results)

                    # Fallback GPS: kalau kategori parkir & tidak ada nama lokasi,
                    # coba baca EXIF GPS foto lalu reverse-geocode.
                    if (parsed.get("type") or "").strip().lower() == "parkir":
                        loc = (parsed.get("location_name") or "").strip()
                        if not loc or loc == "-":
                            gps = image_utils.extract_gps(img_bytes)
                            if gps:
                                label = image_utils.reverse_geocode(
                                    gps[0], gps[1], cache=st.session_state.geocode_cache
                                )
                                if label:
                                    parsed["location_name"] = label

                    extracted_items.append(parsed)
                except Exception as e:
                    failures.append((file.name, str(e)))
                done += 1
                progress_bar.progress(done / max(total_files, 1))

        with st.spinner("Sedang memproses screenshot Flazz/e-money..."):
            for file in flazz_files:
                img_bytes = file.getvalue()
                pdf_image_bytes_list.append(img_bytes)
                try:
                    parsed_list = llm_core.call_vision_api_flazz(provider_name, selected_model, img_bytes, results)
                    flazz_items_raw.extend(parsed_list)
                except Exception as e:
                    failures.append((file.name, str(e)))
                done += 1
                progress_bar.progress(done / max(total_files, 1))

        if failures:
            with st.expander(f"⚠️ {len(failures)} file gagal diproses"):
                for fname, err in failures:
                    st.write(f"- {fname}: {err}")

        flazz_parking_raw_count = sum(1 for f in flazz_items_raw if "park" in (f.get("type") or "").lower())
        flazz_deduped = excel_core.filter_and_dedupe_flazz(extracted_items, flazz_items_raw)

        # Fallback GPS juga untuk entri Flazz yang lolos dedupe: cari foto
        # struk mana pun yang tanggalnya sama (screenshot sendiri tidak punya
        # GPS -- fallback ini memang hanya efektif kalau user juga upload foto
        # lain di tanggal sama; kalau tidak ketemu, tetap "-").
        skipped_dupe = flazz_parking_raw_count - len(flazz_deduped)
        combined_items = extracted_items + flazz_deduped

        st.session_state.extracted_items = combined_items
        st.session_state.pdf_image_bytes_list = pdf_image_bytes_list

        if combined_items:
            msg = f"Ekstraksi selesai: {len(extracted_items)} struk"
            if flazz_files:
                msg += f" + {len(flazz_deduped)} transaksi parkir dari Flazz"
                if skipped_dupe > 0:
                    msg += f" ({skipped_dupe} duplikat di-skip karena sudah ada struk fotonya)"
            st.success(msg + ".")
        else:
            st.error(
                "Tidak ada data yang berhasil diekstrak dari gambar. "
                "PDF gabungan foto tetap bisa diunduh di bawah; periksa pesan error tiap file di atas."
            )

# ─────────────────────────────────────────────────────────────────────────
# HASIL & DOWNLOAD -- Excel dan PDF dibuat & ditampilkan SECARA TERPISAH,
# supaya kalau salah satu gagal, yang lain tetap bisa didownload.
# ─────────────────────────────────────────────────────────────────────────
df = None
if st.session_state.extracted_items:
    df = excel_core.build_rows(st.session_state.extracted_items)
    display_df = df.copy()
    display_df["date"] = display_df["date"].apply(lambda d: d.strftime("%d %b %Y") if d else "-")
    st.dataframe(display_df, use_container_width=True)
    st.markdown(f"**Total: Rp {df['nominal'].sum():,.0f}**".replace(",", "."))

dcol1, dcol2 = st.columns(2)

with dcol1:
    if df is None:
        st.info("Excel belum bisa dibuat karena tidak ada data struk yang terbaca.")
    else:
        try:
            excel_bytes = excel_core.fill_excel_template(
                df,
                TEMPLATE_PATH,
                {"name": name, "department": department, "purpose": purpose, "bank_acc": bank_acc},
            )
            st.download_button(
                "📥 Download Excel Reimburse",
                data=excel_bytes,
                file_name=f"FORM_REIMBURSE_{date.today().strftime('%Y%m')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as e:
            st.error(f"Gagal membuat file Excel: {e}")

with dcol2:
    if not st.session_state.pdf_image_bytes_list:
        st.info("Belum ada gambar untuk digabungkan ke PDF.")
    else:
        try:
            pdf_bytes = pdf_core.merge_images_to_pdf(st.session_state.pdf_image_bytes_list)
            st.download_button(
                "📥 Download PDF Struk + Flazz Gabungan (tanpa kompresi)",
                data=pdf_bytes,
                file_name=f"Struk_Gabungan_{date.today().strftime('%Y%m')}.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"Gagal membuat PDF gabungan: {e}")
