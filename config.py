import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _str_env(name: str, default: str = "") -> str:
    raw = os.getenv(name, default)
    return str(raw).strip()


class Config:
    SECRET_KEY = os.getenv("AGE_KIOSK_SECRET_KEY", "age-kiosk-dev-secret")
    CAM_INDEX = _int_env("AGE_KIOSK_CAM_INDEX", 0)
    CAM_INDEX_OCR = _int_env("AGE_KIOSK_CAM_INDEX_OCR", 0)
    STATUS_POLL_MS = _int_env("AGE_KIOSK_STATUS_POLL_MS", 1200)
    STATUS_FALLBACK_POLL_MS = _int_env("AGE_KIOSK_STATUS_FALLBACK_POLL_MS", 3000)
    EVENTS_POLL_MS = _int_env("AGE_KIOSK_EVENTS_POLL_MS", 1000)
    STREAM_FPS = _int_env("AGE_KIOSK_STREAM_FPS", 14)
    METRICS_WINDOW_SEC = _int_env("AGE_KIOSK_METRICS_WINDOW_SEC", 10)
    SUCCESS_WINDOW_SEC = _int_env("AGE_KIOSK_SUCCESS_WINDOW_SEC", 60)
    HISTORY_LIMIT = _int_env("AGE_KIOSK_HISTORY_LIMIT", 3)
    MONITOR_HISTORY_LIMIT = _int_env("AGE_KIOSK_MONITOR_HISTORY_LIMIT", 10)
    MONITOR_POLL_MS = _int_env("AGE_KIOSK_MONITOR_POLL_MS", 3000)

    STABLE_FRAMES_REQUIRED = _int_env("AGE_KIOSK_STABLE_FRAMES_REQUIRED", 5)
    FACE_MOVE_MAX_PX = _int_env("AGE_KIOSK_FACE_MOVE_MAX_PX", 20)
    COOLDOWN_SEC = _float_env("AGE_KIOSK_COOLDOWN_SEC", 5.0)
    AGE_STABILITY_STD_MAX = _float_env("AGE_KIOSK_AGE_STABILITY_STD_MAX", 4.0)
    LOW_LIGHT_LUMA_MIN = _float_env("AGE_KIOSK_LOW_LIGHT_LUMA_MIN", 45.0)
    QUIET_HTTP_LOGS = _bool_env("AGE_KIOSK_QUIET_HTTP_LOGS", True)
    CLOUD_MODE = _bool_env("AGE_KIOSK_CLOUD_MODE", False)
    CLOUD_MAX_IMAGE_MB = _int_env("AGE_KIOSK_CLOUD_MAX_IMAGE_MB", 5)
    CLOUD_INFER_INTERVAL_MS = _int_env("AGE_KIOSK_CLOUD_INFER_INTERVAL_MS", 1200)
    SUPABASE_URL = _str_env("AGE_KIOSK_SUPABASE_URL", "")
    SUPABASE_API_KEY = _str_env("AGE_KIOSK_SUPABASE_API_KEY", "")
    SUPABASE_TABLE = _str_env("AGE_KIOSK_SUPABASE_TABLE", "detection_logs")
    SUPABASE_TIMEOUT_SEC = _float_env("AGE_KIOSK_SUPABASE_TIMEOUT_SEC", 2.0)
