from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def read_json(path: Path, default: Any) -> Any:
    lock = path_lock(path)
    with lock:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)


def atomic_write_json(path: Path, payload: Any) -> None:
    lock = path_lock(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    lock = path_lock(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            file.flush()
