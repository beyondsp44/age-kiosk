"""
Age Kiosk - 年齡驗證系統 (重構版)
架構: AgeEngine 類別 (單一職責、無冗餘)
"""
import time
import sys
import os
import re
import threading
import sqlite3
import json
import contextlib
from datetime import date, datetime
from collections import deque

_DLL_DIR_HANDLES = []


def _bootstrap_venv_site_packages():
    """Prepend project venv site-packages so top-right Run and F5 share same deps."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    venv_site = os.path.join(project_root, ".venv", "Lib", "site-packages")
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)
    return venv_site


def _enable_windows_nvidia_dll_search(venv_site: str) -> list[str]:
    """Ensure CUDA dependency DLLs shipped by pip packages are discoverable on Windows."""
    if os.name != "nt" or not venv_site:
        return []

    nvidia_root = os.path.join(venv_site, "nvidia")
    if not os.path.isdir(nvidia_root):
        return []

    path_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    seen = {os.path.normcase(os.path.normpath(p)) for p in path_parts}
    added_dirs: list[str] = []

    try:
        pkg_names = sorted(os.listdir(nvidia_root))
    except Exception:
        pkg_names = []

    for pkg_name in pkg_names:
        bin_dir = os.path.join(nvidia_root, pkg_name, "bin")
        if not os.path.isdir(bin_dir):
            continue

        normalized = os.path.normcase(os.path.normpath(bin_dir))
        if normalized not in seen:
            path_parts.insert(0, bin_dir)
            seen.add(normalized)
            added_dirs.append(bin_dir)

        if hasattr(os, "add_dll_directory"):
            try:
                _DLL_DIR_HANDLES.append(os.add_dll_directory(bin_dir))
            except Exception:
                pass

    if added_dirs:
        os.environ["PATH"] = os.pathsep.join(path_parts)
    return added_dirs


_VENV_SITE = _bootstrap_venv_site_packages()
_NVIDIA_DLL_DIRS = _enable_windows_nvidia_dll_search(_VENV_SITE)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
try:
    from .supabase_logger import SupabaseLogger
except Exception:
    from supabase_logger import SupabaseLogger

try:
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except Exception:
    INSIGHTFACE_AVAILABLE = False

try:
    import onnxruntime as ort
    try:
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
    except Exception:
        pass
except Exception:
    ort = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


@contextlib.contextmanager
def _suppress_ai_init_logs():
    """Keep InsightFace/ORT init noise out of the console unless explicitly enabled."""
    if not _env_bool("AGE_KIOSK_QUIET_AI_INIT_LOGS", True):
        yield
        return

    with open(os.devnull, "w", encoding="utf-8", errors="ignore") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            yield

# ===========================
# ===== 全域設定參數 =====
# ===========================
CAM_INDEX      = 0          # 主鏡頭 (臉部偵測)
CAM_INDEX_OCR  = 0          # OCR 鏡頭 (設為不同數字即啟用雙鏡頭)
DB_PATH        = "age_kiosk.log.db"

WINDOW         = 25         # 年齡平滑窗口大小
FRAME_WIDTH    = 640
FRAME_HEIGHT   = 480
MAIN_LOOP_MAX_FPS = 24
STREAM_JPEG_QUALITY = 88

AGE_MIN        = 18         # 最低合法年齡
AUTO_FAIL_MAX  = 15         # AI 估齡低於此值，直接判定未成年（不進 OCR）
GREY_MAX       = 27         # 灰色地帶上限（超過才放行）=> 灰區為 16-27
AGE_OFFSET     = -3         # 年齡校正偏移 (Facenet512 偏高程度較小)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
INSIGHTFACE_ROOT = os.path.join(PROJECT_ROOT, ".insightface")
INSIGHTFACE_DET_CONF = 0.45
UI_FONT_PATH = r"C:\Windows\Fonts\msjh.ttc"
UI_FONT_CACHE = {}
PRIVACY_NOTICE = "隱私聲明：本系統僅即時辨識，不儲存個人影像資料"

DOC_CAPTURE_HOLD_SEC    = 18.0 # 引導出示證件後，等待多久自動完成拍照動作
DOC_CAPTURE_FLASH_SEC   = 0.8  # 證件拍照完成後，凍結預覽秒數（營造拍照感）
MANUAL_INPUT_TIMEOUT_SEC = 15.0 # 手動輸入生日逾時秒數
DOC_GUIDE_WIDTH         = 520
DOC_GUIDE_HEIGHT        = 320
MANUAL_BIRTH_MAX_LEN    = 6
STABLE_FRAME_REQUIRED   = 6
FACE_MOVE_MAX_DIST      = 14
PRE_CAPTURE_HOLD_SEC    = 1.0  # 觸發分析前，先給使用者短暫定格時間
PRE_CAPTURE_MIN_SAMPLES = 3    # 準備拍攝期間至少收集幾張樣本
PRE_CAPTURE_BLUR_MIN    = 40.0 # 最佳樣本清晰度低於此值則重拍
PRE_CAPTURE_LOST_FACE_RESET_SEC = 1.2  # 準備拍攝時若臉離開超過此秒數，回到 WAITING
FACE_BLUR_MIN_WAITING   = 26.0 # WAITING 階段臉部清晰度門檻（過低不進分析）
COOLDOWN_SEC            = 5.0
COOLDOWN_CLEAR_SEC      = 1.0  # 冷卻後需先偵測到無臉至少此秒數，才允許下一輪
AGE_STABILITY_STD_MAX   = 4.0
LOW_LIGHT_LUMA_MIN      = 45.0
FACE_DETECT_INTERVAL_WAITING = 4
FACE_DETECT_INTERVAL_ACTIVE  = 2
FACE_DETECT_INTERVAL_IDLE    = 12
NO_FACE_IDLE_FRAMES          = 20
FACE_TRIGGER_MIN_WIDTH  = 120  # 低於此寬度視為過遠，不進入啟動流程
FACE_TRIGGER_MIN_AREA_RATIO = 0.06  # 臉面積占畫面比例過小視為非近距離真人
FACE_TRIGGER_ZONE       = (0.2, 0.15, 0.6, 0.7)  # 中央啟動區 (x, y, w, h)
FACE_TRIGGER_MAX_WIDTH_RATIO = 0.62  # 過近上限：臉寬占畫面比例
FACE_TRIGGER_MAX_AREA_RATIO = 0.35   # 過近上限：臉面積占畫面比例
FACE_LOCK_TOP_Y_RATIO = 0.18         # 期望頭頂位置（相對畫面，微下移）
FACE_LOCK_BOTTOM_Y_RATIO = 0.68      # 期望下巴位置（相對畫面，微下移）
FACE_LOCK_Y_TOLERANCE_PX = 34        # 上下鎖定容差
FACE_VERTICAL_LOCK = _env_bool("AGE_KIOSK_FACE_VERTICAL_LOCK", True)
SHOW_POSITION_GUIDE = _env_bool("AGE_KIOSK_SHOW_POSITION_GUIDE", False)
GUIDE_OVERLAP_GATE = _env_bool("AGE_KIOSK_GUIDE_OVERLAP_GATE", True)
GUIDE_OVERLAP_MIN = _env_float("AGE_KIOSK_GUIDE_OVERLAP_MIN", 0.72)
UI_FACE_BOX_COLOR       = (255, 210, 90)         # 人臉框顏色（BGR）：科技藍
UI_COLOR_WAITING        = (255, 220, 90)         # WAITING 主色（青藍）
UI_COLOR_HINT           = (0, 80, 255)           # 一般提示（黃橘）
UI_COLOR_HINT_SOFT      = (0, 120, 255)          # 次提示（淡黃橘）

# DirectML stability tuning
DML_DET_SIZE = (224, 224)
DML_FALLBACK_ERR_THRESHOLD = 12

SUPABASE_URL = str(os.getenv("AGE_KIOSK_SUPABASE_URL", "")).strip()
SUPABASE_API_KEY = str(os.getenv("AGE_KIOSK_SUPABASE_API_KEY", "")).strip()
SUPABASE_TABLE = str(os.getenv("AGE_KIOSK_SUPABASE_TABLE", "detection_logs")).strip() or "detection_logs"
SUPABASE_TIMEOUT_SEC = float(os.getenv("AGE_KIOSK_SUPABASE_TIMEOUT_SEC", "2.0"))
SUPABASE_SYNC_ENABLED = _env_bool("AGE_KIOSK_SUPABASE_SYNC", True) and bool(
    SUPABASE_URL and SUPABASE_API_KEY
)


def configure_supabase(url=None, api_key=None, table=None, timeout_sec=None):
    """Allow Flask config to override Supabase sync settings at runtime."""
    global SUPABASE_URL, SUPABASE_API_KEY, SUPABASE_TABLE, SUPABASE_TIMEOUT_SEC, SUPABASE_SYNC_ENABLED

    if url is not None:
        SUPABASE_URL = str(url or "").strip()
    if api_key is not None:
        SUPABASE_API_KEY = str(api_key or "").strip()
    if table is not None:
        SUPABASE_TABLE = str(table or "detection_logs").strip() or "detection_logs"
    if timeout_sec is not None:
        try:
            SUPABASE_TIMEOUT_SEC = max(0.5, float(timeout_sec))
        except Exception:
            pass

    SUPABASE_SYNC_ENABLED = bool(SUPABASE_URL and SUPABASE_API_KEY)
    if SUPABASE_SYNC_ENABLED:
        SupabaseLogger.configure(
            url=SUPABASE_URL,
            api_key=SUPABASE_API_KEY,
            table=SUPABASE_TABLE,
            timeout_sec=SUPABASE_TIMEOUT_SEC,
        )


# ===========================
# ===== 工具函數 =====
# ===========================

def safe_region(region, pad=0.0):
    """安全地從人臉區域 dict 中取出座標"""
    if not isinstance(region, dict):
        return None
    try:
        x = region.get("x", 0)
        y = region.get("y", 0)
        w = region.get("w", 0)
        h = region.get("h", 0)
        if pad > 0:
            x -= (w * pad) / 2
            y -= (h * pad) / 2
            w *= (1 + pad)
            h *= (1 + pad)
        return {
            "x": int(max(0, x)),
            "y": int(max(0, y)),
            "w": int(w),
            "h": int(h)
        }
    except Exception:
        return None


def insightface_face_to_region(face_obj, img_w, img_h):
    """將 InsightFace face 物件轉成 {x,y,w,h}。"""
    try:
        bbox = getattr(face_obj, "bbox", None)
        if bbox is None or len(bbox) < 4:
            return None
        x1 = int(max(0, min(img_w - 1, bbox[0])))
        y1 = int(max(0, min(img_h - 1, bbox[1])))
        x2 = int(max(0, min(img_w - 1, bbox[2])))
        y2 = int(max(0, min(img_h - 1, bbox[3])))
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        return safe_region({"x": x1, "y": y1, "w": w, "h": h})
    except Exception:
        return None


def crop_roi(img, region, pad=0.35):
    """根據臉部區域裁切 ROI，並回傳左上角偏移量"""
    if not isinstance(region, dict):
        return img, 0, 0
    h, w = img.shape[:2]
    x, y, rw, rh = region["x"], region["y"], region["w"], region["h"]
    cx, cy = x + rw // 2, y + rh // 2
    side = int(max(rw, rh) * (1 + pad) * 2)
    x1 = max(0, cx - side // 2)
    y1 = max(0, cy - side // 2)
    x2 = min(w, cx + side // 2)
    y2 = min(h, cy + side // 2)
    return img[y1:y2, x1:x2].copy(), x1, y1


def calc_age(birth, today=None):
    if today is None:
        today = date.today()
    age = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        age -= 1
    return age


def parse_manual_birth_input(raw: str):
    """僅接受民國 YYMMDD，回傳 date 或 None。"""
    digits = re.sub(r"\D", "", raw or "")
    try:
        if len(digits) != 6:
            return None
        y, m, d = int(digits[:2]) + 1911, int(digits[2:4]), int(digits[4:6])
        return date(y, m, d)
    except Exception:
        return None


def format_manual_birth_display(raw_digits: str):
    digits = re.sub(r"\D", "", raw_digits or "")[:MANUAL_BIRTH_MAX_LEN]
    return digits if len(digits) >= MANUAL_BIRTH_MAX_LEN else f"{digits}_"


def calc_face_blur(frame, region):
    """回傳臉部 ROI 清晰度分數（Laplacian variance）。"""
    if frame is None or region is None:
        return 0.0
    roi, _, _ = crop_roi(frame, region, pad=0.20)
    if roi is None or roi.size == 0:
        return 0.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def trigger_zone_rect(img_w: int, img_h: int):
    zx, zy, zw, zh = FACE_TRIGGER_ZONE
    zx1 = int(img_w * zx)
    zy1 = int(img_h * zy)
    zx2 = int(img_w * (zx + zw))
    zy2 = int(img_h * (zy + zh))
    return zx1, zy1, zx2, zy2


def check_face_quality(region, img_w, img_h):
    """檢查臉部品質，回傳 (ok, hint_message, reason_code)"""
    if not region:
        return False, "未偵測到人臉", "NO_FACE"
    x, y, w, h = region['x'], region['y'], region['w'], region['h']
    area = int(w * h)
    frame_area = int(img_w * img_h)
    margin = 5
    if x < margin or y < margin or (x+w) > (img_w-margin) or (y+h) > (img_h-margin):
        return False, "人臉不完整，請站到畫面中央", "FACE_PARTIAL"
    if w < FACE_TRIGGER_MIN_WIDTH:
        return False, "距離太遠，請再靠近一點", "FACE_TOO_SMALL"
    if area < int(frame_area * FACE_TRIGGER_MIN_AREA_RATIO):
        return False, "距離太遠，請再靠近一點", "FACE_TOO_SMALL"
    if w > int(img_w * FACE_TRIGGER_MAX_WIDTH_RATIO) or area > int(frame_area * FACE_TRIGGER_MAX_AREA_RATIO):
        return False, "距離太近，請後退一點", "FACE_TOO_LARGE"

    zx1, zy1, zx2, zy2 = trigger_zone_rect(img_w, img_h)
    cx, cy = x + w // 2, y + h // 2
    if cx < zx1:
        return False, "請往畫面右側移動一點", "FACE_OUT_OF_ZONE"
    if cx > zx2:
        return False, "請往畫面左側移動一點", "FACE_OUT_OF_ZONE"
    if cy < zy1:
        return False, "請稍微往下", "FACE_OUT_OF_ZONE"
    if cy > zy2:
        return False, "請稍微往上", "FACE_OUT_OF_ZONE"

    # Vertical sweet-spot lock by head-top to chin span.
    # This can be disabled to approximate the older GPU-only baseline behavior.
    if FACE_VERTICAL_LOCK:
        target_top = int(img_h * FACE_LOCK_TOP_Y_RATIO)
        target_bottom = int(img_h * FACE_LOCK_BOTTOM_Y_RATIO)
        target_cy = (target_top + target_bottom) // 2
        if abs(cy - target_cy) > FACE_LOCK_Y_TOLERANCE_PX:
            if cy < target_cy:
                return False, "請稍微往下", "FACE_VERTICAL_LOCK"
            return False, "請稍微往上", "FACE_VERTICAL_LOCK"

    # 縱橫比放寬（允許略微側臉）
    aspect = w / max(h, 1)
    if aspect < 0.5 or aspect > 1.5:
        return False, "請正對鏡頭", "FACE_NOT_FRONTAL"
    return True, "已符合偵測條件", "OK"


def configure_console_encoding():
    """避免 Windows cp950 在第三方套件輸出特殊字元時崩潰。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


