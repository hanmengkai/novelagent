"""
db/repository.py — All data operations (JSON file backend)

Replaces the original MySQL-backed version.
所有函数签名与原版保持一致，上层代码无需任何修改。
"""
import json
import uuid
from datetime import datetime
from typing import Any, Optional
from loguru import logger
from . import file_store as fs

# ── 伏笔状态转换（与原版一致）───────────────────────────
_VALID_TRANSITIONS = {
    "BURIED":   {"ACTIVE"},
    "ACTIVE":   {"DUE", "RESOLVED"},
    "DUE":      {"RESOLVED"},
    "RESOLVED": set(),
}


def _now() -> str:
    return datetime.now().isoformat()


# ═══════════════════════════════════════════════════════
#  Novel
# ═══════════════════════════════════════════════════════

def create_novel(title: str, description: str, world_type: str = "玄幻",
                 total_volumes: int = 10) -> str:
    novel_id = str(uuid.uuid4())
    novel = {
        "novel_id": novel_id,
        "title": title,
        "description": description,
        "world_type": world_type,
        "total_volumes": total_volumes,
        "status": "init",
        "created_at": _now(),
        "updated_at": _now(),
    }
    # 保存 novel 记录到全局索引
    novels = fs.load_global("novels_index", default=[])
    novels.append(novel)
    fs.save_global("novels_index", novels)

    # 创建小说数据目录（默认生成空文件）
    _init_novel_data(novel_id)
    return novel_id


def _init_novel_data(novel_id: str):
    """初始化一个新小说的所有数据文件"""
    fs.save_json(novel_id, "core", {
        "novel_id": novel_id,
        "world_memory": {},
        "volumes": [],
    })
    fs.save_json(novel_id, "characters", {})
    fs.save_json(novel_id, "foreshadows", {})
    fs.save_json(novel_id, "world_rules", {})
    fs.save_json(novel_id, "chapters", {})
    fs.save_json(novel_id, "chapter_facts", [])
    fs.save_json(novel_id, "chapter_summaries", {})
    fs.save_json(novel_id, "reader_metrics", {})
    fs.save_json(novel_id, "llm_log", [])


def get_novel(novel_id: str) -> Optional[dict]:
    novels = fs.load_global("novels_index", default=[])
    for n in novels:
        if n["novel_id"] == novel_id:
            return dict(n)
    return None


def _update_novel_in_index(novel_id: str, updates: dict):
    novels = fs.load_global("novels_index", default=[])
    for i, n in enumerate(novels):
        if n["novel_id"] == novel_id:
            novels[i].update(updates)
            novels[i]["updated_at"] = _now()
            break
    fs.save_global("novels_index", novels)


def check_completed(novel_id: str) -> bool:
    """检查小说是否已标记为「已完结」，是则抛出异常拒绝操作"""
    novel = get_novel(novel_id)
    if novel and novel.get("status") == "completed":
        raise PermissionError(
            f"小说「{novel.get('title', '')}」已标记为已完结(completed)，"
            "禁止进行删除、重置、修改、继续生成等操作。\n"
            f"如需操作，请先通过「python main.py uncomplete --novel-id {novel_id}」解除完结状态。"
        )


def update_novel_status(novel_id: str, status: str):
    _update_novel_in_index(novel_id, {"status": status})


