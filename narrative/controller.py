"""
narrative/controller.py — Narrative Control Layer

Responsibilities:
  - Track arc phases (setup → buildup → climax → cooldown)
  - Control emotion curves
  - Manage conflict density
  - Schedule climax/cooldown distribution
  - Output NarrativeDirective for next chapter
"""
from loguru import logger
from langgraph_engine.state import NovelState, NarrativeDirective, ArcPhase
from llm import simple_chat_json
from mcp import reader_mcp, foreshadow_mcp
from config.prompts import NARRATIVE_SYSTEM, NARRATIVE_PROMPT
from db import cache as rc
from db import repo


def run(state: NovelState) -> NovelState:
    """LangGraph node: Narrative Controller sets direction for next chapter."""
    if state.error:
        logger.warning(f"⚠️  [叙事控制] 跳过（上游错误: {state.error}）")
        return state

    novel_id = state.novel_id
    chapter_id = state.chapter_id
    next_chapter_id = chapter_id + 1

    # Current arc state
    current_arc = state.memory_snapshot.get("arc_phase", ArcPhase.BUILDUP)
    current_arc_str = current_arc.value if isinstance(current_arc, ArcPhase) else str(current_arc)

    # Conflict intensity from checker/issues
    high_issues = sum(1 for i in state.issues if i.severity.value == "high")
    conflict_intensity = max(0.3, 0.8 - (high_issues * 0.1))

    # Reader trend
    trend = reader_mcp.get_trend(novel_id, last_n=5)
    reader_trend_str = f"engagement={trend['avg_engagement']:.2f}, trend={trend['trend']}"

    # Volume progress
    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{state.volume_no}") or {}
    total_vol_chapters = len(volume_plan.get("chapter_outlines", [])) or 50
    vol_progress = f"第{chapter_id}章，本卷约{total_vol_chapters}章，进度{chapter_id/total_vol_chapters:.0%}"

    # Overdue foreshadows
    overdue = foreshadow_mcp.get_overdue(novel_id, chapter_id)
    overdue_count = len(overdue)
    pending_count = len(foreshadow_mcp.get_active(novel_id)) + len(foreshadow_mcp.get_due(novel_id))

    # Compute emotion curve from chapter content
    emotion_curve = _estimate_emotion_curve(state)

    try:
        result = simple_chat_json(
            system_prompt=NARRATIVE_SYSTEM,
            user_prompt=NARRATIVE_PROMPT.format(
                current_chapter_id=chapter_id,
                next_chapter_id=next_chapter_id,
                arc_phase=current_arc_str,
                conflict_intensity=conflict_intensity,
                emotion_curve=emotion_curve,
                reader_trend=reader_trend_str,
                volume_progress=vol_progress,
                pending_foreshadows_count=pending_count,
                overdue_foreshadows=f"{overdue_count}个逾期" if overdue_count else "无",
                recent_scene_types=_get_recent_scene_types(novel_id, chapter_id),
                recent_hook_levels=_get_recent_hook_levels(novel_id, chapter_id),
                character_arc_status=_get_character_arc_status(novel_id),
            ),
            fallback=_default_narrative(next_chapter_id, current_arc_str),
        )
    except Exception as e:
        logger.warning(f"[NarrativeController] LLM failed, using default: {e}")
        result = _default_narrative(next_chapter_id, current_arc_str)

    # Build NarrativeDirective
    try:
        next_arc = ArcPhase(result.get("arc_phase", current_arc_str))
    except ValueError:
        next_arc = ArcPhase.BUILDUP

    directive = NarrativeDirective(
        arc_phase=next_arc,
        emotion_curve=result.get("emotion_curve", "平稳"),
        conflict_intensity=float(result.get("conflict_intensity", 0.5)),
        next_chapter_goal=result.get("next_chapter_goal", f"推进第{next_chapter_id}章"),
        style_notes=result.get("style_variance"),
    )
    state.narrative_directive = directive

    # Persist reader metrics for this chapter
    final_text = state.final_text or state.edited_text or state.draft_text
    if final_text:
        metrics = reader_mcp.score_chapter(novel_id, chapter_id, final_text)
        state.reader_metrics = metrics

    # Persist last chapter ending (last 200 chars) for next chapter
    final = state.final_text or state.edited_text or state.draft_text
    if final:
        last_ending = final[-200:].strip()
        repo.set_world_memory(novel_id, "last_chapter_ending", last_ending)
        state.final_text = final  # ensure final_text is set

    # Cache narrative state in Redis
    rc.set_narrative_state(novel_id, {
        "arc_phase": next_arc.value,
        "conflict_intensity": directive.conflict_intensity,
        "chapter_id": chapter_id,
    })

    logger.info(
        f"[NarrativeController] ch{chapter_id} done → "
        f"next arc={next_arc.value}, intensity={directive.conflict_intensity:.2f}"
    )
    return state


