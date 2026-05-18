"""
agents/writer.py — Writer Agent (无记忆生成器)

The Writer has NO long-term memory.
Everything it needs is injected via MCP data + chapter_plan.

Uses 2-part generation to handle long chapters:
  Part 1: ~half the target chars
  Part 2: continuation from Part 1 ending
"""
import re
from loguru import logger
from db.novel_log import log_info
from langgraph_engine.state import NovelState
from llm import chat
from mcp import foreshadow_mcp, style_mcp, world_mcp
from config.prompts import (
    WRITER_SYSTEM, WRITER_PART1_PROMPT, WRITER_PART2_PROMPT,
    WRITER_CONTINUE_SYSTEM, WRITER_CONTINUE_PROMPT,
    OPENING_CHAPTER_DIRECTIVES, ENDING_VOLUME_DIRECTIVE, ENDING_CHAPTER_DIRECTIVE,
)
from config import get_settings
from db import repo


def run(state: NovelState) -> NovelState:
    """LangGraph node: Writer generates chapter draft."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id
    plan = state.chapter_plan
    s = get_settings()

    if not plan:
        logger.error(f"❌ [写手] 第{chapter_id}章无章节计划，无法生成")
        state.error = "Writer: missing chapter_plan"
        return state

    ctx = _build_writer_context(state, s, plan)

    part1, usage1, system_p1 = _generate_part1(chapter_id, plan, ctx, s)
    if part1 is None:
        state.error = f"Writer Part1: generation failed"
        return state

    part2, usage2 = _generate_part2(chapter_id, plan, ctx, s, part1, usage1, system_p1)
    if part2 is None:
        state.draft_text = _strip_meta_headers(part1)
        return state

    if usage2.get("truncated", False) and len(part2) > 500:
        part2 = _maybe_continue_part3(chapter_id, plan, ctx, s, part1, part2, system_p1)

    state.draft_text = _strip_meta_headers(part1.rstrip() + "\n\n" + part2.lstrip())

    if len(state.draft_text) < int(s.chapter_target_chars * 0.55):
        state.draft_text = _expand_short_draft(
            state.draft_text, chapter_id, s, system_p1, plan,
            ctx["style_str"], ctx["emotion_directive"], ctx["plot_anchor"],
        )

    total_chars = len(state.draft_text)
    used_tokens = usage1.get("completion_tokens", 0) + usage2.get("completion_tokens", 0)
    logger.info(f"✍️  [写手] 第{chapter_id}章初稿完成: {total_chars} 字"
                f"  (目标={s.chapter_target_chars}  tokens={used_tokens})")
    log_info(novel_id, f"✍️  [写手] 第{chapter_id}章: {total_chars}字 初稿完成")
    return state


def _build_writer_context(state: NovelState, s, plan) -> dict:
    """Assemble all context variables needed for Part 1 + Part 2 prompts."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id
    world_snapshot = state.world_snapshot

    half_chars = s.chapter_target_chars // 2
    # DeepSeek: ~1.34 chars/token for Chinese. 1.3× headroom prevents truncation.
    writer_max_tokens = min(s.llm_max_tokens, int(half_chars / 1.34 * 1.3))

    style_str = style_mcp.format_for_prompt(novel_id)
    variance_brief = style_mcp.get_variance_brief(novel_id)
    world_rules_brief = world_mcp.format_rules_for_prompt(novel_id)
    foreshadow_hint = foreshadow_mcp.format_for_prompt(novel_id, chapter_id)

    relevant_chars = _filter_characters(state.active_characters, plan.key_characters)
    char_info = _format_character_info(relevant_chars, chapter_id)
    world_background_intro = _build_world_intro(world_snapshot) if chapter_id == 1 else ""
    recent_summary = _format_recent_summaries(state.recent_summaries)
    last_ending = world_snapshot.get("last_chapter_ending", state.memory_snapshot.get("last_ending", ""))

    plot_anchor = _build_plot_anchor_string(novel_id, state.volume_no, chapter_id)
    arc_progression = _format_arc_progression(state)
    semantic_context = _format_semantic_context(state.memory_snapshot.get("semantic_facts", []))
    prop_warning = state.memory_snapshot.get("prop_warning", "")

    scenes = state.memory_snapshot.get("key_scenes", plan.key_scenes or [])
    mid = len(scenes) // 2 + 1
    world_type = world_snapshot.get("world_rules", {}).get("world_type", "玄幻")
    emotion_directive = _build_emotion_directive(state)
    ending_type = state.memory_snapshot.get("ending_type", "悬念")

    style_directive = style_str + (f"\n变化建议：{variance_brief}" if variance_brief else "") + (f"\n⚠️ {prop_warning}" if prop_warning else "")

    position_directive = _build_position_directive(state, novel_id, chapter_id, s)

    # Era anchor from ContextBuilder (anti-setting-drift for urban novels)
    era_anchor = state.memory_snapshot.get("era_anchor", "")

    return dict(
        half_chars=half_chars,
        writer_max_tokens=writer_max_tokens,
        style_str=style_str,
        style_directive=style_directive,
        world_rules_brief=world_rules_brief,
        foreshadow_hint=foreshadow_hint,
        char_info=char_info,
        world_background_intro=world_background_intro,
        recent_summary=recent_summary,
        last_ending=str(last_ending)[:300],
        plot_anchor=plot_anchor,
        arc_progression=arc_progression,
        semantic_context=semantic_context,
        world_type=world_type,
        emotion_directive=emotion_directive,
        ending_type=ending_type,
        half_scenes=scenes[:mid],
        rest_scenes=scenes[mid:],
        foreshadow_ops_str=_format_foreshadow_ops(plan.foreshadow_ops),
        position_directive=position_directive or "",
        era_anchor=era_anchor,
    )


