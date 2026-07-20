from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .persistence import append_jsonl


ALLOWED_EVENT_FIELDS = {
    "job_id",
    "file_count",
    "total_bytes",
    "status",
    "queue_position",
    "queued_count",
    "reason_code",
    "data_mode",
    "worker_alive",
    "warning_code",
}


class StructuredEventLogger:
    def __init__(self, log_file: Path) -> None:
        self.log_file = log_file
        self._lock = threading.RLock()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, *, level: str = "info", **fields: Any) -> None:
        safe_fields = {key: value for key, value in fields.items() if key in ALLOWED_EVENT_FIELDS}
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": level,
            "event": event,
            **safe_fields,
        }
        with self._lock:
            append_jsonl(self.log_file, payload)
