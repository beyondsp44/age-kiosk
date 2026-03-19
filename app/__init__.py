from flask import Flask

from app.services.cloud_log_service import CloudLogService
from app.services.engine_service import EngineService


def create_app(config_class: str = "config.Config") -> Flask:
    app = Flask(__name__, template_folder="views", static_folder="static")
    app.config.from_object(config_class)

    if app.config.get("CLOUD_MODE", False):
        EngineService.shutdown()
        CloudLogService.configure(
            db_path="age_kiosk.log.db",
            supabase_url=app.config.get("SUPABASE_URL", ""),
            supabase_api_key=app.config.get("SUPABASE_API_KEY", ""),
            supabase_table=app.config.get("SUPABASE_TABLE", "detection_logs"),
            supabase_timeout_sec=app.config.get("SUPABASE_TIMEOUT_SEC", 2.0),
        )
    else:
        EngineService.configure(
            cam_face=app.config.get("CAM_INDEX", 0),
            cam_ocr=app.config.get("CAM_INDEX_OCR", 0),
            stream_fps=app.config.get("STREAM_FPS", 20),
            events_poll_ms=app.config.get("EVENTS_POLL_MS", 1000),
            metrics_window_sec=app.config.get("METRICS_WINDOW_SEC", 10),
            success_window_sec=app.config.get("SUCCESS_WINDOW_SEC", 60),
            engine_tuning={
                "STABLE_FRAME_REQUIRED": app.config.get("STABLE_FRAMES_REQUIRED", 5),
                "FACE_MOVE_MAX_DIST": app.config.get("FACE_MOVE_MAX_PX", 20),
                "COOLDOWN_SEC": app.config.get("COOLDOWN_SEC", 5.0),
                "AGE_STABILITY_STD_MAX": app.config.get("AGE_STABILITY_STD_MAX", 4.0),
                "LOW_LIGHT_LUMA_MIN": app.config.get("LOW_LIGHT_LUMA_MIN", 45.0),
            },
            supabase_config={
                "url": app.config.get("SUPABASE_URL", ""),
                "api_key": app.config.get("SUPABASE_API_KEY", ""),
                "table": app.config.get("SUPABASE_TABLE", "detection_logs"),
                "timeout_sec": app.config.get("SUPABASE_TIMEOUT_SEC", 2.0),
            },
        )

    from app.controllers.main_controller import main_bp
    from app.controllers.api.dashboard_api import api_dashboard_bp
    from app.controllers.api.kiosk_api import api_kiosk_bp
    from app.controllers.api.cloud_api import api_cloud_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_dashboard_bp)
    app.register_blueprint(api_kiosk_bp)
    app.register_blueprint(api_cloud_bp)

    return app
