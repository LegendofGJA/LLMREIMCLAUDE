"""
pdf_core.py
===========
Gabungkan foto struk (dan sekarang juga screenshot Flazz) jadi satu PDF TANPA
kompresi/downsizing sama sekali -- byte gambar asli dipakai apa adanya.

Fix untuk img2pdf.ExifOrientationError: beberapa foto HP menyimpan tag EXIF
Orientation yang tidak valid (mis. bernilai 0, padahal standar EXIF hanya
mengenal 1-8). img2pdf secara default akan crash kalau ketemu ini. Solusinya
BUKAN mengompres ulang gambar, tapi memberi tahu img2pdf untuk mengabaikan
tag rotasi yang tidak valid (`rotation=Rotation.ifvalid`) -- gambar lain yang
punya tag rotasi valid tetap dirotasi dengan benar seperti biasa, dan semua
byte gambar tetap 100% utuh seperti file aslinya.
"""

import img2pdf


def merge_images_to_pdf(image_bytes_list: list) -> bytes:
    """Gabungkan gambar asli ke satu PDF tanpa kompresi/downsizing.
    `rotation=Rotation.ifvalid` membuat img2pdf mengabaikan tag EXIF
    Orientation yang rusak/tidak valid alih-alih crash, tanpa menyentuh
    byte gambar sama sekali.

    Kalau versi img2pdf yang terpasang belum punya `Rotation.ifvalid` (atau
    gambar punya orientasi aneh yang tetap ditolak), otomatis dicoba ulang
    dengan mode rotasi lain supaya PDF tetap jadi."""
    if not image_bytes_list:
        raise ValueError("Tidak ada gambar untuk digabungkan ke PDF.")

    rotation_ifvalid = getattr(getattr(img2pdf, "Rotation", None), "ifvalid", None)
    if rotation_ifvalid is None:
        # img2pdf lama: tidak kenal Rotation sama sekali
        return img2pdf.convert(image_bytes_list)

    try:
        return img2pdf.convert(image_bytes_list, rotation=rotation_ifvalid)
    except getattr(img2pdf, "ExifOrientationError", ()):
        # orientasi EXIF ditolak walau sudah ifvalid -> pakai tanpa rotasi
        return img2pdf.convert(image_bytes_list, rotation=img2pdf.Rotation.none)
