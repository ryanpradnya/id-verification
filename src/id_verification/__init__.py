"""Indonesian KTP + live selfie verification.

    from id_verification import Verifier, Settings
    result = await Verifier(Settings(llm_model="none")).verify(ktp_bytes, selfie_bytes)

The server (API + hosted camera page) lives in id_verification.server and needs the [server] extra.
"""

from id_verification.config import Settings
from id_verification.core.images import InvalidImage
from id_verification.core.llm import ProviderError
from id_verification.core.verifier import Verifier, VerifyResult

__all__ = ["InvalidImage", "ProviderError", "Settings", "Verifier", "VerifyResult"]
