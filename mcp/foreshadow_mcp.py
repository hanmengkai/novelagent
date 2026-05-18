"""
mcp/foreshadow_mcp.py — Foreshadow MCP (伏笔状态机)

State machine: BURIED → ACTIVE → DUE → RESOLVED

Provides:
  plant(novel_id, chapter_no, description, ...)
  activate(novel_id, fshadow_id)
  mark_due(novel_id, chapter_no) → list of due foreshadows
  resolve(novel_id, fshadow_id, chapter_no)
  get_active(novel_id) → list
  get_overdue(novel_id, current_chapter) → list of overdue
  update(state) → LangGraph node entry point
"""
from typing import Optional
from loguru import logger
from db import repo
from langgraph_engine.state import NovelState


# ── State constants ──────────────────────────────────────────────────────
BURIED   = "BURIED"
ACTIVE   = "ACTIVE"
DUE      = "DUE"
RESOLVED = "RESOLVED"

# Minimum chapters a foreshadow must live before it can be resolved
_MIN_LIFESPAN = {"core": 20, "major": 10, "minor": 3}

# Default window (chapters) added to burial chapter when due_range_end is None
_DEFAULT_DUE_WINDOW = {"core": 40, "major": 25, "minor": 15}

# ═══════════════════════════════════════════════════════
#  Public MCP API
# ═══════════════════════════════════════════════════════

_COLLECTION_WINDOW = {"core": 20, "major": 15, "minor": 10}


def plant(
    novel_id: str,
    chapter_no: int,
    description: str,
    importance: str = "minor",
    due_range_start: Optional[int] = None,
    due_range_end: Optional[int] = None,
    extra: Optional[dict] = None,
) -> str:
    """Plant a new foreshadow. Returns its ID.

    State rules:
    - Pass extra={'state': 'BURIED'} for foreshadows that should stay hidden
      until they approach their due window (core/major long-arc foreshadows).
    - Default state is ACTIVE: foreshadow appears in prompts immediately.

    due_range_start is auto-computed from due_range_end when not provided,
    using a collection window sized by importance (core=20ch, major=15ch, minor=10ch).
    This ensures mark_due() can fire before the deadline.
    """
    fshadow_id = f"C{chapter_no}F{_next_seq(novel_id, chapter_no)}"

    # Extract state override without mutating caller's dict
    extra_copy = dict(extra) if extra else {}
    initial_state = extra_copy.pop("state", ACTIVE)

    # Auto-compute due_range_start so mark_due() can fire before the deadline
    if due_range_end is not None and due_range_start is None:
        window = _COLLECTION_WINDOW.get(importance, 10)
        due_range_start = max(chapter_no + 1, due_range_end - window)

    repo.upsert_foreshadow(novel_id, fshadow_id, {
        "description": description,
        "buried_chapter": chapter_no,
        "due_range_start": due_range_start,
        "due_range_end": due_range_end,
        "state": initial_state,
        "importance": importance,
        "extra": extra_copy,
    })
    logger.info(f"[ForeshadowMCP] planted ({initial_state}): {fshadow_id} — {description[:50]}")
    return fshadow_id


def activate(novel_id: str, fshadow_id: str) -> None:
    """Transition BURIED → ACTIVE (foreshadow is being developed)."""
    repo.transition_foreshadow_state(novel_id, fshadow_id, ACTIVE)
    logger.debug(f"[ForeshadowMCP] activated: {fshadow_id}")


def mark_due(novel_id: str, chapter_no: int) -> list[dict]:
    """
    Transition ACTIVE foreshadows to DUE if chapter_no falls within due_range.
    Also auto-activates any BURIED foreshadows whose due_range has arrived,
    so they don't silently miss their window.
    Returns list of newly-due foreshadows.
    """
    from sqlalchemy import text
    from db.json_session import get_db
    due_list = []
    with get_db() as db:
        # First: auto-activate BURIED foreshadows whose due window has arrived
        buried_due = db.execute(text(
            "SELECT fshadow_id FROM foreshadowing "
            "WHERE novel_id=:nid AND state=:buried "
            "AND due_range_start IS NOT NULL AND due_range_start<=:cn"
        ), {"nid": novel_id, "buried": BURIED, "cn": chapter_no}).mappings().all()
        for row in buried_due:
            repo.transition_foreshadow_state(novel_id, row["fshadow_id"], ACTIVE)
            logger.info(f"[ForeshadowMCP] auto-activated BURIED→ACTIVE (due window arrived): {row['fshadow_id']}")

        # Then: transition ACTIVE → DUE
        rows = db.execute(text(
            "SELECT fshadow_id FROM foreshadowing "
            "WHERE novel_id=:nid AND state=:s "
            "AND due_range_start IS NOT NULL AND due_range_start<=:cn "
            "AND (due_range_end IS NULL OR due_range_end>=:cn)"
        ), {"nid": novel_id, "s": ACTIVE, "cn": chapter_no}).mappings().all()
        for row in rows:
            repo.transition_foreshadow_state(novel_id, row["fshadow_id"], DUE)
            due_list.append(repo.get_foreshadow(novel_id, row["fshadow_id"]))
    return [f for f in due_list if f]


