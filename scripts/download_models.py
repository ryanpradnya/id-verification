"""Download the local biometric ONNX models into ./models (or $MODELS_DIR).

All three models are Apache-2.0 licensed and safe for commercial use.
Hashes are pinned so a swapped file upstream fails loudly instead of loading.
"""

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    target = Path(os.environ.get("MODELS_DIR", "models"))
    target.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, (url, expected) in MODELS.items():
        path = target / name
        if not path.exists():
            print(f"downloading {name}")
            urllib.request.urlretrieve(url, path)  # noqa: S310  fixed https URLs above
        actual = sha256(path)
        if actual != expected:
            print(f"HASH MISMATCH {name}: {actual}", file=sys.stderr)
            path.unlink()
            failed = True
        else:
            print(f"ok {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
