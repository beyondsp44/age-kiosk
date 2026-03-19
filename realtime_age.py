"""
Compatibility shim for legacy entrypoint.

Real implementation now lives in `app/services/age_engine.py`.
"""
import os
import sys
import time


def _bootstrap_venv_site_packages():
    """Prepend project venv site-packages so top-right Run and F5 share same deps."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_site = os.path.join(script_dir, ".venv", "Lib", "site-packages")
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)
    return venv_site


_bootstrap_venv_site_packages()

import cv2
from app.services import age_engine as _age_engine


# Backward-compatible direct imports:
AgeEngine = _age_engine.AgeEngine
CAM_INDEX = _age_engine.CAM_INDEX
CAM_INDEX_OCR = _age_engine.CAM_INDEX_OCR


def __getattr__(name):
    return getattr(_age_engine, name)


def __dir__():
    return sorted(set(globals().keys()) | set(dir(_age_engine)))


if __name__ == "__main__":
    _age_engine.configure_console_encoding()
    print("=" * 50)
    print("  Age Kiosk Engine - 本機測試模式")
    print("  按 Q 離開 | 按 V 重置流程")
    print("=" * 50)

    engine = _age_engine.AgeEngine(cam_face=_age_engine.CAM_INDEX, cam_ocr=_age_engine.CAM_INDEX_OCR)

    if not engine.cap_face.isOpened():
        print(f"\n[ERROR] 無法開啟攝影機 (Index {_age_engine.CAM_INDEX})")
        print("   請確認攝影機已連接，並修改 CAM_INDEX 參數。")
        engine.release()
        raise SystemExit(1)

    print(f"\n[OK] 攝影機(Index {_age_engine.CAM_INDEX}) 已啟動，開始即時偵測...")
    print("   鍵盤操作: Q=離開 | V=重置")

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
        if k in (ord("q"), ord("Q")):
            print("偵測已由鍵盤 Q 結束，正在關閉...")
            break
        if engine.handle_key_event(k):
            continue

    engine.release()
    cv2.destroyAllWindows()
    print("系統已正常關閉")
