import base64
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    import onnxruntime as ort
except Exception:
    ort = None

try:
    from insightface.app import FaceAnalysis
except Exception:
    FaceAnalysis = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _age_to_display_range(age_val: Optional[float]) -> Optional[str]:
    if age_val is None:
        return None
    age = int(round(float(age_val)))
    if age <= 15:
        return "0-15"
    if age <= 27:
        return "16-27"
    if age <= 35:
        return "28-35"
    if age <= 45:
        return "36-45"
    if age <= 60:
        return "46-60"
    return "61+"


class CloudInferService:
    _lock = threading.Lock()
    _app = None
    _provider = "CPUExecutionProvider"
    _model_name = "buffalo_s"
    _init_error = ""

    @classmethod
    def _select_providers(cls) -> Tuple[List[str], int, str]:
        available: List[str] = []
        if ort is not None:
            try:
                available = list(ort.get_available_providers())
            except Exception:
                available = []

        enable_dml = _env_bool("AGE_KIOSK_ENABLE_DML", False)
        if enable_dml and "DmlExecutionProvider" in available:
            return ["DmlExecutionProvider", "CPUExecutionProvider"], 0, "DmlExecutionProvider"
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"], 0, "CUDAExecutionProvider"
        return ["CPUExecutionProvider"], -1, "CPUExecutionProvider"

    @classmethod
    def runtime_info(cls) -> Dict[str, Any]:
        available: List[str] = []
        if ort is not None:
            try:
                available = list(ort.get_available_providers())
            except Exception:
                available = []
        providers, ctx_id, primary = cls._select_providers()
        return {
            "initialized": cls._app is not None,
            "warming": False,
            "init_error": cls._init_error or None,
            "provider_selected": cls._provider if cls._app is not None else primary,
            "providers_config": providers,
            "ctx_id": ctx_id,
            "available_providers": available,
            "model_name": cls._model_name,
        }

    @classmethod
    def _init_app(cls):
        if FaceAnalysis is None:
            raise RuntimeError("insightface is not installed")

        providers, ctx_id, primary = cls._select_providers()
        model_name = str(os.getenv("AGE_KIOSK_CLOUD_MODEL_NAME", "buffalo_s") or "buffalo_s").strip()
        if not model_name:
            model_name = "buffalo_s"
        root = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), ".insightface")
        os.makedirs(os.path.join(root, "models"), exist_ok=True)

        try:
            app = FaceAnalysis(
                name=model_name,
                root=root,
                providers=providers,
                allowed_modules=["detection", "genderage"],
            )
        except TypeError:
            app = FaceAnalysis(name=model_name, root=root)

        det_size = (192, 192) if primary == "CPUExecutionProvider" else (256, 256)
        app.prepare(ctx_id=ctx_id, det_size=det_size)
        cls._app = app
        cls._provider = primary
        cls._model_name = model_name
        cls._init_error = ""

    @classmethod
    def _get_app(cls):
        with cls._lock:
            if cls._app is not None:
                return cls._app
            if cls._init_error:
                raise RuntimeError(f"MODEL_INIT_FAILED: {cls._init_error}")
            try:
                cls._init_app()
            except Exception as exc:
                cls._init_error = str(exc)
                raise RuntimeError(f"MODEL_INIT_FAILED: {exc}") from exc
            return cls._app

    @staticmethod
    def _decode_image(image_bytes: bytes):
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("invalid image bytes")
        return frame

    @staticmethod
    def _parse_data_url_or_base64(image_base64: str) -> bytes:
        txt = str(image_base64 or "").strip()
        if not txt:
            raise ValueError("image_base64 is empty")
        if txt.startswith("data:"):
            parts = txt.split(",", 1)
            if len(parts) != 2:
                raise ValueError("invalid data URL")
            txt = parts[1]
        try:
            return base64.b64decode(txt, validate=True)
        except Exception as exc:
            raise ValueError("invalid base64 payload") from exc

    @staticmethod
    def _classify(age_value: Optional[float]) -> Dict[str, Any]:
        if age_value is None:
            return {
                "decision_code": "NO_AGE",
                "decision_label": "NO_AGE",
                "needs_manual_verify": True,
            }
        age_int = int(round(float(age_value)))
        if age_int <= 15:
            return {
                "decision_code": "FAIL_MINOR",
                "decision_label": "FAIL (Minor)",
                "needs_manual_verify": False,
            }
        if age_int <= 27:
            return {
                "decision_code": "GREY_VERIFY",
                "decision_label": "VERIFY (Manual Birthday Check)",
                "needs_manual_verify": True,
            }
        return {
            "decision_code": "PASS_ADULT",
            "decision_label": "PASS (Adult)",
            "needs_manual_verify": False,
        }

    @classmethod
    def infer_from_bytes(cls, image_bytes: bytes) -> Dict[str, Any]:
        frame = cls._decode_image(image_bytes)
        app = cls._get_app()

        faces = app.get(frame) or []
        face_count = len(faces)
        if face_count == 0:
            return {
                "provider": cls._provider,
                "face_count": 0,
                "ai_age": None,
                "ai_age_range": None,
                "decision_code": "NO_FACE",
                "decision_label": "NO_FACE",
                "needs_manual_verify": True,
                "message": "No face detected",
            }

        # Use the largest detected face as the primary target.
        best = max(
            faces,
            key=lambda f: max(0.0, float(f.bbox[2] - f.bbox[0])) * max(0.0, float(f.bbox[3] - f.bbox[1])),
        )
        age_value = None
        try:
            age_value = float(getattr(best, "age", None))
        except Exception:
            age_value = None

        cls_result = cls._classify(age_value)
        bbox = [int(round(float(v))) for v in list(best.bbox)] if getattr(best, "bbox", None) is not None else None

        return {
            "provider": cls._provider,
            "face_count": face_count,
            "ai_age": int(round(age_value)) if age_value is not None else None,
            "ai_age_range": _age_to_display_range(age_value),
            "primary_bbox": bbox,
            **cls_result,
            "message": "ok",
        }

    @classmethod
    def infer_from_base64(cls, image_base64: str) -> Dict[str, Any]:
        image_bytes = cls._parse_data_url_or_base64(image_base64)
        return cls.infer_from_bytes(image_bytes)
