"""Decode, sanitize and quality-check uploaded images."""

import io

import cv2
import numpy as np
from PIL import Image, ImageOps

# Real-format check by magic bytes, never by filename/extension.
_MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
}

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard


class InvalidImage(ValueError):
    pass


def sniff(data: bytes) -> str:
    for magic, kind in _MAGIC.items():
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise InvalidImage("unsupported image type (jpeg, png, webp only)")


def sanitize(data: bytes, max_bytes: int) -> bytes:
    """Validate and re-encode to a clean JPEG: applies EXIF rotation, strips metadata."""
    if len(data) > max_bytes:
        raise InvalidImage(f"image larger than {max_bytes} bytes")
    sniff(data)
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (OSError, Image.DecompressionBombError) as e:
        raise InvalidImage("image could not be decoded") from e
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def to_bgr(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def quality_issues(jpeg: bytes, min_side: int, min_blur: float, min_brightness: float) -> tuple[list[str], dict]:
    img = to_bgr(jpeg)
    if img is None:
        return ["IMAGE_UNREADABLE"], {}
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    stats = {
        "short_side": int(min(img.shape[:2])),
        "blur_variance": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "brightness": round(float(gray.mean()), 1),
    }
    issues = []
    if stats["short_side"] < min_side:
        issues.append("IMAGE_TOO_SMALL")
    if stats["blur_variance"] < min_blur:
        issues.append("IMAGE_TOO_BLURRY")
    if stats["brightness"] < min_brightness:
        issues.append("IMAGE_TOO_DARK")
    return issues, stats
