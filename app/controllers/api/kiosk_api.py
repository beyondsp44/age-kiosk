from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.services.engine_service import EngineService

api_kiosk_bp = Blueprint("api_kiosk", __name__, url_prefix="/api/v1/kiosk")


@api_kiosk_bp.get("/health")
def health():
    return jsonify(
        {
            "success": True,
            "data": {"service": "age-kiosk-api", "status": "ok"},
            "message": "healthy",
            "code": 200,
            "errors": [],
        }
    ), 200


@api_kiosk_bp.get("/status")
def status():
    try:
        payload = EngineService.get_status()
        return jsonify(
            {
                "success": True,
                "data": payload,
                "message": "ok",
                "code": 200,
                "errors": [],
            }
        ), 200
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "data": None,
                "message": f"status fetch failed: {exc}",
                "code": 500,
                "errors": [],
            }
        ), 500


@api_kiosk_bp.post("/reset")
def reset():
    try:
        EngineService.reset()
        return jsonify(
            {
                "success": True,
                "data": None,
                "message": "engine reset",
                "code": 200,
                "errors": [],
            }
        ), 200
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "data": None,
                "message": f"reset failed: {exc}",
                "code": 500,
                "errors": [],
            }
        ), 500


@api_kiosk_bp.post("/key")
def key_input():
    try:
        payload = request.get_json(silent=True) or {}
        result = EngineService.send_key(
            key=payload.get("key"),
            key_code=payload.get("key_code"),
        )
        return jsonify(
            {
                "success": True,
                "data": result,
                "message": "key processed",
                "code": 200,
                "errors": [],
            }
        ), 200
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "data": None,
                "message": f"key input failed: {exc}",
                "code": 500,
                "errors": [],
            }
        ), 500


@api_kiosk_bp.get("/stream")
def stream():
    return Response(
        stream_with_context(EngineService.stream_frames()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
