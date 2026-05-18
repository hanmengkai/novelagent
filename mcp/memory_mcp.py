"""
mcp/memory_mcp.py — Memory MCP (唯一事实源)

Provides:
  get_character(novel_id, char_id)
  update_character(novel_id, char_id, updates)
  get_world_snapshot(novel_id)
  append_event(novel_id, chapter_no, event)
  get_snapshot(novel_id, chapter_no)
  commit(state) → LangGraph node entry point
"""
import json
from typing import Optional, Any
from loguru import logger
from db import repo
from db.cache import set_chapter_context, get_chapter_context, rset, rget
from langgraph_engine.state import NovelState


# ═══════════════════════════════════════════════════════
#  Public MCP API
# ═══════════════════════════════════════════════════════

def get_character(novel_id: str, char_id: str) -> Optional[dict]:
    """Retrieve character state. SINGLE SOURCE OF TRUTH."""
    return repo.get_character(novel_id, char_id)


def get_all_characters(novel_id: str) -> list[dict]:
    return repo.get_all_characters(novel_id)


def update_character(novel_id: str, char_id: str, updates: dict) -> None:
    """
    Merge updates into existing character record.
    ONLY allowed via this MCP API — LLM must never update DB directly.

    Empty strings and empty lists in updates are treated as "no change" and
    will NOT overwrite existing non-empty values.  This prevents the
    fact_extractor from accidentally erasing data when it leaves fields blank.
    """
    existing = repo.get_character(novel_id, char_id) or {}

    # Extract the actual changes dict if the LLM returned the nested structure
    changes = updates.get("changes", {})
    flat_updates = {**updates}
    if changes and isinstance(changes, dict):
        flat_updates.update(changes)
    flat_updates.pop("changes", None)
    flat_updates.pop("is_new", None)  # meta key, not a DB field

    # Filter: do not let empty strings / empty lists overwrite existing non-empty values
    safe_updates = {}
    for k, v in flat_updates.items():
        if v == "" or v == [] or v == {}:
            continue  # skip blank — keep existing value
        safe_updates[k] = v

    merged = {**existing, **safe_updates}
    merged["novel_id"] = novel_id
    merged["char_id"] = char_id
    repo.upsert_character(novel_id, char_id, merged)
    logger.debug(f"[MemoryMCP] character updated: {char_id}")


def get_world(novel_id: str) -> dict:
    """Get world info (background, power system, rules)."""
    return repo.get_all_world_memory(novel_id)


def get_world_snapshot(novel_id: str) -> dict:
    """
    Build a compact world snapshot for injection into prompts.
    Includes: background, power_system, story_outline, current_focus, author_intent.
    """
    mem = repo.get_all_world_memory(novel_id)
    rules = repo.get_all_world_rules(novel_id)
    return {
        "background": mem.get("background", ""),
        "power_system": mem.get("power_system", ""),
        "story_outline_summary": _truncate(str(mem.get("story_outline", "")), 800),
        "author_intent": mem.get("author_intent", ""),
        "current_focus": mem.get("current_focus", ""),
        "world_rules": rules,
        "novel_style": mem.get("novel_style", {}),
        "protagonist": mem.get("protagonist", {}),
        "antagonist": mem.get("antagonist", {}),
        "thematic_core": mem.get("thematic_core", {}),
    }


def append_event(novel_id: str, chapter_no: int, event: dict) -> None:
    """Record a world event as a chapter fact."""
    repo.append_chapter_fact(
        novel_id=novel_id,
        chapter_no=chapter_no,
        fact_type=event.get("type", "world_event"),
        fact_text=event.get("text", ""),
        keywords=event.get("keywords", ""),
    )


def get_snapshot(novel_id: str, chapter_no: int) -> dict:
    """
    Build a full memory snapshot for a given chapter context.
    Used by ContextBuilder to assemble LLM prompts.
    """
    # Try Redis cache first
    cached = get_chapter_context(novel_id, chapter_no)
    if cached:
        return cached

    world = get_world_snapshot(novel_id)
    characters = get_all_characters(novel_id)
    recent_facts = repo.get_recent_facts(novel_id, max(1, chapter_no - 5))
    recent_summaries = repo.get_recent_summaries(novel_id, limit=3)

    snapshot = {
        "world": world,
        "characters": characters,
        "recent_facts": recent_facts,
        "recent_summaries": recent_summaries,
        "chapter_no": chapter_no,
    }

    # Cache for 48h
    set_chapter_context(novel_id, chapter_no, snapshot)
    return snapshot