def get_ui_font(size=24):
    key = int(size)
    font = UI_FONT_CACHE.get(key)
    if font is not None:
        return font
    try:
        font = ImageFont.truetype(UI_FONT_PATH, key)
    except Exception:
        font = ImageFont.load_default()
    UI_FONT_CACHE[key] = font
    return font


def put_ui_text(img, text, org, size=24, color=(255, 255, 255)):
    """用 Pillow 畫中文，避免 cv2.putText 無法正常顯示繁中。"""
    if img is None:
        return
    x, y = org
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_img)
    text_kwargs = {
        "font": get_ui_font(size),
        "fill": (int(color[2]), int(color[1]), int(color[0])),
    }
    # Keep text crisp on video while avoiding heavy "blob" artifacts.
    bgr = (int(color[0]), int(color[1]), int(color[2]))
    is_dark_text = max(bgr) < 90
    stroke_w = 1 if int(size) >= 20 else 0
    stroke_fill = (245, 245, 245) if is_dark_text else (8, 8, 8)
    try:
        draw.text(
            (x, y),
            str(text),
            stroke_width=stroke_w,
            stroke_fill=stroke_fill,
            **text_kwargs,
        )
    except TypeError:
        draw.text((x, y), str(text), **text_kwargs)
    img[:] = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def ui_status_text(status):
    mapping = {
        "PASS": "通過",
        "PASS (Adult)": "成年，通過",
        "FAIL (Minor)": "警告，未成年",
        "SUSPECTED ID FRAUD": "請店員協助確認",
    }
    return mapping.get(status, status or "未知狀態")


