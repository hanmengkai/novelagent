"""
agents/fact_extractor.py — Fact Extractor Agent (事实提取器)

从已完成章节的正文中提取结构化事实，写入 NovelState 供后续节点持久化：
  - state.character_updates   → memory_commit 更新角色档案
  - state.new_foreshadows     → foreshadow_update 埋入新伏笔（有机/未计划）
  - state.resolved_foreshadows→ foreshadow_update 标记回收（补充 chapter_plan 未覆盖的）
  - state.extracted_facts     → memory_commit 写入章节事实

去重逻辑：
  - new_foreshadows 排除 chapter_plan.foreshadow_ops 中已有 plant 描述的条目
  - resolved_foreshadows 排除已在 chapter_plan.foreshadow_ops 中 resolve 的 ID
  - resolved_foreshadows 只保留数据库中实际存在的活跃/待回收 ID

在 graph 中位于 narrative_controller → [本节点] → memory_commit。
"""
from loguru import logger
from langgraph_engine.state import NovelState
from llm import simple_chat_json
from mcp import foreshadow_mcp
from config.prompts import FACT_EXTRACT_SYSTEM, FACT_EXTRACT_PROMPT
from agents.base import guard_error


def run(state: NovelState) -> NovelState:
    """LangGraph node: extract structured facts from the completed chapter."""
    if guard_error(state, "事实提取"):
        return state

    novel_id = state.novel_id
    chapter_id = state.chapter_id

    final_text = state.final_text or state.edited_text or state.draft_text
    if not final_text:
        logger.warning(f"⚠️  [事实提取] 第{chapter_id}章无正文，跳过")
        return state

    # ── Build prompt context ─────────────────────────────────────────
    char_profiles = _format_char_profiles(state.active_characters)

    active_fs = foreshadow_mcp.get_active(novel_id) + foreshadow_mcp.get_due(novel_id)
    active_fs_str = _format_active_foreshadows(active_fs)

    planned_ops = state.chapter_plan.foreshadow_ops if state.chapter_plan else []
    planned_ops_str = _format_planned_ops(planned_ops)

    # ── LLM call ─────────────────────────────────────────────────────
    try:
        result = simple_chat_json(
            system_prompt=FACT_EXTRACT_SYSTEM,
            user_prompt=FACT_EXTRACT_PROMPT.format(
                chapter_text=final_text[:8000],
                character_profiles=char_profiles,
                active_foreshadows=active_fs_str,
                planned_ops=planned_ops_str,
            ),
            fallback=_empty_result(),
        )
    except Exception as e:
        logger.error(f"❌ [事实提取] 第{chapter_id}章 LLM 调用失败: {e}")
        return state

    # ── Parse: character updates ──────────────────────────────────────
    raw_updates = result.get("character_updates", [])
    for upd in raw_updates:
        if not isinstance(upd, dict):
            continue
        # Hoist want/fear/contradiction from changes into extra for new characters
        if upd.get("is_new"):
            changes = upd.get("changes", {})
            extra = upd.setdefault("extra", {})
            for field in ("want", "fear", "contradiction"):
                val = changes.pop(field, "")
                if val:
                    extra[field] = val
    _MAX_ACTIVE_CHARS = 50
    state.character_updates = [
        upd for upd in raw_updates
        if isinstance(upd, dict) and (upd.get("char_id") or upd.get("name"))
        and not (_is_new_char(upd) and _is_descriptor_name(upd.get("name", "") or upd.get("char_id", "")))
    ]

    # Cap new character creation to prevent roster explosion
    new_char_requests = [u for u in state.character_updates if u.get("is_new")]
    if new_char_requests:
        from db import repo as _repo
        existing_count = len(_repo.get_all_characters(novel_id))
        if existing_count >= _MAX_ACTIVE_CHARS:
            state.character_updates = [u for u in state.character_updates if not u.get("is_new")]
            logger.warning(
                f"⚠️ [事实提取] 角色上限({_MAX_ACTIVE_CHARS})已达(当前{existing_count})，"
                f"丢弃{len(new_char_requests)}个新角色创建"
            )

    # ── Parse: new foreshadows (organic only, deduplicate vs plan) ────
    planned_plant_descs = {
        op.get("description", "").strip()
        for op in planned_ops
        if op.get("op") == "plant" and op.get("description")
    }
    state.new_foreshadows = [
        f for f in result.get("new_foreshadows", [])
        if isinstance(f, dict)
        and f.get("description", "").strip() not in planned_plant_descs
    ]

    # ── Parse: resolved foreshadows (validate ID + deduplicate vs plan)
    valid_ids = {f.get("fshadow_id") for f in active_fs if f.get("fshadow_id")}
    planned_resolve_ids = {
        op.get("id", "")
        for op in planned_ops
        if op.get("op") == "resolve" and op.get("id") and op.get("id") != "new"
    }
    state.resolved_foreshadows = [
        fid for fid in result.get("resolved_foreshadows", [])
        if isinstance(fid, str)
        and fid in valid_ids
        and fid not in planned_resolve_ids
    ]

    # ── Parse: world events ───────────────────────────────────────────
    state.extracted_facts = [
        ev for ev in result.get("world_events", [])
        if isinstance(ev, dict) and ev.get("text")
    ]

    all_empty = (
        not state.character_updates
        and not state.new_foreshadows
        and not state.resolved_foreshadows
        and not state.extracted_facts
    )
    if all_empty:
        logger.warning(
            f"⚠️  [事实提取] 第{chapter_id}章所有输出均为空 — "
            f"LLM可能返回了空结果或fallback，事实层本章无更新"
        )
    else:
        logger.info(
            f"🔎 [事实提取] 第{chapter_id}章: "
            f"角色更新={len(state.character_updates)}  "
            f"有机伏笔={len(state.new_foreshadows)}  "
            f"回收伏笔={len(state.resolved_foreshadows)}  "
            f"世界事件={len(state.extracted_facts)}"
        )
    return state