def _estimate_emotion_curve(state: NovelState) -> str:
    """Heuristic emotion curve based on chapter plan and issues."""
    if not state.chapter_plan:
        return "平稳"
    arc = state.memory_snapshot.get("chapter_arc", "平静→紧张→缓和")
    return str(arc)


def _get_recent_scene_types(novel_id: str, chapter_id: int) -> str:
    """Get scene type distribution from recent chapters."""
    try:
        from db.json_session import get_db
        from sqlalchemy import text
        start = max(1, chapter_id - 5)
        with get_db() as db:
            rows = db.execute(text(
                "SELECT chapter_no, scene_type FROM chapters "
                "WHERE novel_id=:nid AND chapter_no BETWEEN :start AND :end "
                "ORDER BY chapter_no"
            ), {"nid": novel_id, "start": start, "end": chapter_id}).mappings().all()
        if not rows:
            return "（无记录）"
        return " | ".join(f"第{r['chapter_no']}章:{r.get('scene_type','?')}" for r in rows)
    except Exception:
        return "（无法加载）"


def _get_recent_hook_levels(novel_id: str, chapter_id: int) -> str:
    """Get ending hook levels from recent chapters."""
    try:
        from db.json_session import get_db
        from sqlalchemy import text
        start = max(1, chapter_id - 5)
        with get_db() as db:
            rows = db.execute(text(
                "SELECT chapter_no, hook_level FROM chapters "
                "WHERE novel_id=:nid AND chapter_no BETWEEN :start AND :end "
                "ORDER BY chapter_no"
            ), {"nid": novel_id, "start": start, "end": chapter_id}).mappings().all()
        if not rows:
            return "（无记录）"
        return " | ".join(f"第{r['chapter_no']}章:强度{r.get('hook_level','?')}" for r in rows)
    except Exception:
        return "（无法加载）"


def _get_character_arc_status(novel_id: str) -> str:
    """Get current character arc progress from world memory."""
    try:
        from db import repo
        arc_data = repo.get_world_memory(novel_id, "character_arc_status") or {}
        if not arc_data:
            return "（无弧线记录）"
        lines = [f"{char}: {status}" for char, status in arc_data.items()]
        return "\n".join(lines[:5])
    except Exception:
        return "（无法加载）"


def _default_narrative(next_chapter_id: int, current_arc: str) -> dict:
    """Safe default when LLM fails."""
    arc_cycle = {
        "setup": "buildup",
        "buildup": "climax",
        "climax": "cooldown",
        "cooldown": "setup",
    }
    next_arc = arc_cycle.get(current_arc, "buildup")
    return {
        "arc_phase": next_arc,
        "emotion_curve": "平稳推进",
        "conflict_intensity": 0.6,
        "next_chapter_goal": f"推进第{next_chapter_id}章主线",
        "pacing_note": "标准节奏",
        "must_handle": [],
        "style_variance": "",
    }
