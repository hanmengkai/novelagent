"""
Per-novel log ring buffer.
Stores the latest N log lines per novel in memory.
Pipeline stages call log_step() to push detailed progress.
The web API reads get_logs() for frontend display.
"""

import json
import logging
import os
from collections import deque
from datetime import datetime
from typing import Optional

_MAX_LINES = 500
_JSONL_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# novel_id -> deque of {"time": str, "msg": str, "level": str}
_logs: dict[str, deque[dict]] = {}

_std_logger = logging.getLogger("novel_log")


def _ensure(novel_id: str) -> deque:
    if novel_id not in _logs:
        _logs[novel_id] = deque(maxlen=_MAX_LINES)
    return _logs[novel_id]


def get_jsonl_path(novel_id: str) -> str:
    """Return the absolute path to this novel's JSON Lines log file."""
    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", novel_id)
    return os.path.join(base_dir, "pipeline.jsonl")


def _append_jsonl(novel_id: str, level: str, message: str, event: dict) -> None:
    """Append one JSON line to data/<novel_id>/pipeline.jsonl."""
    path = get_jsonl_path(novel_id)
    try:
        dir_path = os.path.dirname(path)
        os.makedirs(dir_path, exist_ok=True)

        # Cap at 50 MB: rotate to .bak if exceeded
        if os.path.exists(path) and os.path.getsize(path) > _JSONL_MAX_BYTES:
            bak_path = path + ".bak"
            os.replace(path, bak_path)

        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        record = {"ts": ts, "novel_id": novel_id, "level": level, "msg": message, "event": event}
        line = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except IOError as exc:
        _std_logger.warning(f"[novel_log] Failed to write JSONL for {novel_id}: {exc}")


def log_step(novel_id: str, message: str, level: str = "info", event: dict | None = None) -> None:
    """Add a structured log line for a specific novel.

    Also writes to the standard file logger so the full history is preserved,
    and appends a JSON line to data/<novel_id>/pipeline.jsonl.
    """
    buf = _ensure(novel_id)
    now = datetime.now().strftime("%H:%M:%S")
    buf.append({"time": now, "msg": message, "level": level})
    # Also forward to the standard logger for file persistence
    getattr(_std_logger, level, _std_logger.info)(f"[{novel_id[:8]}] {message}")
    # Append to JSONL file
    _append_jsonl(novel_id, level, message, event if event is not None else {})


def log_info(novel_id: str, message: str, event: dict | None = None) -> None:
    log_step(novel_id, message, "info", event=event)


def log_warn(novel_id: str, message: str, event: dict | None = None) -> None:
    log_step(novel_id, message, "warn", event=event)


def log_error(novel_id: str, message: str, event: dict | None = None) -> None:
    log_step(novel_id, message, "error", event=event)


def get_logs(novel_id: str, last_n: int = 200, level_filter: Optional[str] = None) -> list[dict]:
    """Get recent log lines for a novel.

    Args:
        novel_id: Novel UUID.
        last_n: Max lines to return.
        level_filter: "error", "warn", or None for all.

    Returns:
        List of dicts with keys: time, msg, level.
    """
    buf = _logs.get(novel_id)
    if not buf:
        return []
    lines = list(buf)
    if level_filter:
        lines = [l for l in lines if l["level"] == level_filter]
    return lines[-last_n:]


def get_all_novel_ids() -> list[str]:
    """Get all novel IDs that have logs."""
    return list(_logs.keys())


def clear(novel_id: str) -> None:
    """Clear logs for a specific novel."""
    _logs.pop(novel_id, None)
