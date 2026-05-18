"""
compactor/compactor.py — Context Compactor (每N章执行，默认20)

Steps:
  1. Generate chapter arc summary (LLM)
  2. Compress in-memory cache (keep last 3 summaries hot)
  3. Preserve JSON file facts (no deletion)
  4. Keep MinIO original texts intact
"""
from loguru import logger
from langgraph_engine.state import NovelState
from llm import simple_chat_json
from config.prompts import COMPACTOR_SUMMARY_SYSTEM, COMPACTOR_SUMMARY_PROMPT
from db import repo, cache as rc


def run(state: NovelState) -> NovelState:
    """LangGraph node: Run compaction every N chapters."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id

    logger.info(f"[Compactor] Starting compaction at chapter {chapter_id}")

    from config import get_settings
    s = get_settings()
    start_ch = max(1, chapter_id - s.compaction_interval + 1)

    # 1. Get summaries for the window
    summaries = repo.get_recent_summaries(novel_id, limit=s.compaction_interval + 2)
    window_summaries = [s for s in summaries if start_ch <= s.get("chapter_no", 0) <= chapter_id]

    if not window_summaries:
        logger.warning("[Compactor] No summaries to compact")
        return state

    # 2. Character changes in window
    char_changes = _get_char_changes_summary(novel_id, start_ch, chapter_id)

    # 3. Foreshadow changes
    from mcp import foreshadow_mcp
    active_foreshadows = foreshadow_mcp.get_active(novel_id)
    foreshadow_changes = f"当前活跃伏笔: {len(active_foreshadows)}个"

    # 4. Generate arc summary via LLM
    summaries_text = "\n".join(
        f"第{s['chapter_no']}章: {s.get('summary_text', '')}"
        for s in window_summaries
    )

    arc_result = simple_chat_json(
        system_prompt=COMPACTOR_SUMMARY_SYSTEM,
        user_prompt=COMPACTOR_SUMMARY_PROMPT.format(
            n_chapters=len(window_summaries),
            chapter_summaries=summaries_text[:3000],
            character_changes=char_changes[:500],
            foreshadow_changes=foreshadow_changes,
        ),
        fallback={"arc_summary": f"第{start_ch}-{chapter_id}章内容摘要"},
    )

    # 5. Store arc summary in world_memory
    arc_key = f"arc_summary_{start_ch}_{chapter_id}"
    repo.set_world_memory(novel_id, arc_key, arc_result)
    logger.info(f"[Compactor] Arc summary saved: {arc_key}")

    # 5b. Persist character arc progress for narrative controller
    char_arc_progress = arc_result.get("character_arc_progress", {})
    if char_arc_progress:
        existing = repo.get_world_memory(novel_id, "character_arc_status") or {}
        for char_id, progress in char_arc_progress.items():
            existing[char_id] = (
                f"want:{progress.get('want_status','')} | "
                f"fear:{progress.get('fear_status','')} | "
                f"矛盾阶段:{progress.get('contradiction_phase','')} | "
                f"里程碑:{progress.get('arc_milestone','')}"
            )
        repo.set_world_memory(novel_id, "character_arc_status", existing)
        logger.info(f"[Compactor] Character arc status updated: {len(char_arc_progress)} chars")

    # 6. Compress in-memory cache (keep only last 3 summaries hot)
    last3 = summaries[-3:]
    rc.set_recent_summaries(novel_id, last3)

    logger.info(
        f"[Compactor] Done: compacted {len(window_summaries)} chapters "
        f"({start_ch}-{chapter_id})"
    )
    return state


def _get_char_changes_summary(novel_id: str, start_ch: int, end_ch: int) -> str:
    """Get character changes in the compaction window."""
    facts = repo.get_recent_facts(novel_id, start_ch, limit=30)
    power_changes = [f for f in facts
                     if f.get("fact_type") == "power_change"
                     and f.get("chapter_no", 0) <= end_ch]
    return "\n".join(f["fact_text"] for f in power_changes[:10])