def age_to_display_range(age_value):
    """Convert a numeric age to a front-stage display range."""
    if age_value is None:
        return None
    try:
        age = int(round(float(age_value)))
    except Exception:
        return None
    low = max(0, age - 3)
    high = age + 3
    return f"{low}-{high} 歲"


def draw_privacy_notice(img):
    """Draw a privacy notice footer on UI frame."""
    if img is None:
        return
    h, w = img.shape[:2]
    bar_h = 28
    cv2.rectangle(img, (0, h - bar_h), (w, h), (15, 15, 15), -1)
    put_ui_text(img, PRIVACY_NOTICE, (12, h - bar_h + 3), size=18, color=(210, 210, 210))


def draw_face_corner_box(img, region, color=UI_FACE_BOX_COLOR, thickness=2):
    """Draw a face box using only 4 corner brackets."""
    if img is None or not region:
        return
    x, y = int(region["x"]), int(region["y"])
    w, h = int(region["w"]), int(region["h"])
    if w < 8 or h < 8:
        return
    corner = max(14, int(min(w, h) * 0.22))

    # top-left
    cv2.line(img, (x, y), (x + corner, y), color, thickness)
    cv2.line(img, (x, y), (x, y + corner), color, thickness)
    # top-right
    cv2.line(img, (x + w, y), (x + w - corner, y), color, thickness)
    cv2.line(img, (x + w, y), (x + w, y + corner), color, thickness)
    # bottom-left
    cv2.line(img, (x, y + h), (x + corner, y + h), color, thickness)
    cv2.line(img, (x, y + h), (x, y + h - corner), color, thickness)
    # bottom-right
    cv2.line(img, (x + w, y + h), (x + w - corner, y + h), color, thickness)
    cv2.line(img, (x + w, y + h), (x + w, y + h - corner), color, thickness)