def _generate_part1(chapter_id: int, plan, ctx: dict, s) -> tuple:
    """Generate Part 1 of the chapter. Returns (text, usage, system_prompt) or (None, {}, '')."""
    half_chars = ctx["half_chars"]
    system_p1 = WRITER_SYSTEM.format(
        world_type=ctx["world_type"],
        target_chars=s.chapter_target_chars,
        half_chars=half_chars,
    )
    user_p1 = WRITER_PART1_PROMPT.format(
        chapter_id=chapter_id,
        title=plan.title,
        half_chars=half_chars,
        goal=plan.goal,
        conflict_setup=plan.conflict_setup,
        must_include=", ".join(plan.must_include),
        must_avoid=", ".join(plan.must_avoid),
        character_info=ctx["char_info"],
        world_rules_brief=ctx["world_rules_brief"][:400],
        world_background_intro=ctx["world_background_intro"],
        recent_summary=ctx["recent_summary"],
        last_ending=ctx["last_ending"],
        style_directive=ctx["style_directive"],
        foreshadow_hint=ctx["foreshadow_hint"],
        plot_anchor=ctx["plot_anchor"] + ctx["arc_progression"] + ctx["semantic_context"],
        scene_plan=_format_scenes(ctx["half_scenes"]),
        emotion_directive=ctx["emotion_directive"],
        position_directive=ctx["position_directive"],
        era_anchor=ctx["era_anchor"],
    )
    try:
        part1, usage1 = chat(
            [{"role": "system", "content": system_p1}, {"role": "user", "content": user_p1}],
            max_tokens=ctx["writer_max_tokens"],
        )
        if usage1.get("truncated", False):
            logger.warning(f"⚠️ [写手] 第{chapter_id}章第一段被截断({len(part1)}字/{half_chars}目标)，第二段补偿余量")
        return part1, usage1, system_p1
    except Exception as e:
        logger.error(f"❌ [写手] 第{chapter_id}章 第一段生成失败: {e}")
        return None, {}, ""