def resolve(novel_id: str, fshadow_id: str, chapter_no: int) -> None:
    """Transition DUE/ACTIVE → RESOLVED.

    Blocks resolution if the foreshadow is younger than the minimum lifespan
    for its importance level — prevents organic foreshadows from being auto-
    resolved in the same chapter they were planted.
    """
    fs = repo.get_foreshadow(novel_id, fshadow_id)
    if fs:
        buried = fs.get("buried_chapter") or chapter_no
        importance = fs.get("importance", "minor")
        min_age = _MIN_LIFESPAN.get(importance, 3)
        age = chapter_no - buried
        if age < min_age:
            logger.warning(
                f"[ForeshadowMCP] blocked premature resolve: {fshadow_id} "
                f"(age={age} < min={min_age}, importance={importance})"
            )
            return
    repo.transition_foreshadow_state(novel_id, fshadow_id, RESOLVED, resolve_chapter=chapter_no)
    logger.info(f"[ForeshadowMCP] resolved: {fshadow_id} at chapter {chapter_no}")


def get_active(novel_id: str) -> list[dict]:
    return repo.list_foreshadows(novel_id, state=ACTIVE)


def get_buried(novel_id: str) -> list[dict]:
    return repo.list_foreshadows(novel_id, state=BURIED)


def get_due(novel_id: str) -> list[dict]:
    return repo.list_foreshadows(novel_id, state=DUE)


def get_overdue(novel_id: str, current_chapter: int) -> list[dict]:
    """
    Foreshadows where due_range_end < current_chapter and still not resolved.
    These are CRITICAL — must be resolved soon.
    """
    from sqlalchemy import text
    from db.json_session import get_db
    with get_db() as db:
        rows = db.execute(text(
            "SELECT * FROM foreshadowing "
            "WHERE novel_id=:nid AND state!=:resolved "
            "AND due_range_end IS NOT NULL AND due_range_end<:cn"
        ), {"nid": novel_id, "resolved": RESOLVED, "cn": current_chapter}).mappings().all()
        return [dict(r) for r in rows]


def get_all_unresolved(novel_id: str) -> list[dict]:
    """All non-resolved foreshadows."""
    from sqlalchemy import text
    from db.json_session import get_db
    with get_db() as db:
        rows = db.execute(text(
            "SELECT * FROM foreshadowing "
            "WHERE novel_id=:nid AND state!=:resolved ORDER BY buried_chapter"
        ), {"nid": novel_id, "resolved": RESOLVED}).mappings().all()
        return [dict(r) for r in rows]


def format_for_prompt(novel_id: str, current_chapter: int) -> str:
    """Format active foreshadows + overdue as a concise prompt section.

    Priority order: overdue > DUE > core/major ACTIVE > minor ACTIVE
    Shows up to 8 active to give the LLM enough context to resolve them.
    """
    active = get_active(novel_id)
    due = get_due(novel_id)
    overdue = get_overdue(novel_id, current_chapter)

    # Sort active by importance: core first, then major, then minor
    _imp = {"core": 0, "major": 1, "minor": 2}
    active_sorted = sorted(active, key=lambda f: _imp.get(f.get("importance", "minor"), 2))

    lines = []
    if overdue:
        lines.append("【⚠️ 逾期必须回收的伏笔】")
        for f in overdue:
            lines.append(f"  - [{f['fshadow_id']}] {f['description']} (planted ch{f['buried_chapter']})")
    if due:
        lines.append("【本章应回收的伏笔】")
        for f in due:
            lines.append(f"  - [{f['fshadow_id']}] {f['description']}")
    if active_sorted:
        lines.append("【活跃中的伏笔（可推进/回收）】")
        for f in active_sorted[:8]:  # show top 8, importance-ordered
            lines.append(f"  - [{f['fshadow_id']}] [{f.get('importance','?')}] {f['description']}")
        if len(active_sorted) > 8:
            lines.append(f"  （另有 {len(active_sorted)-8} 个次要伏笔未显示）")

    return "\n".join(lines) if lines else "无待处理伏笔"


