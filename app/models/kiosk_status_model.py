from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class KioskStatus:
    state: str = "WAITING"
    face_detected: bool = False
    face_count: int = 0
    frame_luma: Optional[float] = None
    ai_age: Optional[int] = None
    age_stability: Optional[float] = None
    ai_provider: Optional[str] = None
    stable_hits: int = 0
    stable_required: int = 0
    age_stability_threshold: Optional[float] = None
    low_light_luma_min: Optional[float] = None
    reason_code: str = "UNKNOWN"
    reason_message: str = ""
    ocr_msg: str = ""
    ocr_birth: Optional[str] = None
    ocr_age: Optional[int] = None
    ocr_pass: Optional[bool] = None
    final_status: Optional[str] = None
    final_age: Optional[int] = None
    supabase_sync: bool = False
    supabase_queue: int = 0

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "KioskStatus":
        return cls(
            state=payload.get("state", "WAITING"),
            face_detected=bool(payload.get("face_detected", False)),
            face_count=int(payload.get("face_count") or 0),
            frame_luma=payload.get("frame_luma"),
            ai_age=payload.get("ai_age"),
            age_stability=payload.get("age_stability"),
            ai_provider=payload.get("ai_provider"),
            stable_hits=int(payload.get("stable_hits") or 0),
            stable_required=int(payload.get("stable_required") or 0),
            age_stability_threshold=payload.get("age_stability_threshold"),
            low_light_luma_min=payload.get("low_light_luma_min"),
            reason_code=payload.get("reason_code", "UNKNOWN"),
            reason_message=payload.get("reason_message", ""),
            ocr_msg=payload.get("ocr_msg", ""),
            ocr_birth=payload.get("ocr_birth"),
            ocr_age=payload.get("ocr_age"),
            ocr_pass=payload.get("ocr_pass"),
            final_status=payload.get("final_status"),
            final_age=payload.get("final_age"),
            supabase_sync=bool(payload.get("supabase_sync", False)),
            supabase_queue=int(payload.get("supabase_queue") or 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "face_detected": self.face_detected,
            "face_count": self.face_count,
            "frame_luma": self.frame_luma,
            "ai_age": self.ai_age,
            "age_stability": self.age_stability,
            "ai_provider": self.ai_provider,
            "stable_hits": self.stable_hits,
            "stable_required": self.stable_required,
            "age_stability_threshold": self.age_stability_threshold,
            "low_light_luma_min": self.low_light_luma_min,
            "reason_code": self.reason_code,
            "reason_message": self.reason_message,
            "ocr_msg": self.ocr_msg,
            "ocr_birth": self.ocr_birth,
            "ocr_age": self.ocr_age,
            "ocr_pass": self.ocr_pass,
            "final_status": self.final_status,
            "final_age": self.final_age,
            "supabase_sync": self.supabase_sync,
            "supabase_queue": self.supabase_queue,
        }
