"""Best-effort async writer for Supabase REST API."""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class SupabaseLogger:
    _lock = threading.Lock()
    _queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=2000)
    _worker: Optional[threading.Thread] = None

    _enabled: bool = False
    _url: str = ""
    _api_key: str = ""
    _table: str = "detection_logs"
    _timeout_sec: float = 2.0
    _last_error_ts: float = 0.0

    @classmethod
    def configure(
        cls,
        url: str = "",
        api_key: str = "",
        table: str = "detection_logs",
        timeout_sec: float = 2.0,
    ) -> None:
        with cls._lock:
            cls._url = str(url or "").strip().rstrip("/")
            cls._api_key = str(api_key or "").strip()
            cls._table = str(table or "detection_logs").strip() or "detection_logs"
            cls._timeout_sec = max(0.5, float(timeout_sec or 2.0))
            cls._enabled = bool(cls._url and cls._api_key)

            if cls._enabled and (cls._worker is None or not cls._worker.is_alive()):
                cls._worker = threading.Thread(target=cls._worker_loop, daemon=True)
                cls._worker.start()

    @classmethod
    def is_enabled(cls) -> bool:
        with cls._lock:
            return bool(cls._enabled)

    @classmethod
    def pending_count(cls) -> int:
        return int(cls._queue.qsize())

    @classmethod
    def enqueue(cls, record: Dict[str, Any]) -> bool:
        if not cls.is_enabled():
            return False
        try:
            cls._queue.put_nowait(dict(record))
            return True
        except queue.Full:
            cls._warn("queue full, dropping record")
            return False

    @classmethod
    def _warn(cls, msg: str) -> None:
        now = time.time()
        with cls._lock:
            if now - cls._last_error_ts < 10.0:
                return
            cls._last_error_ts = now
        print(f"[Supabase] {msg}")

    @classmethod
    def _worker_loop(cls) -> None:
        while True:
            item = cls._queue.get()
            try:
                if cls.is_enabled():
                    cls._send(item)
            except Exception as exc:
                cls._warn(f"sync failed: {exc}")
            finally:
                cls._queue.task_done()

    @classmethod
    def _send(cls, record: Dict[str, Any]) -> None:
        with cls._lock:
            url = cls._url
            api_key = cls._api_key
            table = cls._table
            timeout_sec = cls._timeout_sec

        endpoint = f"{url}/rest/v1/{table}"
        body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("apikey", api_key)
        request.add_header("Authorization", f"Bearer {api_key}")
        request.add_header("Prefer", "return=minimal")

        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                status = int(getattr(response, "status", 200))
                if status >= 300:
                    raise RuntimeError(f"unexpected status={status}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = ""
            raise RuntimeError(f"http {exc.code} {detail}".strip()) from exc

