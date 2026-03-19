import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from .supabase_logger import SupabaseLogger


class CloudLogService:
    _lock = threading.Lock()
    _db_path = "age_kiosk.log.db"
    _supabase_enabled = False
    _last_sig: Optional[Tuple[Any, ...]] = None
    _last_ts: float = 0.0
    _min_interval_sec: float = 8.0

    @classmethod
    def configure(
        cls,
        db_path: str = "age_kiosk.log.db",
        supabase_url: str = "",
        supabase_api_key: str = "",
        supabase_table: str = "detection_logs",
        supabase_timeout_sec: float = 2.0,
    ) -> None:
        with cls._lock:
            cls._db_path = str(db_path or "age_kiosk.log.db").strip() or "age_kiosk.log.db"
            cls._supabase_enabled = bool(str(supabase_url or "").strip() and str(supabase_api_key or "").strip())
            if cls._supabase_enabled:
                SupabaseLogger.configure(
                    url=supabase_url,
                    api_key=supabase_api_key,
                    table=supabase_table,
                    timeout_sec=supabase_timeout_sec,
                )
        cls._ensure_table()

    @classmethod
    def _ensure_table(cls) -> None:
        conn = sqlite3.connect(cls._db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                mode TEXT,
                raw_age REAL,
                final_status TEXT,
                birth_date TEXT,
                verified_age INTEGER
            )
            """
        )
        conn.commit()
        conn.close()

    @classmethod
    def _should_log(cls, payload: Dict[str, Any], now_ts: float) -> bool:
        if not isinstance(payload, dict):
            return False
        decision_code = str(payload.get("decision_code") or "")
        if decision_code not in {"FAIL_MINOR", "GREY_VERIFY", "PASS_ADULT"}:
            return False
        face_count = int(payload.get("face_count") or 0)
        if face_count <= 0:
            return False

        sig = (
            decision_code,
            payload.get("ai_age"),
            payload.get("ai_age_range"),
            payload.get("provider"),
        )
        with cls._lock:
            if cls._last_sig == sig and (now_ts - cls._last_ts) < cls._min_interval_sec:
                return False
            cls._last_sig = sig
            cls._last_ts = now_ts
        return True

    @classmethod
    def log_infer_result(cls, payload: Dict[str, Any], mode: str = "CLOUD") -> bool:
        now_ts = datetime.now().timestamp()
        if not cls._should_log(payload, now_ts):
            return False

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw_age = payload.get("ai_age")
        raw_age_val = float(raw_age) if isinstance(raw_age, (int, float)) else None
        final_status = str(payload.get("decision_label") or payload.get("decision_code") or "").strip() or "UNKNOWN"

        conn = sqlite3.connect(cls._db_path)
        conn.execute(
            "INSERT INTO detection_logs VALUES (NULL,?,?,?,?,?,?)",
            (
                timestamp,
                str(mode or "CLOUD"),
                raw_age_val,
                final_status,
                None,
                None,
            ),
        )
        conn.commit()
        conn.close()

        with cls._lock:
            enabled = cls._supabase_enabled
        if enabled:
            SupabaseLogger.enqueue(
                {
                    "timestamp": timestamp,
                    "mode": str(mode or "CLOUD"),
                    "raw_age": raw_age_val,
                    "final_status": final_status,
                    "birth_date": None,
                    "verified_age": None,
                }
            )
        return True