def update_novel(novel_id: str, **fields):
    allowed = {"title", "description", "world_type", "total_volumes"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if updates:
        _update_novel_in_index(novel_id, updates)


def delete_novel(novel_id: str):
    # 从全局索引删除
    novels = fs.load_global("novels_index", default=[])
    novels = [n for n in novels if n["novel_id"] != novel_id]
    fs.save_global("novels_index", novels)
    # 删除所有数据文件
    fs.delete_novel_all(novel_id)


# ── Character last seen tracking (P3: 角色出场追踪) ──────────────


def update_character_last_seen(novel_id: str, char_ids: list[str],
                                chapter_no: int) -> None:
    """Record which characters appeared in this chapter.
    
    Stores a {char_id: last_chapter_no} dict in world memory.
    """
    existing = get_world_memory(novel_id, "character_last_seen", default={})
    for cid in char_ids:
        if cid:
            existing[cid] = chapter_no
    set_world_memory(novel_id, "character_last_seen", existing)


def get_character_last_seen(novel_id: str) -> dict:
    """Get {char_id: last_chapter_no} mapping.
    
    Returns empty dict if not yet tracked.
    """
    return get_world_memory(novel_id, "character_last_seen", default={})


def reset_novel_data(novel_id: str):
    """清除小说生成内容，同时清空向量库"""
    _init_novel_data(novel_id)
    _update_novel_in_index(novel_id, {"status": "init"})
    # 清空向量库中的旧数据
    try:
        from db import vector_store as _vs
        if _vs.delete_novel(novel_id):
            import logging
            logging.getLogger(__name__).debug(f"[repo] 已清空向量库: {novel_id}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"[repo] 向量库清空跳过: {e}")


def list_novels() -> list[dict]:
    novels = fs.load_global("novels_index", default=[])
    return [dict(n) for n in novels]


# ═══════════════════════════════════════════════════════
#  World Memory (KV)
# ═══════════════════════════════════════════════════════

def set_world_memory(novel_id: str, key: str, value: Any):
    core = fs.load_json(novel_id, "core")
    if "world_memory" not in core:
        core["world_memory"] = {}
    core["world_memory"][key] = value
    fs.save_json(novel_id, "core", core)


def get_world_memory(novel_id: str, key: str, default: Any = None) -> Any:
    core = fs.load_json(novel_id, "core")
    return core.get("world_memory", {}).get(key, default)


def get_all_world_memory(novel_id: str) -> dict:
    core = fs.load_json(novel_id, "core")
    return core.get("world_memory", {})


# ═══════════════════════════════════════════════════════
#  Volumes
# ═══════════════════════════════════════════════════════

def _get_volumes(novel_id: str) -> list[dict]:
    core = fs.load_json(novel_id, "core")
    return core.get("volumes", [])


def _save_volumes(novel_id: str, volumes: list[dict]):
    core = fs.load_json(novel_id, "core")
    core["volumes"] = volumes
    fs.save_json(novel_id, "core", core)


def upsert_volume(novel_id: str, volume_no: int, data: dict):
    volumes = _get_volumes(novel_id)
    for i, v in enumerate(volumes):
        if v.get("volume_no") == volume_no:
            volumes[i].update(data)
            break
    else:
        entry = {"volume_no": volume_no}
        entry.update(data)
        volumes.append(entry)
    _save_volumes(novel_id, volumes)


def get_volumes(novel_id: str) -> list[dict]:
    """获取所有卷的列表（公开接口）"""
    return _get_volumes(novel_id)


def get_volume(novel_id: str, volume_no: int) -> Optional[dict]:
    for v in _get_volumes(novel_id):
        if v.get("volume_no") == volume_no:
            return v
    return None


# ═══════════════════════════════════════════════════════
#  Characters
# ═══════════════════════════════════════════════════════

def _get_chars(novel_id: str) -> dict:
    return fs.load_json(novel_id, "characters", default={})


def upsert_character(novel_id: str, char_id: str, data: dict):
    chars = _get_chars(novel_id)
    chars[char_id] = {
        "char_id": char_id,
        "name": data.get("name", char_id),
        "status": data.get("status", "alive"),
        "personality": data.get("personality", []),
        "power_level": data.get("power_level", ""),
        "location": data.get("location", ""),
        "emotion_state": data.get("emotion_state", ""),
        "emotion_expression": data.get("emotion_expression", {}),
        "physical_state": data.get("physical_state", ""),
        "relationships": data.get("relationships", {}),
        "aliases": data.get("aliases", []),
        "backstory": data.get("backstory", ""),
        "chapters_active": data.get("chapters_active", []),
        "extra": data.get("extra", {}),
    }
    fs.save_json(novel_id, "characters", chars)


def get_character(novel_id: str, char_id: str) -> Optional[dict]:
    chars = _get_chars(novel_id)
    return chars.get(char_id)


def get_all_characters(novel_id: str) -> list[dict]:
    chars = _get_chars(novel_id)
    return list(chars.values())


# ═══════════════════════════════════════════════════════
#  Foreshadowing
# ═══════════════════════════════════════════════════════

def _get_foreshadows(novel_id: str) -> dict:
    return fs.load_json(novel_id, "foreshadows", default={})


def upsert_foreshadow(novel_id: str, fshadow_id: str, data: dict):
    fshes = _get_foreshadows(novel_id)
    existing = fshes.get(fshadow_id, {})
    fshes[fshadow_id] = {
        "fshadow_id": fshadow_id,
        "description": data.get("description", existing.get("description", "")),
        "buried_chapter": data.get("buried_chapter", existing.get("buried_chapter", 0)),
        "due_range_start": data.get("due_range_start", existing.get("due_range_start")),
        "due_range_end": data.get("due_range_end", existing.get("due_range_end")),
        "state": data.get("state", existing.get("state", "BURIED")),
        "importance": data.get("importance", existing.get("importance", "minor")),
        "resolve_chapter": data.get("resolve_chapter", existing.get("resolve_chapter")),
        "extra": data.get("extra", existing.get("extra", {})),
    }
    fs.save_json(novel_id, "foreshadows", fshes)


def get_foreshadow(novel_id: str, fshadow_id: str) -> Optional[dict]:
    fshes = _get_foreshadows(novel_id)
    return fshes.get(fshadow_id)


def list_foreshadows(novel_id: str, state: Optional[str] = None) -> list[dict]:
    fshes = _get_foreshadows(novel_id)
    result = list(fshes.values())
    if state:
        result = [f for f in result if f.get("state") == state]
    result.sort(key=lambda x: x.get("buried_chapter", 0))
    return result


def transition_foreshadow_state(novel_id: str, fshadow_id: str, new_state: str,
                                 resolve_chapter: Optional[int] = None):
    fshes = _get_foreshadows(novel_id)
    fsh = fshes.get(fshadow_id)
    if fsh is None:
        return
    current = fsh.get("state", "BURIED")
    allowed = _VALID_TRANSITIONS.get(current, set())
    if new_state not in allowed:
        logger.debug(
            f"[repo] blocked invalid foreshadow transition: "
            f"{fshadow_id} {current}→{new_state}"
        )
        return
    fsh["state"] = new_state
    if resolve_chapter is not None:
        fsh["resolve_chapter"] = resolve_chapter
    fs.save_json(novel_id, "foreshadows", fshes)


# ═══════════════════════════════════════════════════════
#  World Rules
# ═══════════════════════════════════════════════════════

def _get_rules(novel_id: str) -> dict:
    return fs.load_json(novel_id, "world_rules", default={})


def set_world_rule(novel_id: str, rule_key: str, rule_value: Any, immutable: bool = True):
    rules = _get_rules(novel_id)
    rules[rule_key] = {
        "rule_key": rule_key,
        "rule_value": rule_value,
        "immutable": immutable,
    }
    fs.save_json(novel_id, "world_rules", rules)


def get_all_world_rules(novel_id: str) -> dict:
    rules = _get_rules(novel_id)
    result = {}
    for key, entry in rules.items():
        result[key] = entry.get("rule_value", {})
    return result


# ═══════════════════════════════════════════════════════
#  Chapters
# ═══════════════════════════════════════════════════════

def _get_chapters(novel_id: str) -> dict:
    return fs.load_json(novel_id, "chapters", default={})


def upsert_chapter(novel_id: str, chapter_no: int, data: dict):
    chapters = _get_chapters(novel_id)
    chapters[str(chapter_no)] = {
        "chapter_no": chapter_no,
        "volume_no": data.get("volume_no", 1),
        "title": data.get("title", ""),
        "brief": data.get("brief", ""),
        "content": data.get("content", ""),
        "word_count": data.get("word_count", 0),
        "quality_score": data.get("quality_score"),
        "status": data.get("status", "pending"),
        "issues": data.get("issues", []),
        "repair_log": data.get("repair_log", []),
        "scene_type": data.get("scene_type"),
        "hook_level": data.get("hook_level"),
    }
    fs.save_json(novel_id, "chapters", chapters)


def get_chapter_stats(novel_id: str) -> dict:
    """返回章统计：总量、完成数、失败数、总字数、活跃伏笔"""
    chapters = _get_chapters(novel_id)
    total = len(chapters)
    done = sum(1 for c in chapters.values() if c.get("status") == "done")
    failed = sum(1 for c in chapters.values() if c.get("status") == "failed")
    words = sum(c.get("word_count", 0) for c in chapters.values())
    chs = [c for c in chapters.values() if c.get("status") == "done"]
    chs.sort(key=lambda x: x.get("chapter_no", 0))
    last_ch = chs[-1] if chs else None

    # 活跃伏笔
    fshes = _get_foreshadows(novel_id)
    pending_fs = sum(1 for f in fshes.values() if f.get("state") != "RESOLVED")

    return {
        "total": total,
        "done": done,
        "failed": failed,
        "words": words,
        "pending_foreshadows": pending_fs,
        "last_chapter": last_ch,
    }


def list_json_chapters(novel_id: str, volume: Optional[int] = None,
                       limit: int = 500) -> list[dict]:
    """从 JSON 文件中获取章节列表，替代 MySQL 查询"""
    chapters = _get_chapters(novel_id)
    result = []
    for c in chapters.values():
        if volume is not None and c.get("volume_no") != volume:
            continue
        result.append({
            "chapter_no": c.get("chapter_no", 0),
            "volume_no": c.get("volume_no", 1),
            "title": c.get("title", ""),
            "word_count": c.get("word_count", 0),
            "status": c.get("status", "pending"),
            "created_at": c.get("created_at", ""),
        })
    result.sort(key=lambda x: x["chapter_no"])
    return result[:limit]


def get_chapter(novel_id: str, chapter_no: int) -> Optional[dict]:
    chapters = _get_chapters(novel_id)
    return chapters.get(str(chapter_no))


def get_chapters_in_volume(novel_id: str, volume_no: int) -> list[dict]:
    chapters = _get_chapters(novel_id)
    result = [c for c in chapters.values() if c.get("volume_no") == volume_no]
    result.sort(key=lambda x: x.get("chapter_no", 0))
    return result


def get_recent_chapters(novel_id: str, up_to_chapter: int, limit: int = 5) -> list[dict]:
    """Return the last `limit` chapters up to and including `up_to_chapter`."""
    chapters = _get_chapters(novel_id)
    result = [
        c for c in chapters.values()
        if isinstance(c.get("chapter_no"), int) and c["chapter_no"] <= up_to_chapter
    ]
    result.sort(key=lambda x: x.get("chapter_no", 0), reverse=True)
    return result[:limit]


# ═══════════════════════════════════════════════════════
#  Chapter Facts
# ═══════════════════════════════════════════════════════

def _get_facts(novel_id: str) -> list[dict]:
    return fs.load_json(novel_id, "chapter_facts", default=[])


def append_chapter_fact(novel_id: str, chapter_no: int, fact_type: str,
                        fact_text: str, keywords: str = ""):
    facts = _get_facts(novel_id)
    facts.append({
        "novel_id": novel_id,
        "chapter_no": chapter_no,
        "fact_type": fact_type,
        "fact_text": fact_text,
        "keywords": keywords,
        "created_at": _now(),
    })
    fs.save_json(novel_id, "chapter_facts", facts)


def get_facts_by_keywords(novel_id: str, keywords: list[str], limit: int = 20) -> list[dict]:
    if not keywords:
        return []
    facts = _get_facts(novel_id)
    result = []
    for f in reversed(facts):
        kw = (f.get("keywords") or "").lower()
        if any(k.lower() in kw for k in keywords):
            result.append(f)
            if len(result) >= limit:
                break
    return result


def get_recent_facts(novel_id: str, since_chapter: int, limit: int = 50) -> list[dict]:
    facts = _get_facts(novel_id)
    filtered = [f for f in facts if f.get("chapter_no", 0) >= since_chapter]
    filtered.sort(key=lambda x: x.get("chapter_no", 0), reverse=True)
    return filtered[:limit]


# ═══════════════════════════════════════════════════════
#  Chapter Summaries
# ═══════════════════════════════════════════════════════

def _get_summaries(novel_id: str) -> dict:
    return fs.load_json(novel_id, "chapter_summaries", default={})


def upsert_chapter_summary(novel_id: str, chapter_no: int, data: dict):
    summaries = _get_summaries(novel_id)
    summaries[str(chapter_no)] = {
        "chapter_no": chapter_no,
        "volume_no": data.get("volume_no", 1),
        "summary_text": data.get("summary_text", ""),
        "key_characters": data.get("key_characters", []),
        "arc_phase": data.get("arc_phase", ""),
        "emotion_peak": data.get("emotion_peak", 0.5),
        "conflict_count": data.get("conflict_count", 0),
    }
    fs.save_json(novel_id, "chapter_summaries", summaries)


def get_recent_summaries(novel_id: str, limit: int = 5) -> list[dict]:
    summaries = _get_summaries(novel_id)
    sorted_items = sorted(summaries.values(), key=lambda x: x.get("chapter_no", 0))
    return sorted_items[-limit:]


# ═══════════════════════════════════════════════════════
#  Reader Metrics
# ═══════════════════════════════════════════════════════

def _get_metrics(novel_id: str) -> dict:
    return fs.load_json(novel_id, "reader_metrics", default={})


def upsert_reader_metrics(novel_id: str, chapter_no: int, engagement: float,
                          tension: float, drop_risk: float, raw: dict = None):
    metrics = _get_metrics(novel_id)
    metrics[str(chapter_no)] = {
        "chapter_no": chapter_no,
        "engagement": engagement,
        "tension": tension,
        "drop_risk": drop_risk,
        "raw_scores": raw or {},
    }
    fs.save_json(novel_id, "reader_metrics", metrics)


def get_reader_metrics(novel_id: str, chapter_no: int) -> Optional[dict]:
    metrics = _get_metrics(novel_id)
    return metrics.get(str(chapter_no))


# ═══════════════════════════════════════════════════════
#  LLM Log
# ═══════════════════════════════════════════════════════

def _get_logs(novel_id: str) -> list[dict]:
    return fs.load_json(novel_id, "llm_log", default=[])


def log_llm_task(novel_id: str, agent_name: str, chapter_no: Optional[int] = None,
                 volume_no: Optional[int] = None, prompt_tokens: int = 0,
                 completion_tokens: int = 0, status: str = "ok", error_msg: str = ""):
    logs = _get_logs(novel_id)
    logs.append({
        "novel_id": novel_id,
        "agent_name": agent_name,
        "chapter_no": chapter_no,
        "volume_no": volume_no,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "status": status,
        "error_msg": error_msg,
        "created_at": _now(),
    })
    # 只保留最近 5000 条日志，避免文件过大
    if len(logs) > 5000:
        logs = logs[-5000:]
    fs.save_json(novel_id, "llm_log", logs)