# ═══════════════════════════════════════════════════════
#  LangGraph Node: memory_commit
# ═══════════════════════════════════════════════════════

def commit(state: NovelState) -> NovelState:
    """
    LangGraph node: persist all facts extracted from the completed chapter.
    Writes: chapter content, chapter facts, character updates, chapter summary.
    """
    if state.error:
        logger.warning(f"⚠️  [记忆提交] 跳过（上游错误: {state.error}）")
        return state

    novel_id = state.novel_id
    chapter_no = state.chapter_id

    # 1. Save chapter content
    final = state.final_text or state.edited_text or state.draft_text
    director_directive = state.memory_snapshot.get("director_directive", {})
    repo.upsert_chapter(novel_id, chapter_no, {
        "volume_no": state.volume_no,
        "title": state.chapter_plan.title if state.chapter_plan else f"第{chapter_no}章",
        "content": final,
        "word_count": len(final),
        "status": "done",
        "issues": [{"code": i.code, "desc": i.description} for i in state.issues],
        "scene_type": director_directive.get("scene_type_requirement"),
        "hook_level": director_directive.get("chapter_end_hook_level"),
    })

    # 2. Save to MinIO
    try:
        from db.minio_client import save_chapter as minio_save
        minio_save(novel_id, chapter_no, final)
    except Exception as e:
        logger.warning(f"MinIO save failed (non-fatal): {e}")

    # 3. Persist extracted facts
    for fact in state.extracted_facts:
        repo.append_chapter_fact(
            novel_id=novel_id,
            chapter_no=chapter_no,
            fact_type=fact.get("type", "other"),
            fact_text=fact.get("text", ""),
            keywords=fact.get("keywords", ""),
        )

    # 4. Apply character updates
    for upd in state.character_updates:
        char_id = upd.get("char_id") or upd.get("name", "")
        if not char_id:
            continue
        is_new = upd.get("is_new", False)
        existing = repo.get_character(novel_id, char_id)
        if existing is None and not is_new:
            # LLM flagged as existing but not found — treat as new
            is_new = True
        if is_new and existing is None:
            # New character: build a complete record from the extracted changes
            changes = upd.get("changes", {})
            extra_from_extractor = upd.get("extra", {})
            full_record = {
                "char_id": char_id,
                "name": upd.get("name", char_id),
                "status": changes.get("status") or "alive",
                "power_level": changes.get("power_level", "未知"),
                "location": changes.get("location", "未知"),
                "emotion_state": changes.get("emotion_state", ""),
                "physical_state": changes.get("physical_state", ""),
                "backstory": changes.get("backstory", ""),
                "personality": changes.get("personality") or [],
                "relationships": changes.get("relationships") or {},
                "extra": {
                    "want": extra_from_extractor.get("want", ""),
                    "fear": extra_from_extractor.get("fear", ""),
                    "contradiction": extra_from_extractor.get("contradiction", ""),
                    "role": extra_from_extractor.get("role", ""),
                },
            }
            repo.upsert_character(novel_id, char_id, full_record)
            logger.info(f"[MemoryMCP] new character created: {char_id}")
        else:
            update_character(novel_id, char_id, upd)

    # 4b. Advance protagonist arc_stage based on volume progress
    try:
        _advance_arc_stage(novel_id, state.volume_no)
    except Exception as _arc_err:
        logger.debug(f"[MemoryMCP] arc_stage advance skipped: {_arc_err}")

    # 5. Save chapter summary
    if state.chapter_plan:
        repo.upsert_chapter_summary(novel_id, chapter_no, {
            "volume_no": state.volume_no,
            "summary_text": _build_summary(state),
            "key_characters": state.chapter_plan.key_characters,
            "arc_phase": state.narrative_directive.arc_phase.value if state.narrative_directive else "",
            "emotion_peak": state.narrative_directive.conflict_intensity if state.narrative_directive else 0.5,
            "conflict_count": 1,
        })

    # 6. Update Redis recent summaries cache
    summaries = repo.get_recent_summaries(novel_id, limit=5)
    from db.cache import set_recent_summaries
    set_recent_summaries(novel_id, summaries)

    # 7. Save reader metrics
    if state.reader_metrics:
        repo.upsert_reader_metrics(
            novel_id=novel_id,
            chapter_no=chapter_no,
            engagement=state.reader_metrics.get("engagement", 0.5),
            tension=state.reader_metrics.get("tension", 0.5),
            drop_risk=state.reader_metrics.get("drop_risk", 0.5),
            raw=state.reader_metrics,
        )

    # 8. Index chapter into vector store for semantic search
    try:
        from db import vector_store as vs
        facts_to_index = []
        for fact in state.extracted_facts:
            facts_to_index.append({
                "fact_type": fact.get("type", "other"),
                "fact_text": fact.get("text", ""),
                "keywords": fact.get("keywords", ""),
                "chapter_no": chapter_no,
            })
        if facts_to_index:
            vs.add_facts(novel_id, facts_to_index)
        # Also index the chapter summary
        summary_text = _build_summary(state)
        if summary_text:
            vs.add_summaries(novel_id, [{
                "chapter_no": chapter_no,
                "summary_text": summary_text,
            }])
        # Re-index character states after commit
        try:
            from db import vector_store as _vs
            updated_chars = repo.get_all_characters(novel_id)
            if updated_chars:
                _vs.add_characters(novel_id, updated_chars)
        except Exception as _ve:
            logger.debug(f"[MemoryMCP] character re-index skipped: {_ve}")
    except Exception as _vs_err:
        logger.warning(f"[MemoryMCP] vector indexing failed (search degraded to keyword): {_vs_err}")

    # 9. Track character last seen (P3: 角色出场追踪)
    # Non-fatal: missing last_seen data degrades analytics but doesn't break generation.
    try:
        if state.chapter_plan and state.chapter_plan.key_characters:
            from db import repo as _repo
            _repo.update_character_last_seen(
                novel_id,
                state.chapter_plan.key_characters,
                chapter_no,
            )
    except Exception:
        pass

    # Style drift check (every 10 chapters)
    try:
        from mcp import style_mcp as _smc
        drift = _smc.detect_style_drift(novel_id, chapter_no)
        if drift["drifted"]:
            state.memory_snapshot["style_drift_warning"] = drift["recalibration"]
            logger.warning(
                f"[MemoryMCP] 风格漂移检测: ch{chapter_no} — "
                + "; ".join(drift["issues"][:3])
            )
    except Exception as _sd_err:
        logger.debug(f"[MemoryMCP] style drift check skipped: {_sd_err}")

    logger.info(f"[MemoryMCP] committed chapter {chapter_no}: {len(final)} chars")
    return state


