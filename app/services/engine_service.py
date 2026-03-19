import atexit
import json
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, Generator, Optional, Tuple

from . import age_engine as realtime_age_module
from .age_engine import AgeEngine, CAM_INDEX, CAM_INDEX_OCR

from app.models import KioskStatus


class EngineService:
    _lock = threading.Lock()
    _engine: Optional[AgeEngine] = None
    _cam_face: int = CAM_INDEX
    _cam_ocr: int = CAM_INDEX_OCR
    _frame_interval_sec: float = 1.0 / 20.0
    _event_poll_ms: int = 1000
    _metrics_window_sec: int = 10
    _success_window_sec: int = 60
    _started_at: float = time.time()
    _status_reads: int = 0
    _key_events: int = 0
    _resets: int = 0
    _camera_switches: int = 0
    _stream_frames_out: int = 0
    _stream_clients: int = 0
    _frame_timestamps: Deque[float] = deque(maxlen=5000)
    _status_timestamps: Deque[float] = deque(maxlen=5000)
    _outcome_timestamps: Deque[Tuple[float, bool]] = deque(maxlen=5000)
    _state_transitions: Dict[str, int] = {}
    _last_state: Optional[str] = None
    _last_status: Dict[str, Any] = {}
    _last_outcome_signature: Optional[Tuple[Any, ...]] = None
    _engine_tuning: Dict[str, Any] = {}
    _tuning_schema: Dict[str, Dict[str, Any]] = {
        "STABLE_FRAME_REQUIRED": {
            "type": int,
            "min": 2,
            "max": 15,
            "description": "連續穩定幀數門檻",
        },
        "FACE_MOVE_MAX_DIST": {
            "type": int,
            "min": 5,
            "max": 80,
            "description": "連續幀允許的人臉位移上限(px)",
        },
        "COOLDOWN_SEC": {
            "type": float,
            "min": 1.0,
            "max": 15.0,
            "description": "結果頁停留秒數",
        },
        "AGE_STABILITY_STD_MAX": {
            "type": float,
            "min": 0.5,
            "max": 15.0,
            "description": "年齡波動標準差上限",
        },
        "LOW_LIGHT_LUMA_MIN": {
            "type": float,
            "min": 5.0,
            "max": 120.0,
            "description": "低光門檻(灰階亮度均值)",
        },
    }

    _reason_messages = {
        "NO_FACE": "WAITING FOR PERSON",
        "MULTIPLE_FACES": "偵測到多人，請保持單人入鏡",
        "LOW_LIGHT": "環境過暗，請增加光源",
        "FACE_TOO_SMALL": "人臉太小，請靠近鏡頭",
        "FACE_PARTIAL": "人臉不完整，請置中並完整入鏡",
        "FACE_OUT_OF_ZONE": "請站在鏡頭中央區域",
        "FACE_NOT_FRONTAL": "請正對鏡頭",
        "BLURRY_FACE": "畫面偏模糊，請稍微停住再偵測",
        "STABILIZING": "正在穩定偵測中",
        "ANALYZING": "AI 年齡分析中",
        "DOCUMENT_STEP": "請進行證件拍照流程",
        "DOCUMENT_CAPTURED": "證件拍照完成",
        "MANUAL_INPUT": "請輸入生日完成驗證",
        "RESULT_READY": "結果已產生",
        "UNSTABLE_AGE": "年齡波動較大，建議人工覆核",
    }

    @classmethod
    def _coerce_tuning_value(cls, key: str, raw_value: Any) -> Optional[Any]:
        spec = cls._tuning_schema.get(key)
        if not spec:
            return None
        caster = spec["type"]
        try:
            val = caster(raw_value)
        except Exception:
            return None
        min_v = spec["min"]
        max_v = spec["max"]
        if val < min_v:
            val = min_v
        if val > max_v:
            val = max_v
        return val

    @classmethod
    def _apply_engine_tuning(cls, tuning: Optional[Dict[str, Any]]) -> None:
        if not tuning:
            return
        applied: Dict[str, Any] = {}
        for key in cls._tuning_schema:
            if key not in tuning:
                continue
            value = cls._coerce_tuning_value(key, tuning[key])
            if value is None:
                continue
            setattr(realtime_age_module, key, value)
            applied[key] = value
        cls._engine_tuning.update(applied)

    @classmethod
    def get_tuning(cls) -> Dict[str, Any]:
        with cls._lock:
            values = {
                key: getattr(realtime_age_module, key, None)
                for key in cls._tuning_schema.keys()
            }
            schema = {
                key: {
                    "type": spec["type"].__name__,
                    "min": spec["min"],
                    "max": spec["max"],
                    "description": spec["description"],
                }
                for key, spec in cls._tuning_schema.items()
            }
            return {
                "values": values,
                "schema": schema,
                "last_applied": dict(cls._engine_tuning),
            }

    @classmethod
    def set_tuning(cls, tuning: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(tuning, dict):
            raise ValueError("tuning payload must be an object")

        normalized: Dict[str, Any] = {}
        ignored: Dict[str, Any] = {}
        for key, raw_value in tuning.items():
            if key not in cls._tuning_schema:
                ignored[key] = "unknown key"
                continue
            value = cls._coerce_tuning_value(key, raw_value)
            if value is None:
                ignored[key] = "invalid value"
                continue
            normalized[key] = value

        with cls._lock:
            cls._apply_engine_tuning(normalized)
            current = {
                key: getattr(realtime_age_module, key, None)
                for key in cls._tuning_schema.keys()
            }

        return {
            "applied": normalized,
            "ignored": ignored,
            "current": current,
        }

    @classmethod
    def configure(
        cls,
        cam_face: int,
        cam_ocr: int,
        stream_fps: int,
        events_poll_ms: int = 1000,
        metrics_window_sec: int = 10,
        success_window_sec: int = 60,
        engine_tuning: Optional[Dict[str, Any]] = None,
        supabase_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        with cls._lock:
            cls._cam_face = int(cam_face)
            cls._cam_ocr = int(cam_ocr)
            fps = max(1, int(stream_fps))
            cls._frame_interval_sec = 1.0 / float(fps)
            cls._event_poll_ms = max(200, int(events_poll_ms))
            cls._metrics_window_sec = max(5, int(metrics_window_sec))
            cls._success_window_sec = max(10, int(success_window_sec))
            cls._apply_engine_tuning(engine_tuning)
            if hasattr(realtime_age_module, "configure_supabase"):
                payload = supabase_config or {}
                realtime_age_module.configure_supabase(
                    url=payload.get("url"),
                    api_key=payload.get("api_key"),
                    table=payload.get("table"),
                    timeout_sec=payload.get("timeout_sec"),
                )

    @classmethod
    def get_engine(cls) -> AgeEngine:
        with cls._lock:
            if cls._engine is None:
                cls._engine = AgeEngine(cam_face=cls._cam_face, cam_ocr=cls._cam_ocr)
            return cls._engine

    @classmethod
    def _infer_reason(cls, payload: Dict[str, Any]) -> Tuple[str, str]:
        reason_code = str(payload.get("reason_code") or "").strip()
        reason_message = str(payload.get("reason_message") or "").strip()
        if reason_code and reason_code != "UNKNOWN":
            return reason_code, reason_message or cls._reason_messages.get(reason_code, "")

        state = str(payload.get("state") or "")
        face_count = int(payload.get("face_count") or 0)
        face_detected = bool(payload.get("face_detected"))
        frame_luma = payload.get("frame_luma")
        low_light_min = payload.get("low_light_luma_min")
        age_stability = payload.get("age_stability")
        stability_th = payload.get("age_stability_threshold")

        if state == "WAITING":
            if face_count >= 2:
                return "MULTIPLE_FACES", cls._reason_messages["MULTIPLE_FACES"]
            if isinstance(frame_luma, (int, float)) and isinstance(low_light_min, (int, float)):
                if frame_luma < low_light_min:
                    return "LOW_LIGHT", cls._reason_messages["LOW_LIGHT"]
            if not face_detected:
                return "NO_FACE", cls._reason_messages["NO_FACE"]
            return "STABILIZING", cls._reason_messages["STABILIZING"]

        if state in {"ANALYZING", "PROCESSING"}:
            return "ANALYZING", cls._reason_messages["ANALYZING"]
        if state == "OCR_PENDING":
            return "DOCUMENT_STEP", cls._reason_messages["DOCUMENT_STEP"]
        if state == "DOC_CAPTURED":
            return "DOCUMENT_CAPTURED", cls._reason_messages["DOCUMENT_CAPTURED"]
        if state == "MANUAL_INPUT":
            return "MANUAL_INPUT", cls._reason_messages["MANUAL_INPUT"]
        if state == "COOLDOWN":
            return "RESULT_READY", cls._reason_messages["RESULT_READY"]

        if isinstance(age_stability, (int, float)) and isinstance(stability_th, (int, float)):
            if age_stability > stability_th:
                return "UNSTABLE_AGE", cls._reason_messages["UNSTABLE_AGE"]

        return "UNKNOWN", reason_message

    @classmethod
    def _record_state_transition(cls, state: str) -> None:
        if not state:
            return
        if cls._last_state and cls._last_state != state:
            key = f"{cls._last_state}->{state}"
            cls._state_transitions[key] = cls._state_transitions.get(key, 0) + 1
        cls._last_state = state

    @classmethod
    def _record_outcome(cls, ts: float, payload: Dict[str, Any]) -> None:
        state = str(payload.get("state") or "")
        if state != "COOLDOWN":
            cls._last_outcome_signature = None
            return

        final_status = payload.get("final_status")
        if not final_status:
            return

        signature = (
            final_status,
            payload.get("final_age"),
            payload.get("ocr_birth"),
            payload.get("ai_age"),
        )
        if signature == cls._last_outcome_signature:
            return

        cls._last_outcome_signature = signature
        is_success = str(final_status).startswith("PASS")
        cls._outcome_timestamps.append((ts, is_success))

    @classmethod
    def _prune_metrics(cls, now_ts: float) -> None:
        frame_cutoff = now_ts - float(cls._metrics_window_sec)
        while cls._frame_timestamps and cls._frame_timestamps[0] < frame_cutoff:
            cls._frame_timestamps.popleft()

        status_cutoff = now_ts - float(cls._metrics_window_sec)
        while cls._status_timestamps and cls._status_timestamps[0] < status_cutoff:
            cls._status_timestamps.popleft()

        outcome_cutoff = now_ts - float(cls._success_window_sec)
        while cls._outcome_timestamps and cls._outcome_timestamps[0][0] < outcome_cutoff:
            cls._outcome_timestamps.popleft()

    @classmethod
    def get_status(cls) -> Dict:
        raw = cls.get_engine().get_status()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

        normalized = KioskStatus.from_payload(payload).to_dict()
        reason_code, reason_message = cls._infer_reason(normalized)
        normalized["reason_code"] = reason_code
        normalized["reason_message"] = reason_message

        now_ts = time.time()
        with cls._lock:
            cls._status_reads += 1
            cls._status_timestamps.append(now_ts)
            cls._record_state_transition(str(normalized.get("state") or ""))
            cls._record_outcome(now_ts, normalized)
            cls._last_status = dict(normalized)
            cls._prune_metrics(now_ts)
        return normalized

    @classmethod
    def reset(cls) -> None:
        cls.get_engine()._reset_state()
        with cls._lock:
            cls._resets += 1

    @classmethod
    def send_key(cls, key: Optional[str] = None, key_code: Optional[int] = None) -> Dict:
        engine = cls.get_engine()

        resolved_code: Optional[int] = key_code
        if resolved_code is None and key:
            if len(key) == 1:
                resolved_code = ord(key)
            else:
                named = {
                    "Enter": 13,
                    "Backspace": 8,
                    "Delete": 127,
                    "Escape": 27,
                }
                resolved_code = named.get(key)

        if resolved_code is None:
            return {"handled": False, "reason": "unsupported key"}

        handled = bool(engine.handle_key_event(resolved_code))
        with cls._lock:
            cls._key_events += 1
        return {"handled": handled, "state": engine.state}

    @classmethod
    def stream_frames(cls) -> Generator[bytes, None, None]:
        with cls._lock:
            cls._stream_clients += 1
        try:
            while True:
                frame_bytes = cls.get_engine().get_ui_frame_bytes()
                if frame_bytes:
                    now_ts = time.time()
                    with cls._lock:
                        cls._stream_frames_out += 1
                        cls._frame_timestamps.append(now_ts)
                        cls._prune_metrics(now_ts)
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                    )
                time.sleep(cls._frame_interval_sec)
        finally:
            with cls._lock:
                cls._stream_clients = max(0, cls._stream_clients - 1)

    @classmethod
    def event_stream(cls, poll_interval_ms: int = 1000) -> Generator[str, None, None]:
        effective_poll_ms = int(poll_interval_ms) if poll_interval_ms else cls._event_poll_ms
        interval_sec = max(0.2, effective_poll_ms / 1000.0)
        event_id = 0
        while True:
            status = cls.get_status()
            payload = {
                "type": "status",
                "ts": int(time.time() * 1000),
                "reason_code": status.get("reason_code"),
                "data": status,
            }
            event_id += 1
            yield (
                f"id: {event_id}\n"
                f"event: status\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )
            time.sleep(interval_sec)

    @classmethod
    def switch_camera(
        cls, cam_face: Optional[int] = None, cam_ocr: Optional[int] = None
    ) -> Dict[str, Any]:
        new_cam_face = cls._cam_face if cam_face is None else int(cam_face)
        new_cam_ocr = cls._cam_ocr if cam_ocr is None else int(cam_ocr)

        with cls._lock:
            changed = (new_cam_face != cls._cam_face) or (new_cam_ocr != cls._cam_ocr)
            cls._cam_face = new_cam_face
            cls._cam_ocr = new_cam_ocr
            if cls._engine is not None:
                cls._engine.release()
                cls._engine = None
            cls._camera_switches += 1

        cls.get_engine()
        return {
            "cam_face": new_cam_face,
            "cam_ocr": new_cam_ocr,
            "restarted": True,
            "changed": changed,
        }

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        with cls._lock:
            now_ts = time.time()
            cls._prune_metrics(now_ts)
            uptime_sec = max(0.0, now_ts - cls._started_at)

            fps_current = 0.0
            if len(cls._frame_timestamps) >= 2:
                frame_span = max(0.001, cls._frame_timestamps[-1] - cls._frame_timestamps[0])
                fps_current = (len(cls._frame_timestamps) - 1) / frame_span

            status_rps = 0.0
            if len(cls._status_timestamps) >= 2:
                status_span = max(0.001, cls._status_timestamps[-1] - cls._status_timestamps[0])
                status_rps = (len(cls._status_timestamps) - 1) / status_span

            outcomes_total = len(cls._outcome_timestamps)
            outcomes_pass = sum(1 for _, is_success in cls._outcome_timestamps if is_success)
            success_rate = (outcomes_pass / outcomes_total * 100.0) if outcomes_total else None

            return {
                "uptime_sec": round(uptime_sec, 2),
                "cam_face": cls._cam_face,
                "cam_ocr": cls._cam_ocr,
                "stream_fps_target": round(1.0 / cls._frame_interval_sec, 2),
                "stream_fps_current": round(fps_current, 2),
                "status_rps_current": round(status_rps, 2),
                "metrics_window_sec": cls._metrics_window_sec,
                "success_window_sec": cls._success_window_sec,
                "status_reads": cls._status_reads,
                "key_events": cls._key_events,
                "resets": cls._resets,
                "camera_switches": cls._camera_switches,
                "stream_frames_out": cls._stream_frames_out,
                "stream_clients": cls._stream_clients,
                "outcomes_total": outcomes_total,
                "outcomes_pass": outcomes_pass,
                "success_rate_pct": round(success_rate, 2) if success_rate is not None else None,
                "state_transitions": dict(cls._state_transitions),
                "engine_tuning": dict(cls._engine_tuning),
                "last_status": dict(cls._last_status),
            }

    @classmethod
    def get_recent_history(cls, limit: int = 5) -> Dict[str, Any]:
        try:
            n = max(1, min(int(limit), 20))
        except Exception:
            n = 5
        return cls.get_history_page(limit=n, offset=0)

    @classmethod
    def _normalize_date_value(cls, raw_value: Optional[str]) -> Optional[str]:
        if raw_value is None:
            return None
        txt = str(raw_value).strip()
        if not txt:
            return None
        try:
            dt = datetime.strptime(txt, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except Exception as exc:
            raise ValueError("invalid date format, expected YYYY-MM-DD") from exc

    @classmethod
    def _build_date_predicates(
        cls, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> Tuple[list, list, Optional[str], Optional[str]]:
        start_norm = cls._normalize_date_value(start_date)
        end_norm = cls._normalize_date_value(end_date)
        if start_norm and end_norm and start_norm > end_norm:
            raise ValueError("start_date must be <= end_date")

        predicates = []
        params = []
        if start_norm:
            predicates.append("timestamp >= ?")
            params.append(f"{start_norm} 00:00:00")
        if end_norm:
            predicates.append("timestamp <= ?")
            params.append(f"{end_norm} 23:59:59")
        return predicates, params, start_norm, end_norm

    @classmethod
    def _normalize_history_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        final_status = str(row["final_status"] or "").strip()
        if final_status.startswith("PASS"):
            level = "pass"
        elif final_status:
            level = "alarm"
        else:
            level = "unknown"

        raw_age = row["raw_age"]
        ai_age = int(round(raw_age)) if isinstance(raw_age, (int, float)) else None
        verified_age = row["verified_age"]
        verified_age = int(verified_age) if isinstance(verified_age, (int, float)) else None

        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "mode": row["mode"],
            "raw_age": raw_age,
            "ai_age": ai_age,
            "verified_age": verified_age,
            "birth_date": row["birth_date"],
            "final_status": final_status or "--",
            "level": level,
        }

    @classmethod
    def get_history_page(
        cls,
        limit: int = 10,
        offset: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            n = max(1, min(int(limit), 100))
        except Exception:
            n = 10
        try:
            off = max(0, int(offset))
        except Exception:
            off = 0

        date_predicates, date_params, start_norm, end_norm = cls._build_date_predicates(
            start_date=start_date, end_date=end_date
        )
        date_where_sql = ""
        if date_predicates:
            date_where_sql = "WHERE " + " AND ".join(date_predicates)

        db_path = getattr(realtime_age_module, "DB_PATH", "age_kiosk.log.db")
        total_count = 0
        items = []
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) AS total_count FROM detection_logs {date_where_sql}",
                tuple(date_params),
            )
            total_row = cur.fetchone()
            total_count = int(total_row["total_count"] or 0) if total_row else 0

            cur.execute(
                f"""
                SELECT
                    id,
                    timestamp,
                    mode,
                    raw_age,
                    final_status,
                    birth_date,
                    verified_age
                FROM detection_logs
                {date_where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(date_params + [n, off]),
            )
            rows = cur.fetchall()
            items = [cls._normalize_history_row(r) for r in rows]
            conn.close()
        except Exception:
            total_count = 0
            items = []

        return {
            "limit": n,
            "offset": off,
            "count": len(items),
            "total_count": total_count,
            "has_prev": off > 0,
            "has_next": (off + len(items)) < total_count,
            "start_date": start_norm,
            "end_date": end_norm,
            "items": items,
        }

    @classmethod
    def get_history_export_rows(
        cls,
        limit: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            n = int(limit)
        except Exception:
            n = 0
        n = max(0, min(n, 100000))

        date_predicates, date_params, start_norm, end_norm = cls._build_date_predicates(
            start_date=start_date, end_date=end_date
        )
        date_where_sql = ""
        if date_predicates:
            date_where_sql = "WHERE " + " AND ".join(date_predicates)

        db_path = getattr(realtime_age_module, "DB_PATH", "age_kiosk.log.db")
        items = []
        total_count = 0
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) AS total_count FROM detection_logs {date_where_sql}",
                tuple(date_params),
            )
            total_row = cur.fetchone()
            total_count = int(total_row["total_count"] or 0) if total_row else 0

            if n > 0:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        timestamp,
                        mode,
                        raw_age,
                        final_status,
                        birth_date,
                        verified_age
                    FROM detection_logs
                    {date_where_sql}
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    tuple(date_params + [n]),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        timestamp,
                        mode,
                        raw_age,
                        final_status,
                        birth_date,
                        verified_age
                    FROM detection_logs
                    {date_where_sql}
                    ORDER BY id DESC
                    """,
                    tuple(date_params),
                )
            rows = cur.fetchall()
            items = [cls._normalize_history_row(r) for r in rows]
            conn.close()
        except Exception:
            items = []
            total_count = 0

        return {
            "limit": n,
            "count": len(items),
            "total_count": total_count,
            "start_date": start_norm,
            "end_date": end_norm,
            "items": items,
        }

    @classmethod
    def get_monitor_summary(
        cls,
        history_limit: int = 10,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            n = max(1, min(int(history_limit), 30))
        except Exception:
            n = 10

        date_predicates, date_params, start_norm, end_norm = cls._build_date_predicates(
            start_date=start_date, end_date=end_date
        )
        date_where_sql = ""
        if date_predicates:
            date_where_sql = "WHERE " + " AND ".join(date_predicates)

        db_path = getattr(realtime_age_module, "DB_PATH", "age_kiosk.log.db")
        total_count = 0
        pass_count = 0
        alarm_count = 0
        age_bucket_counts = {
            "0-12": 0,
            "13-17": 0,
            "18-25": 0,
            "26-35": 0,
            "36-45": 0,
            "46-60": 0,
            "61+": 0,
        }

        def _bucket(age_val: int) -> str:
            if age_val <= 12:
                return "0-12"
            if age_val <= 17:
                return "13-17"
            if age_val <= 25:
                return "18-25"
            if age_val <= 35:
                return "26-35"
            if age_val <= 45:
                return "36-45"
            if age_val <= 60:
                return "46-60"
            return "61+"

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN final_status LIKE 'PASS%' THEN 1 ELSE 0 END) AS pass_count
                FROM detection_logs
                {date_where_sql}
                """,
                tuple(date_params),
            )
            row = cur.fetchone()
            if row:
                total_count = int(row["total_count"] or 0)
                pass_count = int(row["pass_count"] or 0)
                alarm_count = max(0, total_count - pass_count)

            age_predicates = ["(verified_age IS NOT NULL OR raw_age IS NOT NULL)"] + list(date_predicates)
            age_where_sql = "WHERE " + " AND ".join(age_predicates)
            cur.execute(
                f"""
                SELECT verified_age, raw_age
                FROM detection_logs
                {age_where_sql}
                ORDER BY id DESC
                LIMIT 3000
                """,
                tuple(date_params),
            )
            age_rows = cur.fetchall()
            for r in age_rows:
                age_val = r["verified_age"]
                if age_val is None and isinstance(r["raw_age"], (int, float)):
                    age_val = int(round(r["raw_age"]))
                if age_val is None:
                    continue
                try:
                    age_int = int(age_val)
                except Exception:
                    continue
                age_bucket_counts[_bucket(age_int)] += 1

            conn.close()
        except Exception:
            total_count = 0
            pass_count = 0
            alarm_count = 0

        recent = cls.get_history_page(
            limit=n,
            offset=0,
            start_date=start_norm,
            end_date=end_norm,
        ).get("items", [])
        pass_rate = round((pass_count / total_count * 100.0), 2) if total_count > 0 else 0.0

        return {
            "summary": {
                "total_count": total_count,
                "pass_count": pass_count,
                "alarm_count": alarm_count,
                "pass_rate_pct": pass_rate,
            },
            "age_distribution": age_bucket_counts,
            "recent_records": recent,
            "history_limit": n,
            "start_date": start_norm,
            "end_date": end_norm,
        }

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            if cls._engine is not None:
                cls._engine.release()
                cls._engine = None


atexit.register(EngineService.shutdown)
