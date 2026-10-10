"""Local biometrics: YuNet face detection, SFace matching, MiniFASNet passive liveness.

Everything runs on CPU via OpenCV DNN / onnxruntime. Models are loaded once per process.
"""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

DETECT_MAX_SIDE = 1280
LIVENESS_CROP_SCALE = 2.7  # MiniFASNetV2 was trained on 2.7x face crops


@lru_cache
def _detector(models_dir: Path):
    return cv2.FaceDetectorYN.create(
        str(models_dir / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.7, 0.3, 50
    )


@lru_cache
def _recognizer(models_dir: Path):
    return cv2.FaceRecognizerSF.create(str(models_dir / "face_recognition_sface_2021dec.onnx"), "")


@lru_cache
def _liveness(models_dir: Path) -> ort.InferenceSession:
    return ort.InferenceSession(str(models_dir / "MiniFASNetV2.onnx"), providers=["CPUExecutionProvider"])


def detect_faces(img: np.ndarray, models_dir: Path) -> list[np.ndarray]:
    """YuNet rows [x, y, w, h, 10 landmark coords, score] in original image coords, largest first."""
    h, w = img.shape[:2]
    scale = min(1.0, DETECT_MAX_SIDE / max(h, w))
    small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
    det = _detector(models_dir)
    det.setInputSize((small.shape[1], small.shape[0]))
    _, faces = det.detect(small)
    if faces is None:
        return []
    faces = faces.copy()
    faces[:, :14] /= scale
    return sorted(faces, key=lambda f: f[2] * f[3], reverse=True)


def embed(img: np.ndarray, face: np.ndarray, models_dir: Path) -> np.ndarray:
    rec = _recognizer(models_dir)
    return rec.feature(rec.alignCrop(img, face))


def similarity(a: np.ndarray, b: np.ndarray, models_dir: Path) -> float:
    return float(_recognizer(models_dir).match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


def liveness_score(img: np.ndarray, face: np.ndarray, models_dir: Path) -> float:
    """Probability (0..1) that the face is a real live capture, not a print/screen."""
    sess = _liveness(models_dir)
    inp = sess.get_inputs()[0]
    out_h, out_w = inp.shape[2], inp.shape[3]
    src_h, src_w = img.shape[:2]
    x, y, bw, bh = (float(v) for v in face[:4])
    scale = min((src_h - 1) / bh, (src_w - 1) / bw, LIVENESS_CROP_SCALE)
    cx, cy, nw, nh = x + bw / 2, y + bh / 2, bw * scale, bh * scale
    x1, y1 = max(0, int(cx - nw / 2)), max(0, int(cy - nh / 2))
    x2, y2 = min(src_w - 1, int(cx + nw / 2)), min(src_h - 1, int(cy + nh / 2))
    crop = cv2.resize(img[y1 : y2 + 1, x1 : x2 + 1], (out_w, out_h)).astype(np.float32)
    logits = sess.run(None, {inp.name: crop.transpose(2, 0, 1)[None]})[0]
    probs = np.exp(logits - logits.max()) / np.exp(logits - logits.max()).sum()
    return float(probs[0, 1])  # class 1 = real


def analyze(ktp: np.ndarray, selfie: np.ndarray, models_dir: Path) -> dict:
    """Compare the KTP photo with the live face in the selfie.

    Selfies are "holding KTP next to face", so they usually contain two faces:
    the live face (largest) and the printed card photo.
    """
    result = {
        "ktp_face_found": False,
        "selfie_face_count": 0,
        "face_similarity": None,
        "card_in_selfie_similarity": None,
        "liveness_score": None,
    }
    ktp_faces = detect_faces(ktp, models_dir)
    selfie_faces = detect_faces(selfie, models_dir)
    result["ktp_face_found"] = bool(ktp_faces)
    result["selfie_face_count"] = len(selfie_faces)
    if not selfie_faces:
        return result

    live = selfie_faces[0]
    result["liveness_score"] = round(liveness_score(selfie, live, models_dir), 4)
    if not ktp_faces:
        return result

    # A KTP photo taken in hand can also show the holder. e-KTP has one printed photo,
    # so pick the most print-like face (lowest liveness) as the card photo.
    # ponytail: heuristic, swap for an LLM/detector card-bbox crop if it misfires.
    ktp_face = ktp_faces[0] if len(ktp_faces) == 1 else min(ktp_faces, key=lambda f: liveness_score(ktp, f, models_dir))
    ktp_feat = embed(ktp, ktp_face, models_dir)
    result["face_similarity"] = round(similarity(ktp_feat, embed(selfie, live, models_dir), models_dir), 4)
    if len(selfie_faces) > 1:
        card = embed(selfie, selfie_faces[1], models_dir)
        result["card_in_selfie_similarity"] = round(similarity(ktp_feat, card, models_dir), 4)
    return result
