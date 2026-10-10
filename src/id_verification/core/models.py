"""Local biometric ONNX models: fetched on first use, SHA-256 pinned.

All three models are Apache-2.0 licensed. Hashes are pinned so a swapped file upstream
fails loudly instead of loading.
"""

import hashlib
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    "MiniFASNetV2.onnx": (
        "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",
        "b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_models(models_dir: Path) -> None:
    """Download missing models into models_dir. Raises RuntimeError on a hash mismatch."""
    models_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in MODELS.items():
        path = models_dir / name
        if path.exists():
            continue
        log.info("downloading %s", name)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)  # noqa: S310  fixed https URLs above
        if (actual := _sha256(tmp)) != expected:
            tmp.unlink()
            raise RuntimeError(f"hash mismatch for {name}: {actual}")
        tmp.rename(path)


if __name__ == "__main__":  # stdlib only, so the Dockerfile can run it before installing the package
    import sys

    ensure_models(Path(sys.argv[1]))
