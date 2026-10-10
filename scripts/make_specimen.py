"""Render the synthetic specimen KTP used by `id-verification check-llm` and the public OCR tests.

Every value is made up and the photo box is empty: safe to commit, belongs to no one.
python scripts/make_specimen.py  ->  src/id_verification/core/assets/specimen_ktp.jpg
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parents[1] / "src/id_verification/core/assets/specimen_ktp.jpg"
FIELDS = [
    ("NIK", "3171011208900001"),
    ("Nama", "BUDI SPESIMEN"),
    ("Tempat/Tgl Lahir", "JAKARTA, 12-08-1990"),
    ("Jenis Kelamin", "LAKI-LAKI"),
    ("Alamat", "JL. CONTOH NO. 1"),
    ("RT/RW", "001/002"),
    ("Kel/Desa", "GAMBIR"),
    ("Kecamatan", "GAMBIR"),
    ("Agama", "ISLAM"),
    ("Status Perkawinan", "BELUM KAWIN"),
    ("Pekerjaan", "KARYAWAN SWASTA"),
    ("Kewarganegaraan", "WNI"),
    ("Berlaku Hingga", "SEUMUR HIDUP"),
]


def main() -> None:
    w, h = 1280, 810
    img = Image.new("RGB", (w, h), (182, 214, 236))
    d = ImageDraw.Draw(img)
    title, label = ImageFont.load_default(42), ImageFont.load_default(30)
    d.text((w // 2, 40), "PROVINSI DKI JAKARTA", font=title, fill="black", anchor="mt")
    d.text((w // 2, 95), "JAKARTA PUSAT", font=title, fill="black", anchor="mt")
    y = 170
    for name, value in FIELDS:
        d.text((40, y), name, font=label, fill="black")
        d.text((340, y), f": {value}", font=label, fill="black")
        y += 46
    d.rectangle((960, 200, 1220, 540), outline="black", width=3)  # empty photo box
    d.text((1090, 580), "SPESIMEN", font=label, fill="black", anchor="mt")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "JPEG", quality=92)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
