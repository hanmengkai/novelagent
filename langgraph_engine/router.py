"""
langgraph_engine/router.py — Conditional edge routing logic for LangGraph.
Pure functions that inspect NovelState and return the next node name.
"""
from .state import NovelState, IssueSeverity


def route_after_checker(state: NovelState) -> str:
    """
    After Checker runs:
    - HIGH severity issues AND retries remaining → RepairAgent
    - HIGH severity AND no retries left → force_save (accept with warning)
    - MEDIUM/LOW only → NarrativeController (log but skip repair to avoid
      wasting 60s+ R1 + 30s Repair cycles on trivial issues like typos)
    - No issues → NarrativeController
    """
    if state.has_high_severity_issues():
        if state.should_retry():
            return "repair_agent"
        else:
            return "narrative_controller"   # force-accept after max retries
    # MEDIUM-only issues: skip repair, the text is fine enough
    medium_count = sum(1 for i in state.issues if i.severity == IssueSeverity.MEDIUM)
    if medium_count:
        from loguru import logger
        logger.info(
            f"✅ [路由] {medium_count}个MEDIUM问题，无HIGH问题，跳过修复直接放行"
        )
    return "narrative_controller"


def route_after_repair(state: NovelState) -> str:
    """
    After RepairAgent applies patches, re-run Checker.
    If retry budget exhausted, proceed regardless.
    If repairs were minimal (< 3 patches affecting < 200 chars total),
    skip re-check to avoid unnecessary round-trips.
    """
    if state.retry_count >= state.max_retries:
        return "narrative_controller"
    # Minimal repair: skip re-check for tiny fixes
    patch_count = len(state.repair_patches)
    total_changed = sum(len(p.get("replacement", "")) for p in state.repair_patches)
    if patch_count < 3 and total_changed < 200:
        from loguru import logger
        logger.info(
            f"✅ [路由] 仅{patch_count}处微修复({total_changed}字)，跳过再检查"
        )
        return "narrative_controller"
    return "checker"


def route_after_foreshadow(state: NovelState) -> str:
    """
    After foreshadow update:
    - Check if compaction is needed (every N chapters)
    - Otherwise done
    """
    from config import get_settings
    s = get_settings()
    if state.chapter_id > 0 and state.chapter_id % s.compaction_interval == 0:
        return "compactor"
    return "__end__"


def route_after_compactor(state: NovelState) -> str:
    return "__end__"