def _generate_part2(chapter_id: int, plan, ctx: dict, s, part1: str, usage1: dict, system_p1: str) -> tuple:
    """Generate Part 2. Returns (text, usage) or (None, {}) on failure."""
    half_chars = ctx["half_chars"]
    writer_max_tokens = ctx["writer_max_tokens"]
    part1_truncated = usage1.get("truncated", False)
    part1_ending = part1[-200:] if len(part1) > 200 else part1

    p2_max_tokens = writer_max_tokens
    if part1_truncated:
        chars_short = half_chars - len(part1)
        if chars_short > 200:
            extra_tokens = min(int(chars_short / 1.34 * 1.3), writer_max_tokens)
            p2_max_tokens = min(s.llm_max_tokens, writer_max_tokens + extra_tokens)
            logger.info(f"  ↳ Part 1 短了 {chars_short} 字，Part 2 配额: {p2_max_tokens} tokens")

    user_p2 = WRITER_PART2_PROMPT.format(
        chapter_id=chapter_id,
        half_chars=half_chars,
        plot_anchor=ctx["plot_anchor"],
        part1_ending=part1_ending,
        remaining_scenes=_format_scenes(ctx["rest_scenes"]),
        must_achieve=", ".join(plan.must_include[len(plan.must_include) // 2:]),
        ending_type=ctx["ending_type"],
        foreshadow_ops=ctx["foreshadow_ops_str"],
        style_directive=ctx["style_str"],
        emotion_directive=ctx["emotion_directive"],
        position_directive=ctx["position_directive"],
        era_anchor=ctx["era_anchor"],
    )
    try:
        part2, usage2 = chat(
            [
                {"role": "system", "content": system_p1},
                {"role": "assistant", "content": part1},
                {"role": "user", "content": user_p2},
            ],
            max_tokens=p2_max_tokens,
        )
        return part2, usage2
    except Exception as e:
        logger.error(f"❌ [写手] 第{chapter_id}章 第二段生成失败，使用第一段作为草稿: {e}")
        return None, {}


def _maybe_continue_part3(chapter_id: int, plan, ctx: dict, s, part1: str, part2: str, system_p1: str) -> str:
    """Attempt Part 3 continuation if Part 2 was truncated. Returns updated part2."""
    logger.warning(f"⚠️ [写手] 第{chapter_id}章第二段被截断({len(part2)}字)，续写Part 3")
    part2_ending = part2[-200:] if len(part2) > 200 else part2
    combined_so_far = part1 + "\n\n" + part2
    try:
        part3, _ = chat(
            [
                {"role": "system", "content": WRITER_CONTINUE_SYSTEM.format(
                    world_type=ctx["world_type"],
                    target_chars=s.chapter_target_chars,
                )},
                {"role": "assistant", "content": combined_so_far},
                {"role": "user", "content": WRITER_CONTINUE_PROMPT.format(
                    chapter_id=chapter_id,
                    half_chars=ctx["half_chars"] // 2,
                    last_part_ending=part2_ending,
                    ending_type=ctx["ending_type"],
                    plot_anchor=ctx["plot_anchor"],
                    style_directive=ctx["style_str"],
                    emotion_directive=ctx["emotion_directive"],
                    position_directive=ctx["position_directive"],
                    era_anchor=ctx["era_anchor"],
                )},
            ],
            max_tokens=ctx["writer_max_tokens"] // 2,
        )
        if part3 and len(part3) > 200:
            logger.info(f"✍️  [写手] 第{chapter_id}章续写完成: +{len(part3)} 字")
            return part2.rstrip() + "\n\n" + part3.lstrip()
        logger.warning(f"⚠️ [写手] 第{chapter_id}章续写无输出")
    except Exception as e:
        logger.warning(f"⚠️ [写手] 第{chapter_id}章续写失败: {e}")
    return part2


# ── helpers ─────────────────────────────────────────────

def _filter_characters(all_chars: list[dict], key_chars: list[str]) -> list[dict]:
    """Return characters matching key_chars, or all if key_chars is empty."""
    if not key_chars:
        return all_chars[:6]
    key_chars = [k for k in key_chars if k]
    if not key_chars:
        return all_chars[:6]
    result = []
    key_lower = [k.lower() for k in key_chars]
    for c in all_chars:
        name = c.get("name", "").lower()
        cid = c.get("char_id", "").lower()
        if any(k in name or k in cid or name in k for k in key_lower):
            result.append(c)
    return result or all_chars[:4]


def _format_character_info(chars: list[dict], chapter_id: int = 1) -> str:
    if not chars:
        return "无人物信息"
    # P4: After 30 chapters, trim character profiles to essentials
    # to prevent context bloat — skip full backstory, trim relationships.
    is_early_chapter = chapter_id <= 30
    lines = []
    for c in chars:
        rel = c.get("relationships", {})

        # Format emotion expression
        emo_expr = c.get("emotion_expression", {})
        emo_parts = []
        if emo_expr.get("anger"):
            emo_parts.append(f"愤怒→{emo_expr['anger']}")
        if emo_expr.get("sadness"):
            emo_parts.append(f"悲伤→{emo_expr['sadness']}")
        if emo_expr.get("joy"):
            emo_parts.append(f"喜悦→{emo_expr['joy']}")
        if emo_expr.get("fear"):
            emo_parts.append(f"恐惧→{emo_expr['fear']}")
        if emo_expr.get("contempt"):
            emo_parts.append(f"轻蔑→{emo_expr['contempt']}")
        if emo_expr.get("key_emotion"):
            emo_parts.append(f"常见情绪→{emo_expr['key_emotion']}")
        if emo_expr.get("speech_style"):
            emo_parts.append(f"说话风格：{emo_expr['speech_style']}")
        if emo_expr.get("inner_voice"):
            emo_parts.append(f"内心独白风格：{emo_expr['inner_voice']}")
        catchphrases = emo_expr.get("catchphrases", [])
        if catchphrases:
            emo_parts.append(f"口头禅：{'／'.join(catchphrases)}")
        emo_str = "；".join(emo_parts) if emo_parts else "（未设定）"

        # P4: trim backstory for late chapters to prevent context bloat
        backstory_str = str(c.get('backstory', ''))
        if not is_early_chapter and len(backstory_str) > 60:
            backstory_str = backstory_str[:60] + "…"

        # P4: trim relationships for late chapters
        if is_early_chapter:
            rel_str = "、".join(f"{k}({v})" for k, v in rel.items()) if rel else "无"
        else:
            rel_str = "、".join(rel.keys()) if rel else "无"

        appearance_str = c.get("appearance", "")
        lines.append(
            f"【{c.get('name', c.get('char_id', '?'))}】\n"
            f"  境界：{c.get('power_level', '?')} | 位置：{c.get('location', '?')}\n"
            f"  情绪：{c.get('emotion_state', '?')} | 体态：{c.get('physical_state', '?')}\n"
            f"  性格：{', '.join(c.get('personality', []))}\n"
            + (f"  外貌：{appearance_str}\n" if appearance_str else "")
            + f"  关系：{rel_str}\n"
            f"  情绪表达：{emo_str}\n"
            f"  背景：{backstory_str}"
        )
    return "\n\n".join(lines)


def _format_recent_summaries(summaries: list[dict]) -> str:
    if not summaries:
        return "（无近期摘要）"
    parts = []
    for s in summaries[-3:]:
        parts.append(f"第{s.get('chapter_no','?')}章：{s.get('summary_text','')[:120]}")
    return "\n".join(parts)


def _format_scenes(scenes: list) -> str:
    if not scenes:
        return "（无具体场景安排）"
    lines = []
    for sc in scenes:
        if isinstance(sc, dict):
            technique = sc.get("emotion_technique", "")
            technique_str = f" → 技法：{technique}" if technique else ""
            lines.append(
                f"场景{sc.get('scene_no','?')}: {sc.get('description','')}"
                f" [{sc.get('emotion','')}]{technique_str}"
            )
        else:
            lines.append(str(sc))
    return "\n".join(lines)


def _build_emotion_directive(state: "NovelState") -> str:
    """Build a concrete emotion execution directive for the Writer from arc + narrative info."""
    chapter_arc = state.memory_snapshot.get("chapter_arc", "")
    narrative = state.narrative_directive
    emotion_curve = narrative.emotion_curve if narrative else ""
    director = state.memory_snapshot.get("director_directive", {})
    emotion_target = director.get("emotion_target", "") if isinstance(director, dict) else ""

    arc_str = chapter_arc or emotion_curve or emotion_target or "平稳推进"

    # Map arc phase to technique hint
    arc_phase = state.memory_snapshot.get("arc_phase", "buildup")
    phase_hint = {
        "setup":    "以环境烘托和内心独白为主，奠定情绪基调",
        "buildup":  "以内心独白推动矛盾积累，对话使用潜台词制造张力",
        "climax":   "身体感知+内心独白双重叠加，情绪爆发时用短句和碎片化意识流",
        "cooldown": "情绪由激烈转沉静，用环境细节收尾，留下余韵",
    }.get(str(arc_phase), "内心独白与身体感知交替使用")

    return (
        f"本章情绪弧线：{arc_str}\n"
        f"技法建议：{phase_hint}\n"
        f"关键要求：情绪转折点必须同时使用「身体感知」和「内心独白」，禁止用叙述型句式描写情绪"
    )


def _strip_meta_headers(text: str) -> str:
    """
    Remove markdown headers and meta-labels that LLMs sometimes prepend to novel text.
    Examples of what gets stripped:
      # 第49章《航向欧米茄：暗流涌动》
      ## 前半部分
      ## 后半部分
      ### 正文开始
    Chinese web novel prose never uses # headers, so this is always safe to strip.
    """
    lines = text.split('\n')
    filtered = []
    for line in lines:
        stripped = line.strip()
        # Drop any markdown header line (# ## ### …)
        if re.match(r'^#{1,6}\s+', stripped):
            continue
        # Drop lone meta-label lines like "前半部分" / "后半部分" / "正文开始" / "正文："
        if re.match(r'^(前半部分|后半部分|正文开始|正文：|正文:)\s*$', stripped):
            continue
        filtered.append(line)
    return '\n'.join(filtered).strip()


def _build_world_intro(world_snapshot: dict) -> str:
    """
    For chapter 1 only: format rich world background data so the Writer
    knows what to weave into the opening narrative naturally.
    Returns an empty string if no relevant data is found.
    """
    parts = []

    background = world_snapshot.get("background", "")
    if background:
        parts.append(f"世界背景：{str(background)[:300]}")

    power_system = world_snapshot.get("power_system", {})
    if power_system:
        if isinstance(power_system, dict):
            ps_name = power_system.get("name", "")
            ps_levels = power_system.get("levels", [])
            ps_rules = power_system.get("rules", [])
            if ps_name:
                parts.append(f"力量体系名称：{ps_name}")
            if ps_levels:
                parts.append(f"境界划分（低→高）：{'→'.join(str(l) for l in ps_levels[:6])}")
            if ps_rules:
                parts.append(f"体系核心规则：{'；'.join(str(r) for r in ps_rules[:3])}")
        else:
            parts.append(f"力量体系：{str(power_system)[:200]}")

    protagonist = world_snapshot.get("protagonist", {})
    if isinstance(protagonist, dict) and protagonist:
        name = protagonist.get("name", "")
        bg = protagonist.get("background", "")
        initial_power = protagonist.get("initial_power", protagonist.get("protagonist_current", ""))
        goal = protagonist.get("goal", "")
        intro_parts = [x for x in [name, bg, f"初始境界：{initial_power}" if initial_power else ""] if x]
        if intro_parts:
            parts.append(f"主角起点：{'，'.join(intro_parts)}")
        if goal:
            parts.append(f"主角终极目标：{goal}")

    world_setting = world_snapshot.get("world_rules", {})
    if isinstance(world_setting, dict):
        geography = world_setting.get("geography", "")
        politics = world_setting.get("politics", "")
        if geography:
            parts.append(f"地理概貌：{str(geography)[:150]}")
        if politics:
            parts.append(f"势力格局：{str(politics)[:150]}")

    if not parts:
        return ""

    return (
        "\n【开篇世界建立参考（第1章专用）】\n"
        + "\n".join(parts)
        + "\n写作要求：通过主角视角、场景描写、对话等方式自然带出以上背景，禁止大段旁白式世界介绍\n"
    )


def _format_foreshadow_ops(ops: list[dict]) -> str:
    if not ops:
        return "无伏笔操作"
    parts = []
    for op in ops:
        parts.append(f"• {op.get('op','?')}「{op.get('description', op.get('id','?'))}」")
    return "\n".join(parts)


def _expand_short_draft(
    draft: str,
    chapter_id: int,
    s,
    system_prompt: str,
    plan,
    style_str: str,
    emotion_directive: str,
    plot_anchor: str,
) -> str:
    """Expand a draft that collapsed into summary mode (<55% of target chars)."""
    expansion_needed = s.chapter_target_chars - len(draft)
    if expansion_needed <= 0:
        return draft
    try:
        expansion, _ = chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "assistant", "content": draft},
                {"role": "user", "content": (
                    f"上文场景展开不足（当前{len(draft)}字，目标{s.chapter_target_chars}字），"
                    f"请继续场景化展开约{expansion_needed}字。\n"
                    f"严禁用概述式总结替代场景，必须用具体对话、动作、感官描写展开。\n"
                    f"直接从故事内容继续："
                )},
            ],
            max_tokens=min(s.llm_max_tokens, int(expansion_needed / 1.34 * 1.3) + 300),
        )
        if expansion and len(expansion) > 200:
            result = _strip_meta_headers(draft.rstrip() + "\n\n" + expansion.lstrip())
            logger.info(f"✍️  [写手] 第{chapter_id}章补写: +{len(expansion)}字 → 共{len(result)}字")
            return result
    except Exception as e:
        logger.warning(f"⚠️ [写手] 第{chapter_id}章补写失败: {e}")
    return draft