def position_guide_rect(img_w: int, img_h: int):
    target_top = int(img_h * FACE_LOCK_TOP_Y_RATIO)
    target_bottom = int(img_h * FACE_LOCK_BOTTOM_Y_RATIO)
    target_h = max(160, target_bottom - target_top)
    # Keep guide size close to the observed "best overlap" face box.
    target_w = max(120, int(target_h * 0.74))
    cx = img_w // 2
    cy = (target_top + target_bottom) // 2
    x1 = max(0, cx - target_w // 2)
    y1 = max(0, cy - target_h // 2)
    x2 = min(img_w - 1, cx + target_w // 2)
    y2 = min(img_h - 1, cy + target_h // 2)
    return x1, y1, x2, y2


def calc_face_guide_iou(region, img_w: int, img_h: int) -> float:
    if not region:
        return 0.0
    rx1 = int(region.get("x", 0))
    ry1 = int(region.get("y", 0))
    rw = int(region.get("w", 0))
    rh = int(region.get("h", 0))
    if rw <= 1 or rh <= 1:
        return 0.0
    rx2 = rx1 + rw
    ry2 = ry1 + rh
    gx1, gy1, gx2, gy2 = position_guide_rect(img_w, img_h)

    ix1 = max(rx1, gx1)
    iy1 = max(ry1, gy1)
    ix2 = min(rx2, gx2)
    iy2 = min(ry2, gy2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = float(iw * ih)
    if inter <= 0:
        return 0.0
    area_r = float(max(1, rw * rh))
    area_g = float(max(1, (gx2 - gx1) * (gy2 - gy1)))
    union = area_r + area_g - inter
    if union <= 0:
        return 0.0
    return inter / union


def guide_alignment_hint(region, img_w: int, img_h: int) -> str:
    """Human-readable alignment hint when guide box is hidden."""
    if not region:
        return "請站在鏡頭中央並保持距離"

    gx1, gy1, gx2, gy2 = position_guide_rect(img_w, img_h)
    gw = max(1, gx2 - gx1)
    gh = max(1, gy2 - gy1)

    x = int(region.get("x", 0))
    y = int(region.get("y", 0))
    w = int(region.get("w", 0))
    h = int(region.get("h", 0))
    if w <= 1 or h <= 1:
        return "請站在鏡頭中央並保持距離"

    cx = x + w // 2
    cy = y + h // 2

    # Keep distance first to reduce face-size drift.
    if w < int(gw * 0.84) or h < int(gh * 0.84):
        return "請靠近一點"
    if w > int(gw * 1.18) or h > int(gh * 1.18):
        return "請後退一點"

    if cx < gx1:
        return "請往畫面右側移動一點"
    if cx > gx2:
        return "請往畫面左側移動一點"
    if cy < gy1:
        return "請稍微往下"
    if cy > gy2:
        return "請稍微往上"
    return "請站在鏡頭中央並保持距離"


def draw_position_guide(img):
    """Optional visual guide for alignment (disabled by default)."""
    if img is None:
        return
    img_h, img_w = img.shape[:2]
    x1, y1, x2, y2 = position_guide_rect(img_w, img_h)

    # Single thin green line for a cleaner look (no brighter corner accents).
    guide_color = (90, 190, 115)
    cv2.rectangle(img, (x1, y1), (x2, y2), guide_color, 1)


# ===========================
# ===== 主引擎類別 =====
# ===========================

class AgeEngine:
    def __init__(self, cam_face=CAM_INDEX, cam_ocr=CAM_INDEX_OCR):
        # 攝影機初始化
        self.cap_face = cv2.VideoCapture(cam_face)
        self.cap_face.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        self.cap_face.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        # Keep camera queue shallow to reduce latency and CPU spikes.
        try:
            self.cap_face.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        self.cap_ocr = None
        if cam_ocr != cam_face:
            ocr_cap = cv2.VideoCapture(cam_ocr)
            if ocr_cap.isOpened():
                self.cap_ocr = ocr_cap
                self.cap_ocr.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
                self.cap_ocr.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
                try:
                    self.cap_ocr.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

        # OCR 已停用：保留證件拍照引導流程，但不做文字辨識
        print("[OCR] Disabled (manual birthday input mode).")

        # 年齡估測後端初始化（InsightFace，優先 GPU，失敗回退 CPU）
        self.insight_app = None
        self.ai_provider = "CPUExecutionProvider"
        if not INSIGHTFACE_AVAILABLE:
            raise RuntimeError("InsightFace 未安裝，請先安裝 insightface。")
        try:
            os.makedirs(os.path.join(INSIGHTFACE_ROOT, "models"), exist_ok=True)
            available_providers = []
            if ort is not None:
                try:
                    available_providers = list(ort.get_available_providers())
                except Exception:
                    available_providers = []

            has_dml = "DmlExecutionProvider" in available_providers
            has_cuda = "CUDAExecutionProvider" in available_providers
            enable_dml = _env_bool("AGE_KIOSK_ENABLE_DML", False)
            # On Windows we prefer DirectML first for stability, then CUDA, then CPU.
            if has_dml and enable_dml:
                providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
                primary_provider = "DmlExecutionProvider"
            elif has_cuda:
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                primary_provider = "CUDAExecutionProvider"
            else:
                providers = ["CPUExecutionProvider"]
                primary_provider = "CPUExecutionProvider"
            ctx_id = 0 if primary_provider in {"DmlExecutionProvider", "CUDAExecutionProvider"} else -1

            def _create_face_app(provider_list, use_ctx_id):
                with _suppress_ai_init_logs():
                    try:
                        app = FaceAnalysis(
                            name="buffalo_l",
                            root=INSIGHTFACE_ROOT,
                            providers=provider_list,
                            allowed_modules=["detection", "genderage"],
                        )
                    except TypeError:
                        app = FaceAnalysis(name="buffalo_l", root=INSIGHTFACE_ROOT)
                    try:
                        det_size = DML_DET_SIZE if "DmlExecutionProvider" in provider_list else (256, 256)
                        app.prepare(ctx_id=use_ctx_id, det_size=det_size)
                    except TypeError:
                        app.prepare(ctx_id=use_ctx_id)
                return app
            self._create_face_app_fn = _create_face_app

            try:
                self.insight_app = _create_face_app(providers, ctx_id)
                self.ai_provider = primary_provider
            except Exception as provider_exc:
                if primary_provider in {"CUDAExecutionProvider", "DmlExecutionProvider"}:
                    print(f"[AI] {primary_provider} init failed, fallback to CPU: {provider_exc}")
                    self.insight_app = _create_face_app(["CPUExecutionProvider"], -1)
                    self.ai_provider = "CPUExecutionProvider"
                else:
                    raise

            print(f"[AI] Age backend: insightface provider={self.ai_provider} ({INSIGHTFACE_ROOT})")
        except Exception as e:
            raise RuntimeError(f"InsightFace 初始化失敗: {e}") from e

        # 資料庫初始化
        self._init_db()
        configure_supabase()
        if SUPABASE_SYNC_ENABLED:
            print(f"[Supabase] Sync enabled -> table={SUPABASE_TABLE}")
        else:
            print("[Supabase] Sync disabled (set AGE_KIOSK_SUPABASE_URL/API_KEY to enable).")

        # 狀態機變數
        self._reset_state()
        self.running    = True
        self.i          = 0
        self.ui_frame   = None
        self.captured_ui_frame = None
        self.ocr_frame_buffer  = None  # 最新一幀的 OCR 鏡頭畫面

        # 啟動背景主迴圈執行緒
        self.no_face_frames    = 0

        self._insight_lock = threading.Lock()
        self._provider_runtime_error_count = 0
        self._provider_last_error = ""
        self._detect_lock = threading.Lock()
        self._detect_request_pending = False
        self._detect_request_frame = None
        self._detect_request_zone_only = False
        self._detect_face_count = 0
        self._detect_region = None
        self._detect_thread = threading.Thread(target=self._detect_loop, daemon=True)
        self._detect_thread.start()

        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()

    def _reset_state(self):
        """重置所有狀態變數到初始值"""
        self.state               = "WAITING"
        self.ages                = deque(maxlen=WINDOW)
        self.last_region         = None
        self.stable_frame_count  = 0
        self.last_pos            = (0, 0)
        self.doc_mode_start      = 0
        self.cooldown_until      = 0
        self.final_result_data   = {}
        self.verify_birth        = None
        self.verify_age          = None
        self.verify_pass         = None
        self.verify_msg          = ""
        self.manual_input_buffer = ""
        self.manual_input_start  = 0
        self.face_count          = 0
        self.frame_luma          = 0.0
        self.no_face_frames      = 0
        self.reason_code         = "NO_FACE"
        self.reason_message      = "WAITING FOR PERSON"
        self.cooldown_clear_start = 0.0
        self.pre_capture_start   = 0.0
        self.pre_capture_samples = []
        self.pre_capture_lost_face_since = 0.0
        self.doc_capture_preview = None
        self.doc_capture_preview_until = 0.0

    def _set_reason(self, code: str, message: str):
        self.reason_code = str(code or "UNKNOWN")
        self.reason_message = str(message or "")

    def _normalize_face_input(self, img):
        if img is None or not isinstance(img, np.ndarray) or img.size == 0:
            return None
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        if not img.flags["C_CONTIGUOUS"]:
            img = np.ascontiguousarray(img)
        return img

    def _fallback_provider_to_cpu(self, cause: str = ""):
        if self.ai_provider == "CPUExecutionProvider":
            return
        try:
            if hasattr(self, "_create_face_app_fn"):
                cpu_app = self._create_face_app_fn(["CPUExecutionProvider"], -1)
            else:
                cpu_app = FaceAnalysis(name="buffalo_l", root=INSIGHTFACE_ROOT, providers=["CPUExecutionProvider"])
                try:
                    cpu_app.prepare(ctx_id=-1, det_size=(256, 256))
                except TypeError:
                    cpu_app.prepare(ctx_id=-1)
            with self._insight_lock:
                self.insight_app = cpu_app
            self.ai_provider = "CPUExecutionProvider"
            print(f"[AI] Provider fallback -> CPUExecutionProvider ({cause})")
        except Exception as e:
            print(f"[AI] Provider fallback failed: {e}")

    def _safe_get_faces(self, img):
        if self.insight_app is None:
            return []
        img = self._normalize_face_input(img)
        if img is None:
            return []
        try:
            with self._insight_lock:
                faces = self.insight_app.get(img)
            self._provider_runtime_error_count = 0
            return faces or []
        except Exception as e:
            err_msg = str(e)
            if self.ai_provider in {"DmlExecutionProvider", "CUDAExecutionProvider"}:
                self._provider_runtime_error_count += 1
                provider_tag = "DML" if self.ai_provider == "DmlExecutionProvider" else "CUDA"
                # Avoid log spam but keep first/important diagnostics.
                if self._provider_runtime_error_count in {1, 3, 6}:
                    print(f"[AI] {provider_tag} runtime error x{self._provider_runtime_error_count}: {err_msg}")
                self._provider_last_error = err_msg
                err_lower = err_msg.lower()
                if self.ai_provider == "DmlExecutionProvider":
                    if self._provider_runtime_error_count >= DML_FALLBACK_ERR_THRESHOLD:
                        self._fallback_provider_to_cpu(cause="DML runtime unstable")
                else:
                    cuda_hard_fail = ("error loading" in err_lower and "which is missing" in err_lower) or (
                        "cudaexecutionprovider" in err_lower
                    )
                    if cuda_hard_fail or self._provider_runtime_error_count >= 3:
                        self._fallback_provider_to_cpu(cause="CUDA runtime unavailable")
            return []

    def _estimate_age(self, face_img):
        if self.insight_app is None:
            return None
        try:
            faces = self._safe_get_faces(face_img)
            if not faces:
                return None
            best = max(
                faces,
                key=lambda f: max(0.0, float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])))
            )
            if hasattr(best, "age") and best.age is not None:
                return float(best.age)
        except Exception:
            return None
        return None

    def _run_face_detect(self, frame, zone_only=False):
        face_count = 0
        region = None
        try:
            det_frame = frame
            base_x = 0
            base_y = 0
            if zone_only:
                zx, zy, zw, zh = FACE_TRIGGER_ZONE
                x1 = max(0, int(FRAME_WIDTH * zx))
                y1 = max(0, int(FRAME_HEIGHT * zy))
                x2 = min(FRAME_WIDTH, int(FRAME_WIDTH * (zx + zw)))
                y2 = min(FRAME_HEIGHT, int(FRAME_HEIGHT * (zy + zh)))
                if x2 - x1 > 20 and y2 - y1 > 20:
                    det_frame = frame[y1:y2, x1:x2].copy()
                    base_x, base_y = x1, y1

            faces = self._safe_get_faces(det_frame)
            face_count = int(len(faces))
            if faces and face_count == 1:
                best = max(
                    faces,
                    key=lambda f: max(0.0, float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])))
                )
                det_score = float(getattr(best, "det_score", 1.0))
                if det_score >= INSIGHTFACE_DET_CONF:
                    local_w = det_frame.shape[1]
                    local_h = det_frame.shape[0]
                    local_region = insightface_face_to_region(best, local_w, local_h)
                    if local_region:
                        region = {
                            "x": int(local_region["x"] + base_x),
                            "y": int(local_region["y"] + base_y),
                            "w": int(local_region["w"]),
                            "h": int(local_region["h"]),
                        }
        except Exception:
            face_count = 0
            region = None
        return face_count, region

    def _detect_loop(self):
        while self.running:
            frame = None
            zone_only = False
            with self._detect_lock:
                if self._detect_request_pending and self._detect_request_frame is not None:
                    frame = self._detect_request_frame
                    zone_only = bool(self._detect_request_zone_only)
                    self._detect_request_frame = None
                    self._detect_request_pending = False
            if frame is None:
                time.sleep(0.003)
                continue

            face_count, region = self._run_face_detect(frame, zone_only=zone_only)
            with self._detect_lock:
                self._detect_face_count = face_count
                self._detect_region = region

    def _enqueue_detect_frame(self, frame, zone_only=False):
        with self._detect_lock:
            if self._detect_request_pending:
                return
            self._detect_request_frame = frame.copy()
            self._detect_request_zone_only = bool(zone_only)
            self._detect_request_pending = True

    def _pull_detect_result(self):
        with self._detect_lock:
            return int(self._detect_face_count), self._detect_region

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS detection_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                mode        TEXT,
                raw_age     REAL,
                final_status TEXT,
                birth_date  TEXT,
                verified_age INTEGER
            )
        ''')
        conn.commit()
        conn.close()

    def _save_record(self, mode, raw_age, status, birth=None, v_age=None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO detection_logs VALUES (NULL,?,?,?,?,?,?)",
                (
                    timestamp,
                    mode,
                    raw_age,
                    status,
                    str(birth) if birth else None,
                    v_age,
                )
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB] 寫入失敗: {e}")

        if SUPABASE_SYNC_ENABLED:
            raw_age_val = None
            if isinstance(raw_age, (int, float)):
                raw_age_val = float(raw_age)
            verified_age_val = None
            if isinstance(v_age, (int, float)):
                verified_age_val = int(v_age)
            SupabaseLogger.enqueue(
                {
                    "timestamp": timestamp,
                    "mode": str(mode or ""),
                    "raw_age": raw_age_val,
                    "final_status": str(status or ""),
                    "birth_date": str(birth) if birth else None,
                    "verified_age": verified_age_val,
                }
            )

    def _apply_birth_verification(self, birth, estimated_ai_age, mode="MANUAL"):
        """用生日做最終驗證。"""
        v_age  = calc_age(birth)
        v_pass = (v_age >= AGE_MIN)

        suspicious = False
        if estimated_ai_age is not None and abs(estimated_ai_age - v_age) > 15:
            suspicious = True

        final_pass = v_pass and not suspicious
        status_str = "PASS" if final_pass else ("SUSPECTED ID FRAUD" if suspicious else "FAIL (Minor)")
        msg = ""
        if suspicious:
            ai_range = age_to_display_range(estimated_ai_age) if estimated_ai_age is not None else ""
            if ai_range:
                msg = f"AI估測區間 {ai_range}，輸入年齡 {v_age} 歲，請店員確認"
            else:
                msg = f"AI估測與輸入年齡差距較大，輸入年齡 {v_age} 歲，請店員確認"

        self.final_result_data = {
            "status": status_str,
            "color":  (0, 255, 0) if final_pass else (0, 0, 255),
            "age":    v_age,
            "source": "MANUAL",
            "ai_age": int(round(estimated_ai_age)) if estimated_ai_age is not None else None,
            "ai_range": age_to_display_range(estimated_ai_age),
            "msg":    msg
        }
        self.verify_birth = birth
        self.verify_age   = v_age
        self.verify_pass  = v_pass
        self.state        = "COOLDOWN"
        self.cooldown_until = time.time() + COOLDOWN_SEC
        self._set_reason("RESULT_READY", "結果已產生")
        self._save_record(mode, estimated_ai_age, status_str, birth, v_age)

    # ===========================
    # 主迴圈 (背景執行緒)
    # ===========================
    def _main_loop(self):
        while self.running:
            loop_started = time.perf_counter()
            ret, frame = self.cap_face.read()
            if not ret:
                time.sleep(0.02)
                continue

            self.i  += 1
            now      = time.time()
            ip_ui    = frame.copy()
            self.frame_luma = float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))

            # OCR 鏡頭更新 (若有)
            if self.cap_ocr:
                ret_o, ocr_f = self.cap_ocr.read()
                if ret_o:
                    self.ocr_frame_buffer = ocr_f

            # 臉部追蹤降頻，PROCESSING 階段不再執行偵測以提升流暢度
            detect_zone_only = False
            if self.state == "WAITING":
                detect_interval = (
                    FACE_DETECT_INTERVAL_IDLE
                    if self.no_face_frames >= NO_FACE_IDLE_FRAMES
                    else FACE_DETECT_INTERVAL_WAITING
                )
                detect_tick = (self.i % max(1, detect_interval) == 0)
                detect_zone_only = True
            elif self.state == "COOLDOWN":
                detect_tick = (self.i % max(1, FACE_DETECT_INTERVAL_ACTIVE) == 0)
                detect_zone_only = False
            elif self.state == "ANALYZING":
                detect_tick = (self.i % FACE_DETECT_INTERVAL_ACTIVE == 0)
            else:
                detect_tick = False
            if detect_tick:
                self._enqueue_detect_frame(frame, zone_only=detect_zone_only)

            self.face_count, self.last_region = self._pull_detect_result()
            face_detected = (self.last_region is not None)
            valid_capture_face = face_detected
            if self.state == "WAITING" and face_detected:
                valid_capture_face, _, reason = check_face_quality(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                # Treat far-away faces as "no face" in WAITING to avoid noisy candidate states.
                if reason == "FACE_TOO_SMALL":
                    face_detected = False
                    self.face_count = 0
                    self.last_region = None
                    valid_capture_face = False

            if valid_capture_face:
                self.no_face_frames = 0
            else:
                self.no_face_frames = min(self.no_face_frames + 1, NO_FACE_IDLE_FRAMES * 2)

            if SHOW_POSITION_GUIDE and self.state in {"WAITING", "ANALYZING"}:
                draw_position_guide(ip_ui)

            # 執行狀態機
            self._handle_states(ip_ui, frame, now, face_detected)

            # 繪製臉部追蹤框 (WAITING/ANALYZING 時才畫)
            if self.last_region and self.state in ["WAITING", "ANALYZING", "PROCESSING"]:
                draw_region = self.last_region
                box_color = UI_FACE_BOX_COLOR
                if self.state == "WAITING":
                    ok_to_draw, _, reason = check_face_quality(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                    if not ok_to_draw:
                        # Keep WAITING stage clean: if user is still far/off-target, don't highlight a face box.
                        if reason in {"FACE_TOO_SMALL", "FACE_OUT_OF_ZONE", "FACE_PARTIAL"}:
                            draw_region = None
                if draw_region is not None:
                    draw_face_corner_box(ip_ui, draw_region, box_color, 2)

            draw_privacy_notice(ip_ui)
            self.ui_frame = ip_ui
            if MAIN_LOOP_MAX_FPS > 0:
                loop_elapsed = time.perf_counter() - loop_started
                target_dt = 1.0 / float(MAIN_LOOP_MAX_FPS)
                if loop_elapsed < target_dt:
                    time.sleep(target_dt - loop_elapsed)

    # ===========================
    # 狀態機
    # ===========================
    def _handle_states(self, ip_ui, frame, now, face_detected):
        # --- WAITING ---
        if self.state == "WAITING":
            if self.face_count >= 2:
                self.stable_frame_count = 0
                self._set_reason("MULTIPLE_FACES", "偵測到多人，請保持單人入鏡")
                put_ui_text(ip_ui, self.reason_message, (20, 18), size=24, color=UI_COLOR_HINT)
            elif self.frame_luma < LOW_LIGHT_LUMA_MIN:
                self.stable_frame_count = 0
                self._set_reason("LOW_LIGHT", "環境過暗，請增加光源")
                put_ui_text(ip_ui, self.reason_message, (20, 18), size=24, color=UI_COLOR_HINT)
            elif face_detected:
                ok, hint, reason_code = check_face_quality(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                if not ok:
                    self.stable_frame_count = 0
                    self._set_reason(reason_code, hint)
                    put_ui_text(ip_ui, hint, (20, 18), size=24, color=UI_COLOR_HINT)
                else:
                    # Gate can stay active even when guide is hidden (production mode).
                    if GUIDE_OVERLAP_GATE:
                        iou = calc_face_guide_iou(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                        if iou < GUIDE_OVERLAP_MIN:
                            self.stable_frame_count = 0
                            align_msg = (
                                "請再對準綠框後拍攝"
                                if SHOW_POSITION_GUIDE
                                else guide_alignment_hint(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                            )
                            self._set_reason("GUIDE_NOT_ALIGNED", align_msg)
                            put_ui_text(ip_ui, align_msg, (20, 18), size=24, color=UI_COLOR_HINT)
                            return
                    blur_score = calc_face_blur(frame, self.last_region)
                    if blur_score < FACE_BLUR_MIN_WAITING:
                        self.stable_frame_count = 0
                        self._set_reason("BLURRY_FACE", "畫面偏模糊，請稍微停住再偵測")
                        put_ui_text(ip_ui, "畫面偏模糊，請稍微停住再偵測", (20, 18), size=24, color=UI_COLOR_HINT)
                        return

                    curr = (self.last_region['x'], self.last_region['y'])
                    dist = np.hypot(curr[0]-self.last_pos[0], curr[1]-self.last_pos[1])
                    self.last_pos = curr
                    if dist < FACE_MOVE_MAX_DIST:
                        self.stable_frame_count += 1
                    else:
                        self.stable_frame_count = 0
                    if self.stable_frame_count >= STABLE_FRAME_REQUIRED:
                        self.ages.clear()
                        self.stable_frame_count = 0
                        self.pre_capture_start = now
                        self.pre_capture_samples = []
                        self.pre_capture_lost_face_since = 0.0
                        self._set_reason("PRE_CAPTURE", "請保持不動，準備拍攝")
                        self.state = "ANALYZING"
                    else:
                        self._set_reason("STABILIZING", "正在穩定偵測中")
                        put_ui_text(
                            ip_ui,
                            f"正在穩定偵測中 ({self.stable_frame_count}/{STABLE_FRAME_REQUIRED})",
                            (20, 18),
                            size=24,
                            color=(0, 220, 0)
                        )
            else:
                self.stable_frame_count = 0
                self._set_reason("NO_FACE", "WAITING FOR PERSON")
                self.verify_msg = ""
                put_ui_text(ip_ui, "WAITING FOR PERSON", (20, 18), size=26, color=UI_COLOR_WAITING)

        # --- ANALYZING (準備拍攝 + 擇優取幀) ---
        elif self.state == "ANALYZING":
            self._set_reason("PRE_CAPTURE", "請保持不動，準備拍攝")
            elapsed = now - self.pre_capture_start if self.pre_capture_start > 0 else 0.0
            rem = max(0.0, PRE_CAPTURE_HOLD_SEC - elapsed)
            put_ui_text(ip_ui, f"請保持不動，準備拍攝 ({rem:.1f}s)", (20, 18), size=24, color=(0, 220, 255))

            if not face_detected or not self.last_region:
                self.pre_capture_samples = []
                if self.pre_capture_lost_face_since <= 0:
                    self.pre_capture_lost_face_since = now
                lost_sec = now - self.pre_capture_lost_face_since
                self.pre_capture_start = now
                if lost_sec >= PRE_CAPTURE_LOST_FACE_RESET_SEC:
                    self.pre_capture_lost_face_since = 0.0
                    self.pre_capture_samples = []
                    self.pre_capture_start = 0.0
                    self.state = "WAITING"
                    self._set_reason("NO_FACE", "WAITING FOR PERSON")
                    self.verify_msg = ""
                    put_ui_text(ip_ui, "WAITING FOR PERSON", (20, 50), size=20, color=UI_COLOR_WAITING)
                    return
                put_ui_text(ip_ui, "臉部未穩定，重新對焦中", (20, 50), size=20, color=UI_COLOR_HINT_SOFT)
                return

            self.pre_capture_lost_face_since = 0.0
            ok, hint, reason_code = check_face_quality(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
            if not ok:
                self.pre_capture_samples = []
                self.pre_capture_start = now
                self._set_reason(reason_code, hint)
                put_ui_text(ip_ui, hint, (20, 50), size=20, color=UI_COLOR_HINT_SOFT)
                return

            if GUIDE_OVERLAP_GATE:
                iou = calc_face_guide_iou(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                if iou < GUIDE_OVERLAP_MIN:
                    self.pre_capture_samples = []
                    self.pre_capture_start = now
                    align_msg = (
                        "請再對準綠框後拍攝"
                        if SHOW_POSITION_GUIDE
                        else guide_alignment_hint(self.last_region, FRAME_WIDTH, FRAME_HEIGHT)
                    )
                    self._set_reason("GUIDE_NOT_ALIGNED", align_msg)
                    put_ui_text(ip_ui, align_msg, (20, 50), size=20, color=UI_COLOR_HINT_SOFT)
                    return

            curr = (self.last_region['x'], self.last_region['y'])
            dist = np.hypot(curr[0] - self.last_pos[0], curr[1] - self.last_pos[1])
            self.last_pos = curr
            if dist >= FACE_MOVE_MAX_DIST:
                self.pre_capture_samples = []
                self.pre_capture_start = now
                put_ui_text(ip_ui, "請保持不動，避免影像模糊", (20, 50), size=20, color=UI_COLOR_HINT_SOFT)
                return

            roi, _, _ = crop_roi(frame, self.last_region, pad=0.20)
            if roi is not None and roi.size > 0:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                self.pre_capture_samples.append((blur_score, frame.copy(), dict(self.last_region)))
                if len(self.pre_capture_samples) > 10:
                    self.pre_capture_samples.pop(0)

            if elapsed < PRE_CAPTURE_HOLD_SEC:
                return

            if len(self.pre_capture_samples) < PRE_CAPTURE_MIN_SAMPLES:
                self._set_reason("CAPTURE_RETRY", "影像不足，請重新對準")
                self.pre_capture_samples = []
                self.pre_capture_start = 0.0
                self.state = "WAITING"
                return

            best_blur, best_frame, best_region = max(self.pre_capture_samples, key=lambda x: x[0])
            if best_blur < PRE_CAPTURE_BLUR_MIN:
                self._set_reason("BLUR_RETRY", "畫面偏模糊，請再試一次")
                self.pre_capture_samples = []
                self.pre_capture_start = 0.0
                self.state = "WAITING"
                return

            self.captured_ui_frame = best_frame.copy()
            threading.Thread(
                target=self._bg_analyze,
                args=(best_frame.copy(), best_region),
                daemon=True
            ).start()
            self.state = "PROCESSING"
            self.cooldown_until = now + 3

        # --- PROCESSING (等待背景結果，顯示動畫) ---
        elif self.state == "PROCESSING":
            self._set_reason("ANALYZING", "AI 年齡分析中")
            if self.captured_ui_frame is not None:
                ip_ui[:] = self.captured_ui_frame[:]
            bar_w = int((self.i % 40) / 40 * FRAME_WIDTH)
            cv2.rectangle(ip_ui, (0, FRAME_HEIGHT-8), (bar_w, FRAME_HEIGHT), (0, 255, 255), -1)
            put_ui_text(ip_ui, "AI 年齡分析中，請保持不動", (30, 410), size=24, color=(0, 255, 255))
            # 顯示目前處理中的小計
            if self.ages:
                cur = float(np.median(self.ages)) + AGE_OFFSET
                cur_range = age_to_display_range(cur)
                label = f"AI估測區間：約 {cur_range}" if cur_range else f"預估年齡：約 {int(cur)} 歲"
                put_ui_text(ip_ui, label, (20, 18), size=24, color=(0, 220, 255))

        # --- OCR_PENDING (證件拍照引導，無 OCR) ---
        elif self.state == "OCR_PENDING":
            self._set_reason("DOCUMENT_STEP", "請進行證件拍照流程")
            elapsed  = now - self.doc_mode_start
            rem_sec  = int(max(0, np.ceil(DOC_CAPTURE_HOLD_SEC - elapsed)))

            # 黃色大引導框
            cw, ch = DOC_GUIDE_WIDTH, DOC_GUIDE_HEIGHT
            cx1 = (FRAME_WIDTH - cw) // 2
            cy1 = max(40, (FRAME_HEIGHT - ch) // 2)
            cv2.rectangle(ip_ui, (cx1, cy1), (cx1+cw, cy1+ch), (0, 255, 255), 2)
            put_ui_text(ip_ui, "請將 身分證/健保卡 放入框內", (cx1 + 5, cy1 - 34), size=22, color=(0, 100, 255))
            put_ui_text(ip_ui, f"剩餘時間：{rem_sec} 秒", (cx1 + cw - 150, cy1 - 30), size=18, color=(0, 100, 255))

            # PIP 預覽 (雙鏡頭)
            if self.cap_ocr and self.ocr_frame_buffer is not None:
                pip = cv2.resize(self.ocr_frame_buffer, (240, 180))
                ip_ui[FRAME_HEIGHT-180:FRAME_HEIGHT, FRAME_WIDTH-240:FRAME_WIDTH] = pip
                cv2.rectangle(ip_ui,
                              (FRAME_WIDTH-240, FRAME_HEIGHT-180),
                              (FRAME_WIDTH, FRAME_HEIGHT), (0, 255, 0), 2)

            # 證件引導動作完成後，真實拍一張（不儲存），再進手動輸入
            if elapsed >= DOC_CAPTURE_HOLD_SEC:
                capture_frame = None
                if self.cap_ocr and self.ocr_frame_buffer is not None:
                    capture_frame = self.ocr_frame_buffer.copy()
                elif frame is not None:
                    capture_frame = frame.copy()

                if capture_frame is None or capture_frame.size == 0:
                    self.verify_msg = "拍照失敗，請重新對準證件"
                    self.doc_mode_start = now
                    return

                if capture_frame.shape[1] != FRAME_WIDTH or capture_frame.shape[0] != FRAME_HEIGHT:
                    capture_frame = cv2.resize(capture_frame, (FRAME_WIDTH, FRAME_HEIGHT))

                self.doc_capture_preview = capture_frame
                self.doc_capture_preview_until = now + DOC_CAPTURE_FLASH_SEC
                self.verify_msg = "證件拍照完成，請輸入生日後按 Enter 確認"
                self._set_reason("DOCUMENT_CAPTURED", "證件拍照完成")
                self.state = "DOC_CAPTURED"
                print("[Flow] Document snapshot captured (not stored).")
                return

            put_ui_text(
                ip_ui,
                "請先完成證件拍照，系統將在倒數結束後按下快門",
                (cx1 + 5, 410),
                size=22,
                color=(0, 255, 120)
            )

        # --- DOC_CAPTURED (證件拍照完成預覽) ---
        elif self.state == "DOC_CAPTURED":
            self._set_reason("DOCUMENT_CAPTURED", "證件拍照完成")
            if isinstance(self.doc_capture_preview, np.ndarray) and self.doc_capture_preview.size > 0:
                preview = self.doc_capture_preview
                if preview.shape[1] != FRAME_WIDTH or preview.shape[0] != FRAME_HEIGHT:
                    preview = cv2.resize(preview, (FRAME_WIDTH, FRAME_HEIGHT))
                ip_ui[:] = preview

            rem = max(0.0, self.doc_capture_preview_until - now)
            put_ui_text(ip_ui, "證件拍照完成", (20, 18), size=28, color=(0, 255, 180))
            put_ui_text(ip_ui, f"{rem:.1f} 秒後進入生日輸入", (20, 52), size=20, color=(0, 220, 255))

            if now >= self.doc_capture_preview_until:
                self.state = "MANUAL_INPUT"
                self.manual_input_buffer = ""
                self.manual_input_start = now
                self.doc_capture_preview = None
                self.doc_capture_preview_until = 0.0
                self._set_reason("MANUAL_INPUT", "請輸入生日完成驗證")
                print("[Flow] Document preview done, waiting manual birthday input.")
                return

        # --- MANUAL_INPUT (證件引導後，手動輸入生日) ---
        elif self.state == "MANUAL_INPUT":
            self._set_reason("MANUAL_INPUT", "請輸入生日完成驗證")
            input_elapsed = now - self.manual_input_start if self.manual_input_start > 0 else 0
            input_rem = int(max(0, np.ceil(MANUAL_INPUT_TIMEOUT_SEC - input_elapsed)))
            if input_elapsed >= MANUAL_INPUT_TIMEOUT_SEC:
                print("[Timeout] Manual input timeout, reset to waiting.")
                self._reset_state()
                return
            cv2.rectangle(ip_ui, (60, 85), (580, 375), (20, 20, 20), -1)
            cv2.rectangle(ip_ui, (60, 85), (580, 375), (0, 180, 255), 2)
            put_ui_text(ip_ui, "證件拍照完成，請輸入生日", (95, 118), size=30, color=(0, 180, 255))
            cv2.rectangle(ip_ui, (95, 178), (545, 233), (40, 40, 40), -1)
            cv2.rectangle(ip_ui, (95, 178), (545, 233), (0, 160, 255), 2)
            put_ui_text(
                ip_ui,
                format_manual_birth_display(self.manual_input_buffer),
                (110, 187),
                size=32,
                color=(255, 255, 255)
            )
            put_ui_text(ip_ui, f"輸入倒數：{input_rem} 秒", (95, 150), size=20, color=(0, 100, 255))
            if self.verify_msg and "格式錯誤" in self.verify_msg:
                put_ui_text(ip_ui, "格式錯誤，請輸入6碼生日（YYMMDD）", (95, 236), size=20, color=(60, 60, 255))
            put_ui_text(ip_ui, "輸入格式：YYMMDD（例：500101）", (95, 258), size=20, color=(255, 255, 255))

            key_y1, key_y2 = 286, 312
            cv2.rectangle(ip_ui, (95, key_y1), (148, key_y2), (255, 255, 255), 1)
            put_ui_text(ip_ui, "Enter", (99, 289), size=18, color=(255, 255, 255))
            put_ui_text(ip_ui, "確認", (156, 289), size=20, color=(255, 255, 255))

            cv2.rectangle(ip_ui, (228, key_y1), (326, key_y2), (255, 255, 255), 1)
            put_ui_text(ip_ui, "Backspace", (233, 289), size=18, color=(255, 255, 255))
            put_ui_text(ip_ui, "刪除", (332, 289), size=20, color=(255, 255, 255))

            cv2.rectangle(ip_ui, (398, key_y1), (426, key_y2), (255, 255, 255), 1)
            put_ui_text(ip_ui, "C", (406, 289), size=18, color=(255, 255, 255))
            put_ui_text(ip_ui, "清空", (432, 289), size=20, color=(255, 255, 255))

        # --- COOLDOWN (顯示結果) ---
        elif self.state == "COOLDOWN":
            self._set_reason("RESULT_READY", "結果已產生")
            d = self.final_result_data
            color = d.get("color", (200, 200, 200))
            cv2.rectangle(ip_ui, (60, 95), (580, 385), (20, 20, 20), -1)
            cv2.rectangle(ip_ui, (60, 95), (580, 385), color, 3)
            put_ui_text(ip_ui, f"結果：{ui_status_text(d.get('status'))}", (100, 135), size=30, color=color)
            result_source = str(d.get("source") or "")
            if result_source == "MANUAL" and d.get("age") is not None:
                put_ui_text(ip_ui, f"核驗年齡：{d['age']} 歲", (100, 190), size=24, color=(255, 255, 255))
            elif result_source == "AI":
                ai_range = d.get("ai_range")
                ai_age = d.get("ai_age")
                if ai_range:
                    put_ui_text(ip_ui, f"AI估測區間：{ai_range}", (100, 190), size=24, color=(255, 255, 255))
                elif ai_age is not None:
                    put_ui_text(ip_ui, f"AI估測：{ai_age} 歲", (100, 190), size=24, color=(255, 255, 255))
            msg = d.get("msg", "")
            if msg:
                put_ui_text(ip_ui, msg, (100, 245), size=18, color=(0, 200, 255))
            rem = int(max(0, np.ceil(self.cooldown_until - now)))
            put_ui_text(ip_ui, f"{rem} 秒後重置畫面", (100, 325), size=20, color=(0, 255, 160))
            put_ui_text(ip_ui, "請下一位", (100, 285), size=24, color=(0, 220, 255))

            if now <= self.cooldown_until:
                self.cooldown_clear_start = 0.0
                return

            # 冷卻結束後，需先偵測到無臉一小段時間，避免同一人立刻重複觸發
            any_face_present = (self.face_count > 0)
            if any_face_present:
                self.cooldown_clear_start = 0.0
                self._set_reason("WAIT_NEXT_CUSTOMER", "請下一位，請先離開鏡頭")
                put_ui_text(ip_ui, "請先離開鏡頭，再由下一位開始", (100, 350), size=18, color=(0, 220, 255))
                return

            if self.cooldown_clear_start <= 0:
                self.cooldown_clear_start = now
                return

            if (now - self.cooldown_clear_start) >= COOLDOWN_CLEAR_SEC:
                self._reset_state()

    # ===========================
    # 背景任務
    # ===========================
    def _bg_analyze(self, frame_crop, region):
        """背景執行緒：InsightFace 多尺度年齡分析"""
        try:
            scales  = [0.9, 1.0, 1.1]
            results = []
            for s in scales:
                face_img, _, _ = crop_roi(frame_crop, region, pad=max(0, s-0.7))
                if face_img is None or face_img.size == 0:
                    continue
                face_img = cv2.resize(face_img, (160, 160))
                age_val = self._estimate_age(face_img)
                if age_val is not None:
                    results.append(age_val)

            if results:
                sorted_results = sorted(results)
                trimmed = sorted_results[1:-1] if len(sorted_results) >= 4 else sorted_results
                smooth_age = float(np.mean(trimmed)) + AGE_OFFSET  # 套用校正偏移
                smooth_age = max(1, smooth_age)  # 不允許負年齡
                self.ages.append(smooth_age)
                ai_age_rounded = int(round(smooth_age))
                print(f"[AI] 專檢測年齡: {round(smooth_age, 1)} (校正後)")
                if ai_age_rounded <= AUTO_FAIL_MAX:
                    self.final_result_data = {
                        "status": "FAIL (Minor)",
                        "source": "AI",
                        "ai_age": ai_age_rounded,
                        "ai_range": age_to_display_range(smooth_age),
                        "color":  (0, 0, 255),
                        "msg":    "AI 判定明顯未成年"
                    }
                    self.state          = "COOLDOWN"
                    self.cooldown_until = time.time() + COOLDOWN_SEC
                    self._set_reason("RESULT_READY", "結果已產生")
                    self._save_record("AI", smooth_age, "FAIL (Minor)")
                elif ai_age_rounded > GREY_MAX:
                    self.final_result_data = {
                        "status": "PASS (Adult)",
                        "source": "AI",
                        "ai_age": ai_age_rounded,
                        "ai_range": age_to_display_range(smooth_age),
                        "color":  (0, 255, 0)
                    }
                    self.state          = "COOLDOWN"
                    self.cooldown_until = time.time() + COOLDOWN_SEC
                    self._set_reason("RESULT_READY", "結果已產生")
                    self._save_record("AI", smooth_age, "PASS (Adult)")
                else:
                    self.doc_mode_start = time.time()
                    self.state          = "OCR_PENDING"
                    self.manual_input_buffer = ""
                    self.manual_input_start = 0
                    self.verify_msg     = "請出示證件完成拍照步驟"
                    self._set_reason("DOCUMENT_STEP", "請進行證件拍照流程")
            else:
                self._set_reason("NO_FACE", "尚未偵測到可用人臉")
                self.verify_msg = ""
                self.state = "WAITING"
        except Exception as e:
            print(f"[AI] 分析錯誤: {e}")
            self._set_reason("NO_FACE", "分析失敗，請重新站位")
            self.verify_msg = ""
            self.state = "WAITING"

    # ===========================
    # 公開介面
    # ===========================
    def handle_key_event(self, key_code):
        """Handle a keyboard key code from local UI or web API."""
        try:
            k = int(key_code) & 0xFF
        except Exception:
            return False

        if k in (ord('v'), ord('V')):
            self._reset_state()
            return True

        if self.state != "MANUAL_INPUT":
            return False

        if ord('0') <= k <= ord('9'):
            if len(self.manual_input_buffer) < MANUAL_BIRTH_MAX_LEN:
                self.manual_input_buffer += chr(k)
            if "格式錯誤" in (self.verify_msg or ""):
                self.verify_msg = ""
            return True

        if k in (8, 127):
            self.manual_input_buffer = self.manual_input_buffer[:-1]
            if "格式錯誤" in (self.verify_msg or ""):
                self.verify_msg = ""
            return True

        if k in (ord('c'), ord('C')):
            self.manual_input_buffer = ""
            self.verify_msg = ""
            return True

        if k in (10, 13):
            birth = parse_manual_birth_input(self.manual_input_buffer)
            if not birth:
                self.verify_msg = "格式錯誤，請輸入6碼生日（YYMMDD）"
                return True

            est_age = float(np.median(self.ages)) if self.ages else None
            self._apply_birth_verification(birth, est_age, mode="MANUAL")
            self.manual_input_buffer = ""
            return True

        return False

    def get_status(self):
        """回傳 JSON 狀態（供 Flask API 使用）"""
        smooth_age = float(np.median(self.ages)) if self.ages else None
        stability  = float(np.std(self.ages))   if self.ages else None
        if stability is not None and stability > AGE_STABILITY_STD_MAX and self.state in {"WAITING", "ANALYZING", "PROCESSING"}:
            self._set_reason("UNSTABLE_AGE", "年齡波動較大，建議人工覆核")
        return json.dumps({
            "state":          self.state,
            "face_detected":  bool(self.last_region is not None),
            "face_count":     int(self.face_count),
            "frame_luma":     round(float(self.frame_luma), 2),
            "ai_age":         int(round(smooth_age)) if smooth_age else None,
            "ai_age_range":   age_to_display_range(smooth_age),
            "age_stability":  round(stability, 2) if stability else None,
            "ai_provider":    self.ai_provider,
            "stable_hits":    self.stable_frame_count,
            "stable_required": STABLE_FRAME_REQUIRED,
            "age_stability_threshold": AGE_STABILITY_STD_MAX,
            "low_light_luma_min": LOW_LIGHT_LUMA_MIN,
            "reason_code":    self.reason_code,
            "reason_message": self.reason_message,
            "ocr_msg":        self.verify_msg,
            "ocr_birth":      str(self.verify_birth) if self.verify_birth else None,
            "ocr_age":        self.verify_age,
            "ocr_pass":       self.verify_pass,
            "final_status":   self.final_result_data.get("status"),
            "final_age":      self.final_result_data.get("age"),
            "supabase_sync":  bool(SUPABASE_SYNC_ENABLED),
            "supabase_queue": int(SupabaseLogger.pending_count()) if SUPABASE_SYNC_ENABLED else 0,
        })

    def get_ui_frame_bytes(self):
        """回傳 JPEG 位元組（供 Flask MJPEG stream 使用）"""
        if self.ui_frame is not None:
            ok, jpeg = cv2.imencode(
                ".jpg",
                self.ui_frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(STREAM_JPEG_QUALITY)],
            )
            if not ok:
                return None
            return jpeg.tobytes()
        return None

    def release(self):
        self.running = False
        if hasattr(self, "_detect_thread") and self._detect_thread.is_alive():
            self._detect_thread.join(timeout=3)
        if self._thread.is_alive():
            self._thread.join(timeout=3)
        self.cap_face.release()
        if self.cap_ocr:
            self.cap_ocr.release()


# ===========================
# ===== 本機獨立執行 =====
# ===========================
if __name__ == "__main__":
    configure_console_encoding()
    print("=" * 50)
    print("  Age Kiosk Engine - 本機測試模式")
    print("  按 Q 退出  |  按 V 強制重置")
    print("=" * 50)

    engine = AgeEngine(cam_face=CAM_INDEX, cam_ocr=CAM_INDEX_OCR)

    if not engine.cap_face.isOpened():
        print(f"\n[ERROR] 無法開啟攝影機 (Index {CAM_INDEX})。")
        print("   請確認攝影機已連接，並修改 CAM_INDEX 參數。")
        engine.release()
        exit(1)

    print(f"\n[OK] 攝影機 (Index {CAM_INDEX}) 開啟成功！畫面即將顯示...")
    print("   按鍵說明: Q=離開 | V=重置")

    while True:
        frame = engine.ui_frame
        if frame is not None:
            try:
                cv2.imshow("Age Kiosk", frame)
            except cv2.error as e:
                print(f"[ERROR] OpenCV GUI not available: {e}")
                print("[HINT] Reinstall GUI build: .\\Age_kiosk\\.venv\\Scripts\\python.exe -m pip install --force-reinstall opencv-python==4.13.0.92")
                break
        else:
            time.sleep(0.05)
            continue

        k = cv2.waitKey(1) & 0xFF
        if k in (ord('q'), ord('Q')):
            print("使用者按下 Q，系統即將關閉...")
            break
        if engine.handle_key_event(k):
            continue

    engine.release()
    cv2.destroyAllWindows()
    print("系統已安全關閉。")