# ── helpers ─────────────────────────────────────────────

def _advance_arc_stage(novel_id: str, volume_no: int) -> None:
    novel = repo.get_novel(novel_id)
    if not novel:
        return
    total_volumes = novel.get("total_volumes", 10)
    progress = volume_no / total_volumes
    if progress <= 0.25:
        target_stage = "初心"
    elif progress <= 0.50:
        target_stage = "动摇"
    elif progress <= 0.75:
        target_stage = "抉择"
    else:
        target_stage = "蜕变"

    STAGE_ORDER = ["初心", "动摇", "抉择", "蜕变"]

    chars = repo.get_all_characters(novel_id)
    protagonist = next(
        (c for c in chars if c.get("extra", {}).get("goal") or c.get("extra", {}).get("want")),
        None,
    )
    if not protagonist:
        return

    current_stage = protagonist.get("extra", {}).get("arc_stage", "初心")
    current_idx = STAGE_ORDER.index(current_stage) if current_stage in STAGE_ORDER else 0
    target_idx = STAGE_ORDER.index(target_stage) if target_stage in STAGE_ORDER else 0
    if target_idx <= current_idx:
        return

    extra = {**protagonist.get("extra", {}), "arc_stage": target_stage}
    char_id = protagonist.get("char_id", "")
    if char_id:
        repo.upsert_character(novel_id, char_id, {**protagonist, "extra": extra})
        logger.info(f"[MemoryMCP] protagonist arc_stage: {current_stage} → {target_stage}")


def _build_summary(state: NovelState) -> str:
    if state.chapter_plan:
        return f"第{state.chapter_id}章《{state.chapter_plan.title}》目标：{state.chapter_plan.goal}"
    return f"第{state.chapter_id}章"


def _truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text
