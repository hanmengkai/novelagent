"""
db/file_store.py — JSON 文件存储引擎

替代 MySQL，将所有数据存储在 JSON 文件中。
特点:
  - 原子写入（先写临时文件再 rename）
  - 内存缓存 + 惰性加载
  - 自动创建目录结构
"""
import json
import os
import shutil
import tempfile
import threading
from typing import Any, Optional
from loguru import logger

DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ── 路径工具 ─────────────────────────────────────────────

_SAFE_ID_RE = __import__("re").compile(r'^[a-zA-Z0-9_\-]{1,64}$')


def _novel_dir(novel_id: str) -> str:
    if not _SAFE_ID_RE.match(novel_id):
        raise ValueError(f"Invalid novel_id: {novel_id!r}")
    path = os.path.join(DATA_ROOT, novel_id)
    # Extra guard: resolved path must stay inside DATA_ROOT
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(DATA_ROOT)):
        raise ValueError(f"Path traversal detected for novel_id: {novel_id!r}")
    os.makedirs(path, exist_ok=True)
    return path


# ── 原子写入 ─────────────────────────────────────────────

def _atomic_write(path: str, content: str) -> None:
    """原子写入：先写临时文件，再 rename 覆盖原文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── 单层 JSON 文件访问（KV 结构）────────────────────────

_FILE_CACHE: dict[str, Any] = {}
_FILE_LOCKS: dict[str, threading.Lock] = {}
_LOCK = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _LOCK:
        if key not in _FILE_LOCKS:
            _FILE_LOCKS[key] = threading.Lock()
        return _FILE_LOCKS[key]


def _file_path(novel_id: str, section: str) -> str:
    return os.path.join(_novel_dir(novel_id), f"{section}.json")


def load_json(novel_id: str, section: str, default: Any = None) -> Any:
    """
    加载指定 section 的 JSON 文件。
    返回 Python 对象（dict, list, 或 default）。
    """
    cache_key = f"{novel_id}:{section}"
    if cache_key in _FILE_CACHE:
        return _FILE_CACHE[cache_key]

    path = _file_path(novel_id, section)
    if not os.path.exists(path):
        result = default if default is not None else {}
        _FILE_CACHE[cache_key] = result
        return result

    try:
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        _FILE_CACHE[cache_key] = result
        return result
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[FileStore] 读取失败 {path}: {e}，返回默认值")
        result = default if default is not None else {}
        _FILE_CACHE[cache_key] = result
        return result


def save_json(novel_id: str, section: str, data: Any) -> None:
    """
    保存指定 section 的数据到 JSON 文件（原子写入）。
    同时更新内存缓存。
    """
    cache_key = f"{novel_id}:{section}"
    path = _file_path(novel_id, section)

    # 更新缓存
    _FILE_CACHE[cache_key] = data

    # 原子写入磁盘
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    try:
        _atomic_write(path, content)
    except OSError as e:
        logger.error(f"[FileStore] 写入失败 {path}: {e}")


def invalidate_cache(novel_id: Optional[str] = None, section: Optional[str] = None) -> None:
    """清除缓存（用于测试或重建）"""
    global _FILE_CACHE
    if novel_id is None:
        _FILE_CACHE.clear()
    elif section is None:
        _FILE_CACHE = {k: v for k, v in _FILE_CACHE.items() if not k.startswith(f"{novel_id}:")}
    else:
        _FILE_CACHE.pop(f"{novel_id}:{section}", None)


# ── 全局文件（不限小说）─────────────────────────────────

def load_global(section: str, default: Any = None) -> Any:
    cache_key = f"__global__:{section}"
    if cache_key in _FILE_CACHE:
        return _FILE_CACHE[cache_key]

    path = os.path.join(DATA_ROOT, f"{section}.json")
    if not os.path.exists(path):
        result = default if default is not None else {}
        _FILE_CACHE[cache_key] = result
        return result

    try:
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        _FILE_CACHE[cache_key] = result
        return result
    except (json.JSONDecodeError, OSError):
        result = default if default is not None else {}
        _FILE_CACHE[cache_key] = result
        return result


def save_global(section: str, data: Any) -> None:
    cache_key = f"__global__:{section}"
    path = os.path.join(DATA_ROOT, f"{section}.json")
    _FILE_CACHE[cache_key] = data
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    try:
        _atomic_write(path, content)
    except OSError as e:
        logger.error(f"[FileStore] 全局写入失败 {path}: {e}")


# ── 数据迁移工具 ─────────────────────────────────────────

def import_from_dict(novel_id: str, section: str, data: Any) -> None:
    """从已有 dict 导入数据到 JSON 文件"""
    save_json(novel_id, section, data)


def delete_novel_all(novel_id: str) -> None:
    """删除一个小说的所有数据文件"""
    path = _novel_dir(novel_id)
    try:
        shutil.rmtree(path)
        logger.info(f"[FileStore] 删除小说数据: {novel_id}")
    except OSError as e:
        logger.error(f"[FileStore] 删除失败 {path}: {e}")

    # 清除缓存
    invalidate_cache(novel_id)


def list_novel_ids() -> list[str]:
    """列出 data 目录下的所有小说 ID"""
    if not os.path.exists(DATA_ROOT):
        return []
    return sorted([
        d for d in os.listdir(DATA_ROOT)
        if os.path.isdir(os.path.join(DATA_ROOT, d)) and d != "__pycache__"
    ])