def _build_position_directive(state: "NovelState", novel_id: str, chapter_id: int, s) -> str:
    """Build chapter-position-specific writing directives.

    Injects extra rules for:
      1. Opening chapters (1-3): hook, pacing, dialogue quality
      2. Final volume chapters: wrap-up trajectory
      3. Final chapter: emotional intensity
    """
    novel = repo.get_novel(novel_id)
    total_volumes = novel.get("total_volumes", 10) if novel else 10
    volume_no = state.volume_no
    directives = []

    # ── Opening chapters 1-3 ──────────────────────────
    if chapter_id <= 3 and chapter_id in OPENING_CHAPTER_DIRECTIVES:
        directives.append(OPENING_CHAPTER_DIRECTIVES[chapter_id])

    # ── Final volume (all chapters in last volume) ────
    if volume_no == total_volumes and total_volumes > 1:
        # Check if this IS the last chapter
        volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}") or {}
        outlines = volume_plan.get("chapter_outlines", [])
        is_last_chapter = bool(outlines and outlines[-1].get("chapter_no", 0) == chapter_id)

        if is_last_chapter:
            # Final chapter: emotional intensity
            directives.append(ENDING_CHAPTER_DIRECTIVE)
        else:
            # Other chapters in last volume: wrap-up momentum
            directives.append(ENDING_VOLUME_DIRECTIVE.format(
                volume_no=volume_no, total_volumes=total_volumes,
            ))

    combined = "\n\n".join(d for d in directives if d)
    return combined


