"""KTP data schema (LLM structured output) and deterministic NIK rules."""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# NIK digits 1-2 -> keyword expected in the PROVINSI header.
# Codes 91-96 share "PAPUA": people registered before the 2022 split keep 91/92.
PROVINCES = {
    "11": "ACEH",
    "12": "SUMATERA UTARA",
    "13": "SUMATERA BARAT",
    "14": "RIAU",
    "15": "JAMBI",
    "16": "SUMATERA SELATAN",
    "17": "BENGKULU",
    "18": "LAMPUNG",
    "19": "BANGKA",
    "21": "KEPULAUAN RIAU",
    "31": "JAKARTA",
    "32": "JAWA BARAT",
    "33": "JAWA TENGAH",
    "34": "YOGYAKARTA",
    "35": "JAWA TIMUR",
    "36": "BANTEN",
    "51": "BALI",
    "52": "NUSA TENGGARA BARAT",
    "53": "NUSA TENGGARA TIMUR",
    "61": "KALIMANTAN BARAT",
    "62": "KALIMANTAN TENGAH",
    "63": "KALIMANTAN SELATAN",
    "64": "KALIMANTAN TIMUR",
    "65": "KALIMANTAN UTARA",
    "71": "SULAWESI UTARA",
    "72": "SULAWESI TENGAH",
    "73": "SULAWESI SELATAN",
    "74": "SULAWESI TENGGARA",
    "75": "GORONTALO",
    "76": "SULAWESI BARAT",
    "81": "MALUKU",
    "82": "MALUKU UTARA",
    "91": "PAPUA",
    "92": "PAPUA",
    "93": "PAPUA",
    "94": "PAPUA",
    "95": "PAPUA",
    "96": "PAPUA",
}
WNI = {"WNI", "INDONESIA"}


class KtpData(BaseModel):
    nik: str = Field(description="16-digit NIK, digits only")
    nama: str = Field(description="Full name exactly as printed")
    tempat_lahir: str | None = None
    tanggal_lahir: date | None = Field(None, description="Birth date, YYYY-MM-DD")
    jenis_kelamin: Literal["LAKI-LAKI", "PEREMPUAN"] | None = Field(
        None, description="Normalize MALE to LAKI-LAKI and FEMALE to PEREMPUAN"
    )
    golongan_darah: str | None = None
    alamat: str | None = None
    rt_rw: str | None = None
    kelurahan_desa: str | None = None
    kecamatan: str | None = None
    agama: str | None = None
    status_perkawinan: str | None = None
    pekerjaan: str | None = None
    kewarganegaraan: str | None = Field(None, description="WNI, or the foreign country as printed")
    berlaku_hingga: str | None = Field(None, description="'SEUMUR HIDUP' or YYYY-MM-DD")
    provinsi: str | None = Field(None, description="Header line without the word PROVINSI")
    kabupaten_kota: str | None = None


class KtpExtraction(BaseModel):
    is_indonesian_ktp: bool = Field(description="True only for an Indonesian e-KTP card")
    tampering_suspected: bool = Field(description="Edited text/photo, mismatched fonts, pasted elements")
    recaptured: bool = Field(description="Card shown on a screen, or a photocopy/printout, not the physical card")
    data: KtpData | None = Field(None, description="Null when the card is unreadable")
    notes: str = Field("", description="Short reasoning for the flags above")


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").upper().replace("PROVINSI", "")).strip()


def nik_rules(d: KtpData, today: date) -> list[str]:
    """Reason codes for structural NIK problems and data inconsistencies."""
    codes = []
    nik = re.sub(r"\D", "", d.nik or "")
    if not d.nama.strip() or d.tanggal_lahir is None or d.jenis_kelamin is None or not d.kewarganegaraan:
        codes.append("KTP_FIELDS_MISSING")

    if len(nik) != 16 or nik[12:] == "0000":
        return codes + ["NIK_INVALID_FORMAT"]

    province = PROVINCES.get(nik[:2])
    if province is None:
        codes.append("NIK_INVALID_PROVINCE")
    elif d.provinsi and province not in _norm(d.provinsi):
        codes.append("NIK_PROVINCE_MISMATCH")

    dd, mm, yy = int(nik[6:8]), int(nik[8:10]), int(nik[10:12])
    female = dd > 40
    day = dd - 40 if female else dd
    if not (1 <= day <= 31 and 1 <= mm <= 12):
        return codes + ["NIK_INVALID_FORMAT"]

    b = d.tanggal_lahir
    if b and (b.day, b.month, b.year % 100) != (day, mm, yy):
        codes.append("NIK_BIRTHDATE_MISMATCH")
    if d.jenis_kelamin and (d.jenis_kelamin == "PEREMPUAN") != female:
        codes.append("NIK_GENDER_MISMATCH")

    # e-KTP for citizens is valid for life (UU 24/2013) regardless of the printed date.
    # Foreigner (WNA) cards expire.
    if d.kewarganegaraan and _norm(d.kewarganegaraan) not in WNI:
        try:
            expired = date.fromisoformat((d.berlaku_hingga or "").strip()) < today
        except ValueError:
            expired = True
        if expired:
            codes.append("KTP_EXPIRED")
    return codes
