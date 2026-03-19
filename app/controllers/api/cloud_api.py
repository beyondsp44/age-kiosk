from flask import Blueprint, current_app, jsonify, request

from app.services.cloud_infer_service import CloudInferService

api_cloud_bp = Blueprint("api_cloud", __name__, url_prefix="/api/v1/cloud")


def _ok(data, message: str = "ok", code: int = 200):
    return (
        jsonify(
            {
                "success": True,
                "data": data,
                "message": message,
                "code": code,
                "errors": [],
            }
        ),
        code,
    )


def _error(message: str, code: int = 500):
    return (
        jsonify(
            {
                "success": False,
                "data": None,
                "message": message,
                "code": code,
                "errors": [],
            }
        ),
        code,
    )


def _max_image_bytes() -> int:
    mb = int(current_app.config.get("CLOUD_MAX_IMAGE_MB", 5))
    mb = max(1, min(mb, 20))
    return mb * 1024 * 1024


@api_cloud_bp.get("/health")
def health():
    try:
        return _ok(CloudInferService.runtime_info())
    except Exception as exc:
        return _error(f"cloud health failed: {exc}")


@api_cloud_bp.post("/infer")
def infer():
    try:
        max_bytes = _max_image_bytes()
        file_obj = request.files.get("image")
        if file_obj is not None:
            image_bytes = file_obj.read(max_bytes + 1)
            if not image_bytes:
                return _error("empty image payload", code=400)
            if len(image_bytes) > max_bytes:
                return _error("image too large", code=413)
            result = CloudInferService.infer_from_bytes(image_bytes)
            return _ok(result)

        payload = request.get_json(silent=True) or {}
        image_base64 = payload.get("image_base64")
        if not image_base64:
            return _error("missing image file or image_base64", code=400)
        result = CloudInferService.infer_from_base64(image_base64)
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc), code=400)
    except Exception as exc:
        return _error(f"cloud infer failed: {exc}")