def _build_plot_anchor_string(novel_id: str, volume_no: int, chapter_id: int = 0) -> str:
    """Build a compact plot-direction anchor from story_outline + volume_plan.
    
    This is injected into Writer and Planner prompts to prevent plot drift
    across long-form generation (200+ chapters).
    """
    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}") or {}
    
    parts = []
    
    # Core theme and ending direction
    core_theme = story_outline.get("core_theme", "")
    ending_dir = story_outline.get("ending_direction", "")
    if core_theme:
        parts.append(f"全书核心主题：{core_theme}")
    if ending_dir:
        parts.append(f"结局方向：{ending_dir}")

    # Thematic core anchor
    thematic_core = repo.get_world_memory(novel_id, "thematic_core") or {}
    central_q = thematic_core.get("central_question", "")
    contract = thematic_core.get("emotional_contract", "")
    if central_q:
        parts.append(f"全书核心命题：{central_q}")
    if contract:
        parts.append(f"情感约定：{contract}")
    
    # Volume goal
    vol_goal = volume_plan.get("volume_goal", "")
    if vol_goal:
        parts.append(f"本卷目标：{vol_goal}")
    
    # Current act
    act_structure = story_outline.get("act_structure", [])
    current_act = next(
        (a for a in act_structure if _vol_in_act(volume_no, a.get("volumes", ""))),
        {},
    )
    act_name = current_act.get("name", "")
    act_goal = current_act.get("goal", "")
    if act_name:
        parts.append(f"当前幕：{act_name}")
    if act_goal:
        parts.append(f"幕目标：{act_goal}")
    
    # Power milestones for this volume range
    power_milestones = story_outline.get("power_milestones", [])
    remaining_power = [m for m in power_milestones if m.get("volume", 999) >= volume_no]
    if remaining_power:
        power_strs = []
        for m in remaining_power[:3]:
            ev = m.get("event", "")
            lv = m.get("level", "")
            power_strs.append(f"第{m.get('volume','?')}卷={lv}({ev[:30]})")
        parts.append(f"后续力量突破：{' → '.join(power_strs)}")
    
    # Forbidden content reminder
    forbidden = story_outline.get("forbidden_content", [])
    vol_forbidden = volume_plan.get("forbidden_this_volume", [])
    all_forbidden = list(dict.fromkeys(forbidden + vol_forbidden))[:5]
    if all_forbidden:
        parts.append(f"全局禁止内容：{'；'.join(all_forbidden)}")
    
    # Chapter-specific mandatory key events (injected at top for prominence)
    chapter_outlines = volume_plan.get("chapter_outlines", [])
    if chapter_id > 0:
        my_outline = next(
            (c for c in chapter_outlines if c.get("chapter_no") == chapter_id), {}
        )
        if my_outline:
            key_event = my_outline.get("key_event", "")
            conflict = my_outline.get("conflict", "")
            goal = my_outline.get("goal", "")
            if key_event:
                parts.insert(0, f"⚠️【强制执行】本章必须写出的关键事件：{key_event}")
            if conflict:
                parts.append(f"⚠️【强制执行】本章必须体现的冲突：{conflict}")
            if goal:
                parts.append(f"本章目标：{goal}")

    if not parts:
        return ""

    return (
        "【全书剧情锚点（严格遵循）】\n"
        + "\n".join(parts)
        + "\n"
    )


