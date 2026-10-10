"""No-LLM mode: RapidOCR reads the KTP, a label parser maps lines to KtpData.

Needs the [ocr] extra. Cannot judge tampering or recapture: those checks are reported
as skipped, never as passed.
"""

import logging
import re
from datetime import date
from functools import lru_cache

from id_verification.core.ktp import KtpData, KtpExtraction

# label regex (on the uppercased line start) -> KtpData field
LABELS = {
    r"NIK\b": "nik",
    r"NAMA\b": "nama",
    r"TEMPAT": "ttl",
    r"JENIS": "jenis_kelamin",
    r"GOL": "golongan_darah",
    r"ALAMAT": "alamat",
    r"RT\s*/?\s*RW": "rt_rw",
    r"KE[LI1]\s*/": "kelurahan_desa",  # OCR often reads "Kel" as "KeI"
    r"KECAMATAN": "kecamatan",
    r"AGAMA": "agama",
    r"STATUS": "status_perkawinan",
    r"PEKERJAAN": "pekerjaan",
    r"KEWARGANEGARAAN": "kewarganegaraan",
    r"BERLAKU": "berlaku_hingga",
}
_NIK_FIXES = str.maketrans({"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "B": "8", "S": "5", "Z": "2"})
_DATE = re.compile(r"(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{4})")


@lru_cache
def _engine():
    from rapidocr import RapidOCR  # lazy: optional [ocr] extra

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    return RapidOCR()


def read_lines(jpeg: bytes) -> list[str]:
    return list(_engine()(jpeg).txts or [])


def _label(line: str) -> str | None:
    return next((field for rx, field in LABELS.items() if re.match(rx, line)), None)


def _date(s: str) -> date | None:
    m = _DATE.search(s)
    try:
        return date(int(m[3]), int(m[2]), int(m[1])) if m else None
    except ValueError:
        return None


def parse(lines: list[str]) -> KtpExtraction:
    """Map OCR lines to fields. A value is either after ':' on the label line or on the next line."""
    lines = [re.sub(r"\s+", " ", ln.replace("：", ":")).strip().upper() for ln in lines]
    raw: dict[str, str] = {}
    provinsi = kabupaten = None
    for i, line in enumerate(lines):
        if line.startswith("PROVINSI") and provinsi is None:
            provinsi = line.removeprefix("PROVINSI").strip()
            kabupaten = lines[i + 1] if i + 1 < len(lines) else None
            continue
        field = _label(line)
        if field is None or field in raw:
            continue
        value = line.split(":", 1)[1] if ":" in line else ""
        if not value.strip() and i + 1 < len(lines) and _label(lines[i + 1]) is None:
            value = lines[i + 1]
        raw[field] = value.strip(" :")

    text = " ".join(lines)
    nik = re.sub(r"\D", "", raw.get("nik", "").translate(_NIK_FIXES))
    gender = re.search(r"PEREMPUAN|FEMALE|LAKI|MALE", raw.get("jenis_kelamin", ""))  # WNA cards are in English
    blood = re.search(r"GOL\.?\s*DARAH\s*:?\s*(AB|A|B|O)\b", text)
    ttl = raw.get("ttl", "")
    berlaku = raw.get("berlaku_hingga", "")
    if "SEUMUR" in berlaku:
        berlaku = "SEUMUR HIDUP"
    elif d := _date(berlaku):
        berlaku = d.isoformat()

    data = None
    if len(nik) == 16 and raw.get("nama"):
        data = KtpData(
            nik=nik,
            nama=raw["nama"],
            tempat_lahir=ttl.split(",")[0].strip() or None,
            tanggal_lahir=_date(ttl),
            jenis_kelamin=("PEREMPUAN" if gender[0] in ("PEREMPUAN", "FEMALE") else "LAKI-LAKI") if gender else None,
            golongan_darah=blood[1] if blood else None,
            alamat=raw.get("alamat") or None,
            rt_rw=raw.get("rt_rw") or None,
            kelurahan_desa=raw.get("kelurahan_desa") or None,
            kecamatan=raw.get("kecamatan") or None,
            agama=raw.get("agama") or None,
            status_perkawinan=raw.get("status_perkawinan") or None,
            pekerjaan=raw.get("pekerjaan") or None,
            kewarganegaraan=raw.get("kewarganegaraan") or None,
            berlaku_hingga=berlaku or None,
            provinsi=provinsi or None,
            kabupaten_kota=kabupaten,
        )
    return KtpExtraction(
        # ponytail: header + parseable NIK heuristic; a card-layout classifier would be stronger.
        is_indonesian_ktp=bool(provinsi) and len(nik) == 16,
        tampering_suspected=False,  # not checked in no-LLM mode, reported in skipped_checks
        recaptured=False,
        data=data,
        notes="local OCR",
    )


def extract_ktp(jpeg: bytes) -> KtpExtraction:
    return parse(read_lines(jpeg))