# ── helpers ─────────────────────────────────────────────────────────

def _format_char_profiles(chars: list[dict]) -> str:
    if not chars:
        return "无人物档案"
    lines = []
    for c in chars[:10]:
        lines.append(
            f"{c.get('name', c.get('char_id', '?'))}"
            f"[ID:{c.get('char_id', '?')}]: "
            f"境界={c.get('power_level', '?')}, "
            f"位置={c.get('location', '?')}, "
            f"情绪={c.get('emotion_state', '?')}, "
            f"状态={c.get('status', 'alive')}"
        )
    return "\n".join(lines)


def _format_active_foreshadows(foreshadows: list[dict]) -> str:
    if not foreshadows:
        return "无活跃伏笔"
    lines = []
    for f in foreshadows:
        lines.append(
            f"[{f.get('fshadow_id', '?')}] "
            f"{f.get('description', '')} "
            f"(状态:{f.get('state', '?')}, 埋入第{f.get('buried_chapter', '?')}章)"
        )
    return "\n".join(lines)


def _format_planned_ops(ops: list[dict]) -> str:
    if not ops:
        return "无计划伏笔操作"
    lines = []
    for op in ops:
        lines.append(
            f"• {op.get('op', '?')}: "
            f"[{op.get('id', 'new')}] {op.get('description', '')}"
        )
    return "\n".join(lines)


def _is_new_char(upd: dict) -> bool:
    return bool(upd.get("is_new"))


def _is_descriptor_name(name: str) -> bool:
    """Return True if the name looks like a descriptive label rather than a proper noun.

    Proper names in Chinese fiction are typically 2-4 characters with no embedded
    role/appearance/relationship words. Descriptors like '扎马尾的女人', '技术组长',
    '老头', '年轻警员' are scene-filler labels that should not become permanent records.
    """
    if not name:
        return True
    # Descriptor keywords: role titles, appearance words, relationship labels
    # Names longer than 5 chars are almost always descriptive labels
    if len(name) > 5:
        return True
    # Explicit 2-char descriptor words that look like nicknames but aren't
    _TWO_CHAR_DESCRIPTORS = {"老头", "老太", "老汉", "老妪", "老兵", "小兵", "小鬼", "小卒"}
    if name in _TWO_CHAR_DESCRIPTORS:
        return True
    # "小X" / "老X" (exactly 2 chars) = nickname prefix, NOT a descriptor
    # e.g. 小五, 小明, 老王 are valid names
    _NICKNAME_PREFIXES = ("小", "老")
    if len(name) == 2 and name[0] in _NICKNAME_PREFIXES:
        return False
    # Descriptor keywords: role titles, appearance words, relationship labels
    _DESCRIPTOR_TOKENS = (
        "的", "年轻", "技术", "组长", "副手", "警员", "士兵",
        "女人", "男人", "老头", "老太", "首领", "头目", "队长", "舰长",
        "船长", "司机", "驾驶员", "工人", "士官", "指挥官", "负责人",
        "甲", "乙", "丙", "丁", "路人", "随从", "警卫", "保镖",
    )
    return any(tok in name for tok in _DESCRIPTOR_TOKENS)


def _empty_result() -> dict:
    return {
        "character_updates": [],
        "new_foreshadows": [],
        "resolved_foreshadows": [],
        "world_events": [],
    }