def _format_arc_progression(state: NovelState) -> str:
    """Format compacted arc summaries for long-range context.
    
    Shows the story's arc progression across all previous chapters
    so the Writer never loses track of where the story has been.
    """
    # Arc summaries are pre-loaded by context_builder into memory_snapshot
    arc_keys = sorted(k for k in state.memory_snapshot.keys() if k.startswith("arc_summary_"))
    if not arc_keys:
        return ""
    
    # Load arc summaries that may be stored directly in memory_snapshot
    arcs = []
    for key in arc_keys[-3:]:  # last 3 arcs
        data = state.memory_snapshot.get(key, {})
        if isinstance(data, dict):
            summary = data.get("arc_summary", "")[:150]
            if summary:
                arcs.append(summary)
    
    if not arcs:
        return ""
    
    return "【故事弧线进展摘要】\n" + "\n---\n".join(arcs) + "\n"


def _vol_in_act(vol_no: int, vol_range: str) -> bool:
    """Check if a volume number falls within a volume range string like '第1-2卷'."""
    import re
    try:
        nums = re.findall(r"\d+", str(vol_range))
        if len(nums) >= 2:
            return int(nums[0]) <= vol_no <= int(nums[1])
        elif len(nums) == 1:
            return vol_no == int(nums[0])
    except Exception:
        pass
    return False


def _format_semantic_context(facts: list[dict]) -> str:
    """Format semantically relevant facts for Writer context.
    
    These facts come from vector search and may span across the entire novel,
    not just the last N chapters. This gives the Writer long-range memory.
    """
    if not facts:
        return ""
    lines = []
    for f in facts[:8]:  # limit to avoid token bloat
        cn = f.get("chapter_no", "?")
        score = f.get("score", 0)
        ft = f.get("fact_type", "?")
        txt = f.get("fact_text", "")[:120]
        lines.append(f"  [ch{cn}] {txt}")
    if not lines:
        return ""
    return (
        "\n【语义相关事实（来自全本检索）】\n"
        + "\n".join(lines)
        + "\n"
    )
