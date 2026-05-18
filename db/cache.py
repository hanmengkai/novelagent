"""
db/cache.py — In-memory cache with file persistence

所有数据存储在内存中的 Python dict，
服务重启时从 JSON 文件恢复关键 novel 状态。
"""
import json
import os
import time
from datetime import datetime, date
from typing import Any, Optional
from loguru import logger

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "_memory_cache.json")

# ── 内存存储 ─────────────────────────────────────────────

_cache: dict[str, Any] = {}
_cache_ttl: dict[str, float] = {}  # key → expiry timestamp

# ── 持久化 ───────────────────────────────────────────────

PERSIST_KEYS = {  # 这些 key 需要持久化到文件
    "stop_requested",
    "current_task",
}

_NOVEL_PERSIST_PREFIXES = {  # novel-scoped keys 需要持久化的前缀
    "recent_summaries",
    "narrative_state",
}


def _persist_path() -> str:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    return CACHE_FILE


def _load_persisted():
    global _cache
    path = _persist_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _cache.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass


def _save_persisted():
    to_save = {}
    # 只保存需要持久化的 key
    for k, v in _cache.items():
        if k in PERSIST_KEYS:
            to_save[k] = v
        elif any(k.startswith(f"novel:{prefix}") for prefix in _NOVEL_PERSIST_PREFIXES):
            to_save[k] = v
        elif k.startswith("novel:"):
            # 持久化所有 novel-scoped key（包括 current_task 用于 Web 页面展示）
            suffix = k.split(":", 2)[-1] if ":" in k else ""
            if suffix:
                to_save[k] = v
    try:
        with open(_persist_path(), "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, default=str)
    except OSError as e:
        logger.warning(f"[Cache] 持久化失败: {e}")


# 启动时加载持久化数据
_load_persisted()


# ── TTL 检查 ──────────────────────────────────────────────

def _is_expired(key: str) -> bool:
    expiry = _cache_ttl.get(key)
    return expiry is not None and time.time() > expiry


def _clean_expired():
    now = time.time()
    expired = [k for k, t in _cache_ttl.items() if now > t]
    for k in expired:
        _cache.pop(k, None)
        _cache_ttl.pop(k, None)


# ── 工具函数 ────────────────────────────────────────────────

class _SafeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


def rset(key: str, value: Any, ttl: Optional[int] = None) -> None:
    data = json.dumps(value, ensure_ascii=False, cls=_SafeEncoder)
    _cache[key] = json.loads(data)  # store as Python object
    if ttl:
        _cache_ttl[key] = time.time() + ttl
    else:
        _cache_ttl.pop(key, None)
    _save_persisted()


def rget(key: str, default: Any = None) -> Any:
    _clean_expired()
    if _is_expired(key):
        _cache.pop(key, None)
        return default
    return _cache.get(key, default)


def rdel(key: str) -> None:
    _cache.pop(key, None)
    _cache_ttl.pop(key, None)
    _save_persisted()


def rkeys(pattern: str) -> list[str]:
    _clean_expired()
    # 简单前缀匹配
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return [k for k in _cache if k.startswith(prefix)]
    return [k for k in _cache if k == pattern]


def ping() -> bool:
    return True


# ── Novel-scoped helpers（与原版一致）──────────────────────

def novel_key(novel_id: str, suffix: str) -> str:
    return f"novel:{novel_id}:{suffix}"


def set_chapter_context(novel_id: str, chapter_no: int, ctx: dict) -> None:
    rset(novel_key(novel_id, f"ctx:{chapter_no}"), ctx, ttl=172800)


def get_chapter_context(novel_id: str, chapter_no: int) -> Optional[dict]:
    return rget(novel_key(novel_id, f"ctx:{chapter_no}"))


def set_recent_summaries(novel_id: str, summaries: list[dict]) -> None:
    rset(novel_key(novel_id, "recent_summaries"), summaries, ttl=172800)


def get_recent_summaries(novel_id: str) -> list[dict]:
    return rget(novel_key(novel_id, "recent_summaries"), default=[])


def set_narrative_state(novel_id: str, state: dict) -> None:
    rset(novel_key(novel_id, "narrative_state"), state, ttl=604800)


def get_narrative_state(novel_id: str) -> Optional[dict]:
    return rget(novel_key(novel_id, "narrative_state"))


# ── Stop-flag helpers ────────────────────────────────────

def request_stop(novel_id: str) -> None:
    rset(novel_key(novel_id, "stop_requested"), True, ttl=3600)


def clear_stop(novel_id: str) -> None:
    rdel(novel_key(novel_id, "stop_requested"))


def is_stop_requested(novel_id: str) -> bool:
    return bool(rget(novel_key(novel_id, "stop_requested"), default=False))


# ── Current-task tracking ────────────────────────────────

def set_current_task(novel_id: str, task: dict) -> None:
    rset(novel_key(novel_id, "current_task"), task, ttl=86400)


def get_current_task(novel_id: str) -> Optional[dict]:
    key = novel_key(novel_id, "current_task")
    val = rget(key)
    if val is None:
        # 跨进程同步：从持久化文件重新加载
        _load_persisted()
        val = rget(key)
    return val


def clear_current_task(novel_id: str) -> None:
    rdel(novel_key(novel_id, "current_task"))
