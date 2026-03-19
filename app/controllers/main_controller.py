from flask import Blueprint, current_app, render_template, url_for
from app.services.engine_service import EngineService

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@main_bp.route("/dashboard")
def dashboard():
    if current_app.config.get("CLOUD_MODE", False):
        return render_template(
            "cloud_demo.html",
            title="AI Age Kiosk Cloud Demo",
            cloud_health_api=url_for("api_cloud.health"),
            cloud_infer_api=url_for("api_cloud.infer"),
            infer_interval_ms=current_app.config.get("CLOUD_INFER_INTERVAL_MS", 1200),
        )

    return render_template(
        "index.html",
        title="AI Age Kiosk Dashboard",
        stream_url=url_for("api_dashboard.video_feed"),
        events_api=url_for("api_dashboard.events"),
        status_api=url_for("api_dashboard.status"),
        stats_api=url_for("api_dashboard.stats"),
        history_api=url_for("api_dashboard.history"),
        tuning_api=url_for("api_dashboard.get_tuning"),
        switch_camera_api=url_for("api_dashboard.switch_camera"),
        reset_api=url_for("api_dashboard.reset"),
        key_api=url_for("api_dashboard.key_input"),
        history_limit=current_app.config.get("HISTORY_LIMIT", 5),
        status_poll_ms=current_app.config.get("STATUS_POLL_MS", 1000),
        status_fallback_poll_ms=current_app.config.get("STATUS_FALLBACK_POLL_MS", 3000),
        events_poll_ms=current_app.config.get("EVENTS_POLL_MS", 1000),
    )


@main_bp.route("/monitor")
def monitor():
    # Dashboard page is for stats only; release camera engine to avoid unnecessary runtime load.
    EngineService.shutdown()
    return render_template(
        "monitor.html",
        title="AI Age Kiosk Monitor",
        monitor_summary_api=url_for("api_dashboard.monitor_summary"),
        monitor_history_api=url_for("api_dashboard.monitor_history"),
        monitor_export_api=url_for("api_dashboard.monitor_export_csv"),
        monitor_daily_archive_api=url_for("api_dashboard.monitor_daily_archive_csv"),
        monitor_history_limit=current_app.config.get("MONITOR_HISTORY_LIMIT", 10),
        monitor_poll_ms=current_app.config.get("MONITOR_POLL_MS", 3000),
    )
