import csv
import io
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.services.engine_service import EngineService

api_dashboard_bp = Blueprint("api_dashboard", __name__, url_prefix="/api")


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


@api_dashboard_bp.get("/status")
def status():
    try:
        return _ok(EngineService.get_status())
    except Exception as exc:
        return _error(f"status fetch failed: {exc}")


@api_dashboard_bp.get("/video_feed")
def video_feed():
    return Response(
        stream_with_context(EngineService.stream_frames()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@api_dashboard_bp.get("/events")
def events():
    poll_ms = request.args.get("poll_ms", default=1000, type=int)
    return Response(
        stream_with_context(EngineService.event_stream(poll_interval_ms=poll_ms)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api_dashboard_bp.post("/switch-camera")
def switch_camera():
    try:
        payload = request.get_json(silent=True) or {}
        cam_face = payload.get("cam_face")
        cam_ocr = payload.get("cam_ocr")
        data = EngineService.switch_camera(cam_face=cam_face, cam_ocr=cam_ocr)
        return _ok(data, message="camera switched")
    except Exception as exc:
        return _error(f"switch camera failed: {exc}", code=400)


@api_dashboard_bp.get("/stats")
def stats():
    try:
        return _ok(EngineService.get_stats())
    except Exception as exc:
        return _error(f"stats fetch failed: {exc}")


@api_dashboard_bp.get("/history")
def history():
    try:
        limit = request.args.get("limit", default=5, type=int)
        return _ok(EngineService.get_recent_history(limit=limit))
    except Exception as exc:
        return _error(f"history fetch failed: {exc}")


@api_dashboard_bp.get("/monitor/summary")
def monitor_summary():
    try:
        history_limit = request.args.get("history_limit", default=10, type=int)
        start_date = request.args.get("start_date", default=None, type=str)
        end_date = request.args.get("end_date", default=None, type=str)
        return _ok(
            EngineService.get_monitor_summary(
                history_limit=history_limit,
                start_date=start_date,
                end_date=end_date,
            )
        )
    except ValueError as exc:
        return _error(f"invalid date filter: {exc}", code=400)
    except Exception as exc:
        return _error(f"monitor summary failed: {exc}")


@api_dashboard_bp.get("/monitor/history")
def monitor_history():
    try:
        limit = request.args.get("limit", default=10, type=int)
        offset = request.args.get("offset", default=0, type=int)
        start_date = request.args.get("start_date", default=None, type=str)
        end_date = request.args.get("end_date", default=None, type=str)
        return _ok(
            EngineService.get_history_page(
                limit=limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
            )
        )
    except ValueError as exc:
        return _error(f"invalid date filter: {exc}", code=400)
    except Exception as exc:
        return _error(f"monitor history failed: {exc}")


@api_dashboard_bp.get("/monitor/export.csv")
def monitor_export_csv():
    try:
        limit = request.args.get("limit", default=0, type=int)
        start_date = request.args.get("start_date", default=None, type=str)
        end_date = request.args.get("end_date", default=None, type=str)
        result = EngineService.get_history_export_rows(
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )
        rows = result.get("items", [])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "timestamp",
                "mode",
                "raw_age",
                "ai_age",
                "verified_age",
                "birth_date",
                "final_status",
                "level",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("timestamp"),
                    row.get("mode"),
                    row.get("raw_age"),
                    row.get("ai_age"),
                    row.get("verified_age"),
                    row.get("birth_date"),
                    row.get("final_status"),
                    row.get("level"),
                ]
            )

        csv_data = output.getvalue()
        output.close()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        range_tag = ""
        if result.get("start_date") or result.get("end_date"):
            s = result.get("start_date") or "begin"
            e = result.get("end_date") or "today"
            range_tag = f"_{s}_to_{e}"
        filename = f"age_kiosk_history{range_tag}_{ts}.csv"
        return Response(
            csv_data.encode("utf-8-sig"),
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        return _error(f"invalid date filter: {exc}", code=400)
    except Exception as exc:
        return _error(f"monitor export failed: {exc}")


@api_dashboard_bp.get("/monitor/archive/daily.csv")
def monitor_daily_archive_csv():
    try:
        day = request.args.get("date", default=None, type=str)
        if not day:
            day = datetime.now().strftime("%Y-%m-%d")

        result = EngineService.get_history_export_rows(
            limit=0,
            start_date=day,
            end_date=day,
        )
        rows = result.get("items", [])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "timestamp",
                "mode",
                "raw_age",
                "ai_age",
                "verified_age",
                "birth_date",
                "final_status",
                "level",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("timestamp"),
                    row.get("mode"),
                    row.get("raw_age"),
                    row.get("ai_age"),
                    row.get("verified_age"),
                    row.get("birth_date"),
                    row.get("final_status"),
                    row.get("level"),
                ]
            )

        csv_data = output.getvalue()
        output.close()

        filename = f"history_{day}.csv"
        return Response(
            csv_data.encode("utf-8-sig"),
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        return _error(f"invalid date filter: {exc}", code=400)
    except Exception as exc:
        return _error(f"daily archive failed: {exc}")


@api_dashboard_bp.get("/tuning")
def get_tuning():
    try:
        return _ok(EngineService.get_tuning())
    except Exception as exc:
        return _error(f"tuning fetch failed: {exc}")


@api_dashboard_bp.post("/tuning")
def set_tuning():
    try:
        payload = request.get_json(silent=True) or {}
        return _ok(EngineService.set_tuning(payload), message="tuning updated")
    except ValueError as exc:
        return _error(f"invalid tuning payload: {exc}", code=400)
    except Exception as exc:
        return _error(f"tuning update failed: {exc}")


@api_dashboard_bp.post("/reset")
def reset():
    try:
        EngineService.reset()
        return _ok(None, message="engine reset")
    except Exception as exc:
        return _error(f"reset failed: {exc}")


@api_dashboard_bp.post("/key")
def key_input():
    try:
        payload = request.get_json(silent=True) or {}
        result = EngineService.send_key(
            key=payload.get("key"),
            key_code=payload.get("key_code"),
        )
        return _ok(result, message="key processed")
    except Exception as exc:
        return _error(f"key input failed: {exc}")
