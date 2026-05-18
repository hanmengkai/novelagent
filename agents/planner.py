"""
agents/planner.py — Planner Agent (章节结构规划师)

Input:  director directive, character state, world rules, foreshadow info
Output: ChapterPlan with scenes, key_characters, constraints
"""
from loguru import logger
from db.novel_log import log_info
from langgraph_engine.state import NovelState, ChapterPlan
from llm import simple_chat_json
from mcp import world_mcp, foreshadow_mcp, style_mcp
from config.prompts import PLANNER_SYSTEM, PLANNER_PROMPT
from config import get_settings
from db import repo


def run(state: NovelState) -> NovelState:
    """LangGraph node: Planner creates detailed chapter structure."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id
    s = get_settings()

    directive = state.memory_snapshot.get("director_directive", {})
    main_plot_step = state.memory_snapshot.get("main_plot_step", "") or directive.get("main_plot_step", "（导演未指定）")
    world_rules_str = world_mcp.format_rules_for_prompt(novel_id)
    foreshadow_info = foreshadow_mcp.format_for_prompt(novel_id, chapter_id)

    # Load existing chapter titles to prevent duplicates
    used_titles = _get_used_titles(novel_id)

    # Build compact character summary (top 5 most relevant)
    character_summary = _build_character_summary(state.active_characters)

    # Build plot anchor to prevent drift
    plot_anchor = _build_plot_anchor_string(novel_id, state.volume_no)

    result = simple_chat_json(
        system_prompt=PLANNER_SYSTEM,
        user_prompt=PLANNER_PROMPT.format(
            chapter_id=chapter_id,
            director_directive=_format_directive(directive),
            plot_anchor=plot_anchor,
            main_plot_step=main_plot_step,
            character_summary=character_summary,
            world_rules=world_rules_str,
            foreshadow_info=foreshadow_info,
            used_titles=used_titles,
            chapter_target_chars=s.chapter_target_chars,
        ),
        fallback=_default_plan(chapter_id, directive),
    )

    # Build ChapterPlan dataclass
    plan = ChapterPlan(
        title=result.get("title", f"第{chapter_id}章"),
        goal=result.get("goal", "推进主线"),
        key_scenes=result.get("key_scenes", []),
        key_characters=[k for k in result.get("key_characters", []) if k],
        must_include=result.get("must_include", []),
        must_avoid=result.get("must_avoid", []),
        conflict_setup=result.get("conflict_setup", ""),
        foreshadow_ops=result.get("foreshadow_ops", []),
    )

    state.chapter_plan = plan
    state.style_signature = style_mcp.get_style_signature(novel_id)
    state.memory_snapshot["chapter_arc"] = result.get("chapter_arc", "")
    state.memory_snapshot["ending_type"] = result.get("ending_type", "悬念")
    state.memory_snapshot["key_scenes"] = result.get("key_scenes", [])

    logger.info(f"📐 [规划师] 第{chapter_id}章《{plan.title}》: "
                f"{len(plan.key_scenes)} 场景  {len(plan.key_characters)} 角色"
                f"  结尾={state.memory_snapshot.get('ending_type','?')}")
    log_info(novel_id, f"📐 [规划师] 第{chapter_id}章《{plan.title}》 {len(plan.key_scenes)}场景")
    return state


def _get_used_titles(novel_id: str) -> str:
    """Load all existing chapter titles to prevent duplicates."""
    try:
        from db.json_session import get_db
        from sqlalchemy import text
        with get_db() as db:
            rows = db.execute(text(
                "SELECT chapter_no, title FROM chapters WHERE novel_id=:nid AND title!='' ORDER BY chapter_no"
            ), {"nid": novel_id}).mappings().all()
        if not rows:
            return "（无已用标题）"
        return "\n".join(f"第{r['chapter_no']}章：{r['title']}" for r in rows)
    except Exception:
        return "（无法加载）"


def _build_character_summary(characters: list[dict]) -> str:
    if not characters:
        return "无角色信息"
    lines = []
    for c in characters[:8]:  # limit to avoid context bloat
        lines.append(
            f"• {c.get('name', c.get('char_id', '?'))}："
            f"[{c.get('power_level', '未知')}] "
            f"位置:{c.get('location', '?')} "
            f"状态:{c.get('status', 'alive')} "
            f"情绪:{c.get('emotion_state', '?')}"
        )
    return "\n".join(lines)


def _format_directive(directive: dict) -> str:
    return (
        f"方向：{directive.get('chapter_direction', '')}\n"
        f"主线推进步：{directive.get('main_plot_step', '（未指定）')}\n"
        f"冲突类型：{directive.get('conflict_type', '')}\n"
        f"冲突目标：{directive.get('conflict_goal', '')}\n"
        f"节奏要求：{directive.get('pacing_note', '')}\n"
        f"必须完成：{', '.join(directive.get('must_achieve', []))}\n"
        f"情绪目标：{directive.get('emotion_target', '')}\n"
        f"本章禁止：{', '.join(directive.get('forbidden_this_chapter', []))}"
    )


def _default_plan(chapter_id: int, directive: dict) -> dict:
    return {
        "title": f"第{chapter_id}章",
        "goal": directive.get("chapter_direction", "推进主线"),
        "key_scenes": [
            {"scene_no": 1, "description": "开场铺垫", "characters": [], "purpose": "建立场景", "emotion": "平静", "emotion_technique": "环境烘托"},
            {"scene_no": 2, "description": "冲突爆发", "characters": [], "purpose": "制造张力", "emotion": "紧张", "emotion_technique": "内心独白+身体感知"},
            {"scene_no": 3, "description": "结尾悬念", "characters": [], "purpose": "吸引下章", "emotion": "期待", "emotion_technique": "对话潜台词"},
        ],
        "key_characters": [],
        "conflict_setup": directive.get("conflict_goal", ""),
        "must_include": directive.get("must_achieve", []),
        "must_avoid": directive.get("forbidden_this_chapter", []),
        "foreshadow_ops": [],
        "chapter_arc": "平静→紧张→期待",
        "ending_type": "悬念",
        "word_count_target": 4000,
    }


def _build_plot_anchor_string(novel_id: str, volume_no: int) -> str:
    """Build a compact plot-direction anchor from story_outline + volume_plan.
    
    This is injected into Writer and Planner prompts to prevent plot drift
    across long-form generation (200+ chapters).
    """
    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}") or {}
    
    parts = []
    
    core_theme = story_outline.get("core_theme", "")
    ending_dir = story_outline.get("ending_direction", "")
    if core_theme:
        parts.append(f"全书核心主题：{core_theme}")
    if ending_dir:
        parts.append(f"结局方向：{ending_dir}")
    
    vol_goal = volume_plan.get("volume_goal", "")
    if vol_goal:
        parts.append(f"本卷目标：{vol_goal}")
    
    if not parts:
        return ""
    
    return (
        "【全书剧情锚点（严格遵循）】\n"
        + "\n".join(parts)
        + "\n"
    )