# ═══════════════════════════════════════════════════════
#  LangGraph Node: foreshadow_update
# ═══════════════════════════════════════════════════════

def update(state: NovelState) -> NovelState:
    """
    LangGraph node: apply foreshadow operations from the completed chapter.

    Two sources of operations are processed (in order):
      1. chapter_plan.foreshadow_ops  — planned ops (plant / activate / resolve)
      2. state.new_foreshadows        — organic foreshadows found by fact_extractor
         state.resolved_foreshadows  — additional resolves found by fact_extractor
         (fact_extractor already deduplicates against planned ops)
    """
    if state.error:
        logger.warning(f"⚠️  [伏笔更新] 跳过（上游错误: {state.error}）")
        return state

    novel_id = state.novel_id
    chapter_no = state.chapter_id

    planned_ops = state.chapter_plan.foreshadow_ops if state.chapter_plan else []
    planned_plant_count = 0
    planned_activate_count = 0
    planned_resolve_count = 0

    # ── 1. Process planned ops from chapter_plan ─────────────────────
    for op in planned_ops:
        op_type = op.get("op", "")
        fid = op.get("id", "")
        desc = op.get("description", "")

        if op_type == "plant":
            plant(
                novel_id=novel_id,
                chapter_no=chapter_no,
                description=desc,
                importance=op.get("importance", "minor"),
                due_range_start=op.get("due_range_start"),
                due_range_end=op.get("due_range_end"),
            )
            planned_plant_count += 1

        elif op_type == "activate" and fid and fid != "new":
            activate(novel_id, fid)
            planned_activate_count += 1

        elif op_type == "resolve" and fid and fid != "new":
            resolve(novel_id, fid, chapter_no)
            planned_resolve_count += 1

    # ── 2. Plant organic (unplanned) foreshadows from fact_extractor ──
    for f in state.new_foreshadows:
        importance = f.get("importance", "minor")
        due_end = f.get("due_range_end")
        if due_end is None:
            # Assign a default resolution deadline so foreshadows don't drift forever
            due_end = chapter_no + _DEFAULT_DUE_WINDOW.get(importance, 15)
        plant(
            novel_id=novel_id,
            chapter_no=chapter_no,
            description=f.get("description", ""),
            importance=importance,
            due_range_start=f.get("due_range_start"),
            due_range_end=due_end,
        )

    # ── 3. Resolve additional foreshadows from fact_extractor ─────────
    for fshadow_id in state.resolved_foreshadows:
        resolve(novel_id, fshadow_id, chapter_no)

    # ── 4. Auto-activate BURIED foreshadows when they reach their due window ──
    # Use due_range_start as the activation threshold; fall back to buried+3
    # for legacy foreshadows that lack due_range_start.
    buried = get_buried(novel_id)
    auto_activated = 0
    for f in buried:
        buried_ch = f.get("buried_chapter") or chapter_no
        activate_at = f.get("due_range_start") or (buried_ch + 3)
        if chapter_no >= activate_at:
            activate(novel_id, f["fshadow_id"])
            auto_activated += 1
    if auto_activated:
        logger.info(f"[ForeshadowMCP] auto-activated {auto_activated} BURIED foreshadows")

    # ── 5. Mark DUE for next chapter ──────────────────────────────────
    mark_due(novel_id, chapter_no + 1)

    logger.info(
        f"[ForeshadowMCP] update done ch{chapter_no}: "
        f"planned(plant={planned_plant_count} activate={planned_activate_count} "
        f"resolve={planned_resolve_count})  "
        f"organic(+{len(state.new_foreshadows)} planted, "
        f"{len(state.resolved_foreshadows)} resolved)"
    )
    return state


# ── internal ─────────────────────────────────────────────

def _next_seq(novel_id: str, chapter_no: int) -> int:
    """Get next sequence number for foreshadow IDs in this chapter."""
    existing = repo.list_foreshadows(novel_id)
    prefix = f"C{chapter_no}F"
    count = sum(1 for f in existing if f.get("fshadow_id", "").startswith(prefix))
    return count + 1
