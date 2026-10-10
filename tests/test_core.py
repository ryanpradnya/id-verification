"""Core library only (no server extra): import boundary, OCR parser, LLM JSON fallback."""

import asyncio
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage

from id_verification.core.images import sanitize
from id_verification.core.llm import LLM, ProviderError, Review
from id_verification.core.ocr import parse
from tests.conftest import SPECIMEN


def test_core_import_pulls_no_server_dependencies():
    code = (
        "import sys, id_verification; "
        "bad = [m for m in ('fastapi', 'sqlalchemy', 'celery', 'boto3', 'redis', 'cryptography') if m in sys.modules]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ---------- OCR parser (synthetic lines, no images) ----------

LINES = [
    "PROVINSI JAWA BARAT",
    "KABUPATEN BANDUNG",
    "NIK",
    "：3204O15508950OO2",  # full-width colon, OCR O-for-0
    "Nama : SITI CONTOH",
    "Tempat/Tgl Lahir",
    ": BANDUNG, 15-08-1995",
    "Jenis Kelamin : PEREMPUAN Gol. Darah : AB",
    "KeI/Desa",  # OCR read l as I
    ": CIBIRU",
    "Kewarganegaraan : WNI",
    "Berlaku Hingga : SEUMUR HIDUP",
]


def test_parse_labels_and_values():
    ex = parse(LINES)
    d = ex.data
    assert ex.is_indonesian_ktp and not ex.tampering_suspected and not ex.recaptured
    assert d.nik == "3204015508950002" and d.nama == "SITI CONTOH"
    assert (d.tempat_lahir, d.tanggal_lahir.isoformat()) == ("BANDUNG", "1995-08-15")
    assert (d.jenis_kelamin, d.golongan_darah) == ("PEREMPUAN", "AB")
    assert (d.kelurahan_desa, d.kewarganegaraan, d.berlaku_hingga) == ("CIBIRU", "WNI", "SEUMUR HIDUP")
    assert (d.provinsi, d.kabupaten_kota) == ("JAWA BARAT", "KABUPATEN BANDUNG")


def test_parse_english_foreigner_card():
    lines = ["PROVINSI BALI", "NIK : 5171010101800001", "NAMA : JOHN SAMPLE", "JENIS KELAMIN", "FEMALE",
             "BERLAKU HINGGA : 01-02-2030"]  # fmt: skip
    d = parse(lines).data
    assert d.jenis_kelamin == "PEREMPUAN" and d.berlaku_hingga == "2030-02-01"


def test_parse_unreadable_nik_is_not_a_ktp():
    ex = parse(["PROVINSI BALI", "NIK : 51710101", "NAMA : X"])
    assert ex.data is None and not ex.is_indonesian_ktp


def test_specimen_image_ocr():
    pytest.importorskip("rapidocr")
    from id_verification.core.ocr import extract_ktp

    d = extract_ktp(sanitize(SPECIMEN.read_bytes(), 10 << 20)).data
    assert (d.nik, d.nama, d.tanggal_lahir.isoformat()) == ("3171011208900001", "BUDI SPESIMEN", "1990-08-12")


# ---------- LLM structured output + JSON fallback (fake models, no network) ----------


class FakeModel:
    def __init__(self, structured=None, text: str = "", exc: Exception | None = None):
        self.structured, self.text, self.exc, self.prompts = structured, text, exc, []

    def with_structured_output(self, schema, **_):
        outer = self

        class Bound:
            async def ainvoke(self, msgs):
                if outer.exc:
                    raise outer.exc
                if outer.structured is None:
                    raise ValueError("model does not support tools")
                return outer.structured

        return Bound()

    async def ainvoke(self, msgs):
        self.prompts.append(msgs[1].content[0]["text"])
        if self.exc:
            raise self.exc
        return AIMessage(self.text)


class APIConnectionError(Exception):  # same name as the OpenAI / Anthropic SDK errors
    pass


def llm_with(*models) -> LLM:
    llm = LLM.__new__(LLM)
    llm.model_id, llm._models = "fake", [(m, None) for m in models]
    return llm


def review(llm: LLM) -> Review:
    return asyncio.run(llm.review(b"k", b"s", "3171011208900001", "BUDI"))


GOOD = Review(same_person=True, selfie_spoof_suspected=False)


def test_structured_output_used_when_it_works():
    assert review(llm_with(FakeModel(structured=GOOD))) == GOOD


def test_falls_back_to_prompted_json():
    m = FakeModel(text='Sure!\n```json\n{"same_person": false, "selfie_spoof_suspected": true}\n```')
    r = review(llm_with(m))
    assert (r.same_person, r.selfie_spoof_suspected) == (False, True)
    assert '"same_person"' in m.prompts[0]  # schema was put into the prompt


def test_garbage_from_every_model_is_a_provider_error():
    with pytest.raises(ProviderError):
        review(llm_with(FakeModel(text="I cannot help with that")))


def test_unreachable_model_skips_its_json_retry_and_uses_the_fallback_model():
    down = FakeModel(exc=APIConnectionError("refused"))
    assert review(llm_with(down, FakeModel(structured=GOOD))) == GOOD
    assert down.prompts == []
