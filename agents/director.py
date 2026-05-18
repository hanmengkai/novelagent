"""
agents/director.py — Director Agent (叙事总导演)

Responsibilities:
  - Control main plot direction
  - Manage conflict structure
  - Define arc phases per chapter
"""
from loguru import logger
from db.novel_log import log_info
from langgraph_engine.state import NovelState, ArcPhase
from llm import simple_chat_json
from mcp import memory_mcp, foreshadow_mcp, reader_mcp
from config.prompts import DIRECTOR_SYSTEM, DIRECTOR_PROMPT
from db import repo


def run(state: NovelState) -> NovelState:
    """LangGraph node: Director determines chapter direction."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id

    # Load world snapshot from MCP (facts only, no hallucination)
    world_snapshot = memory_mcp.get_world_snapshot(novel_id)
    state.world_snapshot = world_snapshot
    state.author_intent = world_snapshot.get("author_intent", "")
    state.current_focus = world_snapshot.get("current_focus", "")
    state.thematic_core = world_snapshot.get("thematic_core", {})

    # Load active characters
    state.active_characters = memory_mcp.get_all_characters(novel_id)

    # Load foreshadow status
    foreshadow_status = foreshadow_mcp.format_for_prompt(novel_id, chapter_id)
    state.foreshadowing_due = foreshadow_mcp.get_due(novel_id)
    state.foreshadowing_active = foreshadow_mcp.get_active(novel_id)

    # Load reader engagement trend
    trend = reader_mcp.get_trend(novel_id, last_n=5)
    reader_trend_str = (
        f"近5章均值: engagement={trend['avg_engagement']:.2f}, "
        f"tension={trend['avg_tension']:.2f}, "
        f"trend={trend['trend']}"
    )

    # Build diversity log from recent chapters
    recent_diversity_log = _build_diversity_log(novel_id, chapter_id)

    # Extract protagonist main goal for main-plot tracking
    protagonist_goal = world_snapshot.get("protagonist", {}).get("goal", "（未设定）")

    # Build pacing alert when recent chapters are consistently low-tension
    pacing_alert = _build_pacing_alert(novel_id, chapter_id, trend)

    # Build character absence alert (P3: 角色出场追踪)
    absence_alert = _build_absence_alert(novel_id, chapter_id, state.active_characters)

    # Get volume goal + chapter outline
    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{state.volume_no}") or {}
    chapter_outlines = volume_plan.get("chapter_outlines", [])
    chapter_outline = next(
        (c for c in chapter_outlines if c.get("chapter_no") == chapter_id), {}
    )
    volume_goal = volume_plan.get("volume_goal", "")
    arc_phase_str = chapter_outline.get("arc_phase", "buildup")

    # Get last chapter ending
    last_ending = repo.get_world_memory(novel_id, "last_chapter_ending") or ""

    # Build plot milestones from novel description (anti-drift: prevent terminal-loop)
    plot_milestones = _build_plot_milestones(novel_id, chapter_id)

    # Call Director LLM
    result = simple_chat_json(
        system_prompt=DIRECTOR_SYSTEM,
        user_prompt=DIRECTOR_PROMPT.format(
            chapter_id=chapter_id,
            author_intent=state.author_intent,
            current_focus=state.current_focus,
            volume_goal=volume_goal,
            arc_phase=arc_phase_str,
            chapter_outline=_format_outline(chapter_outline),
            last_chapter_ending=str(last_ending)[:300],
            foreshadow_status=foreshadow_status,
            reader_trend=reader_trend_str,
            recent_diversity_log=recent_diversity_log,
            protagonist_goal=protagonist_goal,
            pacing_alert=pacing_alert,
            absence_alert=absence_alert,
            plot_milestones=plot_milestones,
        ),
        fallback=_default_directive(chapter_id),
    )

    # Store directive in state for Planner
    state.memory_snapshot["director_directive"] = result
    state.memory_snapshot["chapter_outline"] = chapter_outline
    state.memory_snapshot["volume_goal"] = volume_goal
    state.memory_snapshot["main_plot_step"] = result.get("main_plot_step", "")

    # Set narrative arc phase, capping runaway climax streaks
    arc_raw = _cap_consecutive_climax(novel_id, chapter_id, arc_phase_str)
    try:
        state.memory_snapshot["arc_phase"] = ArcPhase(arc_raw)
    except ValueError:
        state.memory_snapshot["arc_phase"] = ArcPhase.BUILDUP

    logger.info(f"🎬 [导演] 第{chapter_id}章方向: {result.get('chapter_direction', 'N/A')}"
                f"  冲突={result.get('conflict_type','?')}  节奏={result.get('pacing_note','?')}")
    log_info(novel_id, f"🎬 [导演] 第{chapter_id}章方向: {result.get('chapter_direction', 'N/A')[:60]}")
    return state


def _format_outline(outline: dict) -> str:
    if not outline:
        return "（无大纲，自由发挥）"
    return (
        f"标题：{outline.get('title', '')}\n"
        f"目标：{outline.get('goal', '')}\n"
        f"关键事件：{outline.get('key_event', '')}\n"
        f"冲突：{outline.get('conflict', '')}\n"
        f"结尾钩子：{outline.get('ending_hook', '')}"
    )


def _build_diversity_log(novel_id: str, chapter_id: int) -> str:
    """Build a log of recent chapter arc phases and titles to guide diversity."""
    try:
        summaries = repo.get_world_memory(novel_id, "_chapter_summaries") or {}
        if not summaries:
            # Fall back to reading chapter_summaries directly from file store
            from db import file_store as fs
            summaries = fs.load_json(novel_id, "chapter_summaries", default={})
        start = max(1, chapter_id - 6)
        lines = []
        for ch_no in range(start, chapter_id):
            s = summaries.get(str(ch_no), {})
            arc = s.get("arc_phase", "?")
            title = s.get("summary_text", "")[:30]
            lines.append(f"第{ch_no}章: arc={arc}  {title}")
        return "\n".join(lines) if lines else "（无近期记录）"
    except Exception:
        return "（无法加载）"


def _cap_consecutive_climax(novel_id: str, chapter_id: int, arc_phase: str,
                             max_streak: int = 4) -> str:
    """Downgrade arc_phase from climax if the last max_streak chapters were all climax."""
    if arc_phase != "climax":
        return arc_phase
    try:
        from db import file_store as fs
        summaries = fs.load_json(novel_id, "chapter_summaries", default={})
        streak = 0
        for ch_no in range(chapter_id - 1, max(0, chapter_id - max_streak - 1), -1):
            s = summaries.get(str(ch_no), {})
            if s.get("arc_phase") == "climax":
                streak += 1
            else:
                break
        if streak >= max_streak:
            logger.info(
                f"🎬 [导演] 第{chapter_id}章: 连续{streak}章高潮，强制降为cooldown")
            return "cooldown"
    except Exception:
        pass
    return arc_phase


def _build_pacing_alert(novel_id: str, chapter_id: int, trend: dict) -> str:
    """Return a hard pacing constraint string when recent chapters are too slow."""
    avg_tension = trend.get("avg_tension", 0.5)
    trend_dir = trend.get("trend", "flat")
    if avg_tension >= 0.4 or trend_dir == "rising":
        return ""
    # Verify with arc_phase history: require 3+ consecutive low-tension phases
    try:
        from db import file_store as fs
        summaries = fs.load_json(novel_id, "chapter_summaries", default={})
        low_phases = {"setup", "cooldown"}
        streak = 0
        for ch_no in range(chapter_id - 1, max(0, chapter_id - 4), -1):
            arc = summaries.get(str(ch_no), {}).get("arc_phase", "buildup")
            if arc in low_phases:
                streak += 1
            else:
                break
        if streak < 3:
            return ""
    except Exception:
        return ""
    return (
        "\n【节奏预警】近期连续低张力章节过多！"
        "本章必须安排至少一个明确的冲突推进事件；"
        "scene_type_requirement 不得选『日常生活』；"
        "conflict_goal 须有实质推进内容，不得填『铺垫』或『维持现状』。\n"
    )


def _build_absence_alert(novel_id: str, chapter_id: int,
                          active_chars: list[dict]) -> str:
    """Check if any characters have been absent for too long.
    
    Returns a warning string for the director prompt if any major
    characters haven't appeared in 15+ chapters.
    """
    try:
        from db import repo as _repo
        last_seen = _repo.get_character_last_seen(novel_id)
        if not last_seen:
            return ""
        char_names = {c.get("char_id", ""): c.get("name", "?") for c in active_chars}
        warnings = []
        threshold = 15
        for cid, last_ch in last_seen.items():
            if chapter_id - last_ch > threshold:
                name = char_names.get(cid, cid)
                if name and name != "?":
                    absent_for = chapter_id - last_ch
                    warnings.append(
                        f"  - {name}（已{absent_for}章未出场）"
                    )
        if not warnings:
            return ""
        return (
            "\n【角色缺席预警】以下角色已超过15章未出现，\n"
            + "\n".join(warnings)
            + "\n如非故意冷藏，建议在本卷安排出场。\n"
        )
    except Exception:
        return ""


def _default_directive(chapter_id: int) -> dict:
    return {
        "chapter_direction": f"推进第{chapter_id}章主线剧情",
        "main_plot_step": f"第{chapter_id}章主线向前推进一步",
        "conflict_type": "外部冲突",
        "conflict_goal": "制造紧张感",
        "pacing_note": "中速推进",
        "must_achieve": ["完成一个冲突"],
        "foreshadow_instruction": "none",
        "emotion_target": "平稳→紧张→缓和",
        "chapter_end_hook_level": 3,
        "scene_type_requirement": "对话",
        "forbidden_this_chapter": [],
    }


def _build_plot_milestones(novel_id: str, chapter_id: int) -> str:
    """Build a list of plot milestones from novel description / author intent.
    
    Extracts key plot points from the novel's core.json volumes and author_intent,
    filtering out ones already completed. Used by Director to enforce plot progression
    and prevent terminal-loop chapters.
    """
    try:
        from db import file_store as fs
        core = fs.load_json(novel_id, "core.json", default={})
        volumes = core.get("volumes", core.get("world_memory", {}).get("volumes", []))
        novel = repo.get_novel(novel_id)
    except Exception:
        novel = repo.get_novel(novel_id)
        volumes = []

    if not volumes:
        # Fall back to extracting from novel description
        description = (novel or {}).get("description", "")
        if not description:
            return "（无全书里程碑数据）"
        # Extract key plot lines from the description's volume structure
        lines = []
        # Look for "第X卷" patterns
        import re
        vol_blocks = re.split(r'###\s*【第.*卷】', description)
        if len(vol_blocks) <= 1:
            vol_blocks = re.split(r'【第.*卷】', description)
        for block in vol_blocks[1:]:
            # Extract first sentence/key plot
            for line in block.split('\n'):
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('-') and not line.startswith('*'):
                    lines.append(f"  • {line[:100]}")
                    break
        if lines:
            return "全书核心情节里程碑（按顺序推进）：\n" + "\n".join(lines[:10])
        return "（无全书里程碑数据）"
    
    # Extract key plot from each volume's description
    milestones = []
    for v in volumes[:10]:
        vn = v.get("volume_number", "?")
        goal = v.get("goal", v.get("description", ""))
        if isinstance(goal, str) and goal:
            milestones.append(f"  卷{vn}: {goal[:80]}")

    if milestones:
        return "全书核心情节里程碑（按卷序推进）：\n" + "\n".join(milestones)
    return "（无全书里程碑数据）"
