"""
agents/checker.py — Checker Agent (强约束校验器)

Validates:
  1. Character consistency (power level, location, status)
  2. World rule compliance
  3. Timeline integrity
  4. Foreshadow requirements (due foreshadows handled)
  5. Must-include elements present
  6. Must-avoid elements absent

Uses the strong reasoning model for accuracy.
"""
import json
from loguru import logger
from db.novel_log import log_info
from langgraph_engine.state import NovelState, ChapterIssue, IssueSeverity
from llm import chat_strong, extract_json
from mcp import world_mcp, foreshadow_mcp
from config.prompts import CHECKER_SYSTEM, CHECKER_PROMPT
from db import repo
from agents.base import guard_error


def run(state: NovelState) -> NovelState:
    """LangGraph node: Checker validates chapter content."""
    if guard_error(state, "检查"):
        return state

    novel_id = state.novel_id
    chapter_id = state.chapter_id
    text_to_check = state.edited_text or state.draft_text

    if not text_to_check:
        logger.warning(f"⚠️  [校验] 第{chapter_id}章无文本，跳过校验")
        return state

    stub_issue = _check_text_length(state, text_to_check)
    if stub_issue:
        state.issues = [stub_issue]
        return state

    inputs = _build_check_inputs(state, text_to_check)
    result = _call_checker_llm(chapter_id, inputs)
    state.issues = _parse_llm_issues(result)

    state.issues.extend(_check_outline_compliance(state))
    state.issues.extend(_check_foreshadow_resolution_quality(state, text_to_check))
    if _is_ending_chapter(state):
        state.issues.extend(_check_ending_quality(state, text_to_check))
    else:
        state.issues.extend(_check_last_volume_quality(state, text_to_check))
    state.issues.extend(_check_opening_quality(state, text_to_check, chapter_id))
    state.issues.extend(_check_character_arc_stage(state, text_to_check))
    state.issues.extend(_check_setting_drift(state, text_to_check))

    high = sum(1 for i in state.issues if i.severity == IssueSeverity.HIGH)
    medium = sum(1 for i in state.issues if i.severity == IssueSeverity.MEDIUM)
    passed = result.get("passed", not bool(high))
    result_str = "通过✅" if passed else "未通过❌"
    logger.info(
        f"🔍 [校验] 第{chapter_id}章 第{state.retry_count}轮: {result_str}"
        f"  严重={high}  中等={medium}"
        f"  | {result.get('summary', '无摘要')}"
    )
    log_info(novel_id, f"🔍 [校验] 第{chapter_id}章 {result_str} ({high}严重/{medium}中等)")
    return state


def _check_text_length(state: NovelState, text: str):
    """Return a HIGH ChapterIssue if text is too short, else None."""
    from config import get_settings as _gs
    _s = _gs()
    _min_chars = int(_s.chapter_target_chars * 0.5)
    if len(text) >= _min_chars:
        return None
    logger.warning(
        f"⚠️ [校验] 第{state.chapter_id}章文本过短 ({len(text)}字 < {_min_chars})，标记HIGH问题跳过LLM"
    )
    return ChapterIssue(
        code="TEXT_TOO_SHORT",
        description=(
            f"章节内容仅{len(text)}字，目标{_s.chapter_target_chars}字。"
            f"疑似概述式生成（非场景化展开），需补写扩展。"
        ),
        severity=IssueSeverity.HIGH,
    )


def _build_check_inputs(state: NovelState, text: str) -> dict:
    """Assemble all inputs needed for the Checker LLM prompt."""
    novel_id = state.novel_id
    chapter_id = state.chapter_id
    plan = state.chapter_plan
    sem_facts = state.memory_snapshot.get("semantic_facts", [])
    return dict(
        chapter_text=text[:6000],
        char_profiles=_format_char_profiles(state.active_characters[:8]),
        world_rules_str=world_mcp.format_rules_for_prompt(novel_id),
        timeline_rule=world_mcp.get_rule(novel_id, "timeline_rule", "strict_increasing"),
        recent_facts=_format_semantic_facts(sem_facts) if sem_facts else _format_recent_facts(novel_id, chapter_id),
        foreshadow_state=foreshadow_mcp.format_for_prompt(novel_id, chapter_id),
        plot_direction=_build_plot_direction_check(novel_id, state.volume_no),
        must_include=", ".join(plan.must_include) if plan else "",
        must_avoid=", ".join(plan.must_avoid) if plan else "",
        chapter_id=chapter_id,
    )


def _call_checker_llm(chapter_id: int, inputs: dict) -> dict:
    """Call the strong LLM for validation. Returns parsed result dict."""
    try:
        content, _ = chat_strong(
            [
                {"role": "system", "content": CHECKER_SYSTEM},
                {"role": "user", "content": CHECKER_PROMPT.format(
                    chapter_text=inputs["chapter_text"],
                    character_profiles=inputs["char_profiles"],
                    world_rules=inputs["world_rules_str"],
                    timeline_rule=inputs["timeline_rule"],
                    chapter_id=inputs["chapter_id"],
                    recent_facts=inputs["recent_facts"],
                    foreshadow_state=inputs["foreshadow_state"],
                    must_include=inputs["must_include"],
                    must_avoid=inputs["must_avoid"],
                    plot_direction=inputs["plot_direction"],
                )},
            ],
            max_tokens=8192,
        )
        return extract_json(content) or {}
    except Exception as e:
        logger.error(f"❌ [校验] 第{chapter_id}章 LLM 调用失败: {e}")
        return {"issues": [{"code": "CHECKER_UNAVAILABLE", "description": str(e), "severity": "low"}]}


def _parse_llm_issues(result: dict) -> list[ChapterIssue]:
    """Convert raw LLM result dict into ChapterIssue list."""
    issues = []
    for raw in result.get("issues", []):
        try:
            sev = IssueSeverity(raw.get("severity", "low"))
        except ValueError:
            sev = IssueSeverity.LOW
        issues.append(ChapterIssue(
            code=raw.get("code", "UNKNOWN"),
            description=raw.get("description", ""),
            severity=sev,
            location=raw.get("location"),
        ))
    return issues


# ── helpers ──────────────────────────────────────────────────────

def _format_char_profiles(chars: list[dict]) -> str:
    if not chars:
        return "无人物档案"
    lines = []
    for c in chars:
        lines.append(
            f"{c.get('name', c.get('char_id', '?'))}: "
            f"境界={c.get('power_level', '?')}, "
            f"位置={c.get('location', '?')}, "
            f"状态={c.get('status', 'alive')}, "
            f"情绪={c.get('emotion_state', '?')}"
        )
    return "\n".join(lines)


def _format_recent_facts(novel_id: str, chapter_id: int) -> str:
    facts = repo.get_recent_facts(novel_id, max(1, chapter_id - 3), limit=20)
    if not facts:
        return "无近期事实"
    return "\n".join(f"• [{f['fact_type']}] {f['fact_text']}" for f in facts[:15])


def _format_semantic_facts(facts: list[dict]) -> str:
    if not facts:
        return "（无语义相关事实）"
    lines = []
    for f in facts:
        score = f.get("score", 0)
        cn = f.get("chapter_no", "?")
        ft = f.get("fact_type", "?")
        txt = f.get("fact_text", "")[:150]
        lines.append(f"• (ch{cn}@{score:.2f} [{ft}]) {txt}")
    return "\n".join(lines)


def _build_plot_direction_check(novel_id: str, volume_no: int) -> str:
    """Build a direction-check section for the Checker prompt.
    
    Injects the original story outline and volume plan so the Checker
    can validate that the generated chapter stays on track.
    """
    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{volume_no}") or {}
    
    parts = []
    
    core_theme = story_outline.get("core_theme", "")
    ending_dir = story_outline.get("ending_direction", "")
    if core_theme:
        parts.append(f"- 核心主题：{core_theme}")
    if ending_dir:
        parts.append(f"- 结局方向：{ending_dir}")
    
    vol_goal = volume_plan.get("volume_goal", "")
    if vol_goal:
        parts.append(f"- 本卷核心目标：{vol_goal}")
    
    # Thematic core
    thematic_core = repo.get_world_memory(novel_id, "thematic_core") or {}
    central_q = thematic_core.get("central_question", "")
    anti_themes = thematic_core.get("anti_themes", [])
    if central_q:
        parts.append(f"- 全书核心命题：{central_q}")
    if anti_themes:
        parts.append(f"- 主题禁区（必须避免）：{'；'.join(anti_themes[:3])}")

    # Forbidden content
    forbidden = story_outline.get("forbidden_content", [])
    if forbidden:
        parts.append(f"- 全局禁止内容：{'；'.join(forbidden[:3])}")
    
    # World type check
    world_type = story_outline.get("world_type", "")
    if world_type:
        parts.append(f"- 故事类型（必须严格匹配）：{world_type}")
    
    if not parts:
        return "（无情节方向参考数据）"
    
    check_prompt = (
        "请额外检查【情节方向】维度：\n"
        + "\n".join(parts)
        + (
            "\n\n检查问题：\n"
            "1. 本章是否与全书核心主题和类型一致？\n"
            "2. 冲突类型是否匹配故事类型？（末世制造文不应出现魔法种田情节）\n"
            "3. 本章是否在推进本卷目标？\n"
            "4. 是否引入了明显偏离故事类型的元素？（如末世文中出现修仙/魔法/外星飞船等）\n"
            "\n如果存在明显的情节方向偏离，报告 PLOT_DRIFT high-severity issue。"
        )
    )
    return check_prompt


def _check_foreshadow_resolution_quality(state: NovelState, text: str) -> list:
    """
    Check whether foreshadows the planner explicitly scheduled to resolve this chapter
    receive substantive emotional treatment in the text.

    Uses chapter_plan.foreshadow_ops (op=resolve) rather than get_due(), which spans
    many chapters and would produce false positives on every chapter in a closing arc.
    Matching is keyword-based (entities extracted from description), not exact-text,
    because description text is meta-notes, not prose.
    """
    if not state.chapter_plan or not state.chapter_plan.foreshadow_ops:
        return []

    # Only check foreshadows explicitly scheduled for resolution this chapter
    resolve_ids = {
        op.get("id", "")
        for op in state.chapter_plan.foreshadow_ops
        if op.get("op") == "resolve" and op.get("id")
    }
    if not resolve_ids:
        return []

    novel_id = state.novel_id
    due = foreshadow_mcp.get_due(novel_id)
    # Map id -> foreshadow record
    due_map = {fs.get("fshadow_id", ""): fs for fs in due}

    EMOTION_KEYWORDS = [
        "心", "情", "泪", "感", "痛", "喜", "悲", "愤", "震", "惊",
        "颤抖", "沉默", "哽咽", "愣住", "凝视", "释然", "颤栗",
    ]

    # Chinese words ≥2 chars that are likely proper nouns / key objects in the desc
    import re as _re
    _CHINESE_WORD_RE = _re.compile(r'[一-鿿]{2,6}')

    issues = []
    for fid in resolve_ids:
        fs = due_map.get(fid)
        if not fs:
            continue
        desc = fs.get("description", "")
        if not desc or len(desc) < 4:
            continue

        # Extract candidate anchor words from description (nouns / proper nouns)
        anchors = _CHINESE_WORD_RE.findall(desc)
        # Prefer longer, more specific anchors; drop common stop-words
        _STOPWORDS = {"主角", "登场", "出现", "发现", "提到", "将会", "可能", "一个", "这个"}
        anchors = [a for a in anchors if a not in _STOPWORDS]
        anchors.sort(key=len, reverse=True)
        anchors = anchors[:5]

        if not anchors:
            continue

        # Find where any anchor appears in the chapter text
        found_at = -1
        for anchor in anchors:
            idx = text.find(anchor)
            if idx >= 0:
                found_at = idx
                break

        if found_at < 0:
            issues.append(ChapterIssue(
                code="FORESHADOW_RESOLUTION_ABSENT",
                description=f"计划解决的伏笔「{desc[:30]}」在文中未见相关描写",
                severity=IssueSeverity.LOW,
            ))
            continue

        # Verify emotional weight in ±300 char window around the anchor
        ctx_start = max(0, found_at - 300)
        ctx_end = min(len(text), found_at + 300)
        context = text[ctx_start:ctx_end]
        has_emotion = any(kw in context for kw in EMOTION_KEYWORDS)

        if not has_emotion:
            issues.append(ChapterIssue(
                code="FORESHADOW_RESOLUTION_WEAK",
                description=(
                    f"计划解决的伏笔「{desc[:30]}」有所提及，"
                    f"但前后300字缺少情绪共鸣，解决质量偏低"
                ),
                severity=IssueSeverity.MEDIUM,
            ))

    return issues


def _check_outline_compliance(state: NovelState) -> list:
    """Post-generation check: compare generated chapter against volume plan outline.
    
    Returns a list of ChapterIssue if the generated chapter fails to include
    key elements specified in the volume plan's chapter outline.
    """
    from langgraph_engine.state import ChapterIssue, IssueSeverity
    
    novel_id = state.novel_id
    chapter_id = state.chapter_id
    text = state.edited_text or state.draft_text or ""
    
    if not text:
        return []
    
    volume_plan = repo.get_world_memory(novel_id, f"volume_plan_{state.volume_no}") or {}
    outlines = volume_plan.get("chapter_outlines", [])
    my_outline = next((c for c in outlines if c.get("chapter_no") == chapter_id), {})
    
    if not my_outline:
        return []
    
    issues = []
    
    # Check 1: key_event appears in text
    key_event = my_outline.get("key_event", "")
    if key_event and len(key_event) > 8:
        # Check if any significant part of the key_event appears
        key_parts = [p for p in key_event.split("，")[:3] if len(p) > 4]
        if key_parts and not any(kp in text for kp in key_parts):
            issues.append(ChapterIssue(
                code="OUTLINE_KEY_EVENT_MISSING",
                description=f"卷大纲要求的关键事件「{key_event[:50]}」在文中未出现",
                severity=IssueSeverity.HIGH,
            ))
    
    # Check 2: conflict from outline appears
    conflict = my_outline.get("conflict", "")
    if conflict and len(conflict) > 8:
        conflict_parts = [p for p in conflict.split("，")[:2] if len(p) > 4]
        if conflict_parts and not any(cp in text for cp in conflict_parts):
            issues.append(ChapterIssue(
                code="OUTLINE_CONFLICT_MISSING",
                description=f"大纲要求的冲突「{conflict[:50]}」未在文中体现",
                severity=IssueSeverity.MEDIUM,
            ))
    
    # Check 3: core event phrase presence with looser matching
    goal_check = my_outline.get("goal", "")
    if goal_check and len(goal_check) > 6:
        goal_keywords = [w for w in goal_check.split("，") if len(w) > 3][:2]
        if goal_keywords and not any(kw in text for kw in goal_keywords):
            issues.append(ChapterIssue(
                code="OUTLINE_GOAL_NOT_ACHIEVED",
                description=f"大纲目标「{goal_check[:40]}」可能未达成",
                severity=IssueSeverity.LOW,
            ))
    
    return issues


def _check_character_arc_stage(state: NovelState, text: str) -> list:
    chars = state.active_characters
    protagonist = next((c for c in chars if c.get("extra", {}).get("goal")), None)
    if not protagonist:
        return []
    arc_stage = protagonist.get("extra", {}).get("arc_stage", "初心")
    if arc_stage != "蜕变":
        return []
    naive_keywords = ["放弃了", "认输了", "果然我不行", "我太弱了，不该", "向命运低头"]
    found = [kw for kw in naive_keywords if kw in text]
    if found:
        return [ChapterIssue(
            code="CHARACTER_ARC_REGRESSION",
            description=f"主角处于「蜕变」阶段，但文中出现了「初心」阶段的认怂行为：{'、'.join(found[:2])}",
            severity=IssueSeverity.MEDIUM,
        )]
    return []


# ── Ending chapter quality gate ────────────────────────────

_ENDING_EMOTION_KEYWORDS = {
    "acceptance": ["释然", "放下", "接受", "平静", "不再", "原谅", "和解"],
    "closure":    ["最后", "结局", "终点", "句号", "结束", "完成", "收尾", "告别"],
    "reflection": ["回想", "回首", "曾经", "过去", "一路", "历程", "经历", "回忆"],
}

_ENDING_POSITIVE_KEYWORDS = [
    "希望", "未来", "重建", "新生", "明天", "光明", "向前", "继续",
    "新生活", "新世界", "复苏", "黎明",
]


def _is_ending_chapter(state: NovelState) -> bool:
    """Check if this chapter is the last chapter of the last volume."""
    if state.error:
        return False
    novel = repo.get_novel(state.novel_id)
    if not novel:
        return False
    total_volumes = novel.get("total_volumes", 10)
    if state.volume_no != total_volumes:
        return False
    volume_plan = repo.get_world_memory(state.novel_id, f"volume_plan_{state.volume_no}")
    outlines = (volume_plan or {}).get("chapter_outlines", [])
    if not outlines:
        return False
    last_chapter_no = outlines[-1].get("chapter_no", 0)
    return state.chapter_id == last_chapter_no


def _check_ending_quality(state: NovelState, text: str) -> list:
    """
    Ending-specific quality checks for the final chapter.

    Dimensions checked:
      1. Emotional beats — acceptance, closure, reflection
      2. Content density — word count >= 4000
      3. Positive outlook — at least 2 forward-looking statements
      4. Core foreshadows — all core foreshadows must be resolved
    """
    issues = []

    # ── 1. Emotional beats ─────────────────────────────
    for beat, keywords in _ENDING_EMOTION_KEYWORDS.items():
        if not any(kw in text for kw in keywords):
            issues.append(ChapterIssue(
                code="ENDING_EMOTION_MISSING",
                description=f"结尾缺少「{beat}」情感节拍（期望关键词: {'/'.join(keywords[:3])}）",
                severity=IssueSeverity.MEDIUM,
            ))

    # ── 2. Content density ─────────────────────────────
    wc = len(text)
    if wc < 4000:
        issues.append(ChapterIssue(
            code="ENDING_TOO_SHORT",
            description=f"结尾章节仅{wc}字（<4000），内容密度不足，场景未充分展开",
            severity=IssueSeverity.HIGH,
        ))

    # ── 3. Positive outlook (genre-aware: only for apocalyptic/rebirth novels) ──
    world_type = (repo.get_world_memory(state.novel_id, "story_outline") or {}).get("world_type", "")
    _HOPE_GENRES = {"末世", "重生", "穿越", "现代"}
    needs_hope_check = any(g in world_type for g in _HOPE_GENRES)
    positive_count = sum(1 for kw in _ENDING_POSITIVE_KEYWORDS if kw in text)
    if needs_hope_check and positive_count < 2:
        issues.append(ChapterIssue(
            code="ENDING_LACKS_HOPE",
            description=f"结尾缺少希望感（仅{positive_count}个正向词汇，期望 >=2），"
                        f"{world_type}类小说结局需要给读者以暖意和展望",
            severity=IssueSeverity.MEDIUM,
        ))

    # ── 4. Core foreshadows resolved ───────────────────
    unresolved = foreshadow_mcp.get_all_unresolved(state.novel_id)
    core_unresolved = [f for f in unresolved if f.get("importance") == "core"]
    if core_unresolved:
        issues.append(ChapterIssue(
            code="ENDING_FORESHADOW_UNRESOLVED",
            description=f"结尾仍有 {len(core_unresolved)} 个核心伏笔未解决: "
                        + "; ".join(f.get("description", "?")[:30] for f in core_unresolved[:3]),
            severity=IssueSeverity.HIGH,
        ))

    return issues


def _check_opening_quality(state: NovelState, text: str, chapter_id: int) -> list:
    """Opening chapter quality checks for chapters 1-3.

    Dimensions:
      1. Opening 200 chars must contain a concrete person + event + conflict
      2. Chapter ending must have a hook/suspense
    """
    issues = []
    if chapter_id > 3:
        return issues

    # ── 1. Opening 200 chars ───────────────────────────
    opening = text[:200]
    has_action_marker = any(
        m in opening for m in
        ("！", "?", "？", "!", "喊", "吼", "冲", "拍", "踢", "骂",
         "摔", "砸", "撞", "叫", "操", "靠", "滚", "打")
    )
    if not has_action_marker:
        issues.append(ChapterIssue(
            code="OPENING_NO_CONFLICT",
            description=(
                f"第{chapter_id}章开篇200字未出现具体冲突标记，"
                "可能以背景描述或平铺叙述开头，前200字必须出具体的人+事+冲突"
            ),
            severity=IssueSeverity.MEDIUM,
        ))

    # ── 2. Chapter ending hook (ch1-2 must have hook, ch3 is climax) ──
    if chapter_id <= 2:
        ending = text[-300:]
        hook_markers = ("？", "?", "!", "！", "突然", "怎么回事",
                        "什么", "难道", "不会", "完了", "糟了",
                        "轰", "砰", "咔", "ung")
        has_hook = any(m in ending for m in hook_markers)
        if not has_hook:
            issues.append(ChapterIssue(
                code="CHAPTER_NO_HOOK",
                description=(
                    f"第{chapter_id}章节尾未检测到明显的悬疑钩子，"
                    "章末必须有让读者想点下一章的悬念或转折"
                ),
                severity=IssueSeverity.MEDIUM,
            ))

    return issues


def _check_last_volume_quality(state: NovelState, text: str) -> list:
    """Quality checks for chapters in the final volume (not the final chapter).

    Checks:
      1. Foreshadow resolution progress
      2. Chapter drives toward conclusion
    """
    issues = []
    novel = repo.get_novel(state.novel_id)
    if not novel:
        return issues
    total_volumes = novel.get("total_volumes", 10)
    if state.volume_no != total_volumes or total_volumes <= 1:
        return issues

    # ── 1. Foreshadow resolution progress ──────────────
    unresolved = foreshadow_mcp.get_all_unresolved(state.novel_id)
    if unresolved:
        issues.append(ChapterIssue(
            code="LAST_VOLUME_FORESHAW_LINGERING",
            description=(
                f"最后一卷仍有 {len(unresolved)} 个未解决伏笔"
            ),
            severity=IssueSeverity.LOW,
        ))

    return issues


# ── Setting drift detection ─────────────────────────────

_SETTING_DRIFT_APOCALYPSE_KEYWORDS = [
    "废墟", "残骸", "废塔", "熔渣", "竖井", "冷却管道", "废弃管道",
    "清剿网", "守墓人", "追踪协议", "红外扫描", "电磁频段",
    "冷凝液", "散热鳍", "烧蚀纹路", "脊髓直连", "备用接口",
    "后颈接口", "神经束", "冷源", "皮下脉络", "银灰色脉络",
    "地下掩体", "报废服务器", "塌陷", "断裂的钢筋",
]

_SETTING_DRIFT_URBAN_KEYWORDS = [
    "城中村", "握手楼", "出租屋", "房东", "催租", "房租",
    "地铁", "公交", "网吧", "报刊亭", "路边摊", "办公室",
    "工资", "社保", "银行卡", "短信", "手机屏幕", "电脑屏幕",
    "外卖", "淘宝", "微信", "QQ", "支付宝",
]


def _check_setting_drift(state: NovelState, text: str) -> list:
    """Detect if a 都市/重生/现代 novel has drifted into post-apocalyptic/末世 setting.

    Rule-based pre-check: scans for disproportionately high counts of 『末世』
    keywords vs 『都市』keywords. Only activates when the novel type is urban/rebirth.
    """
    try:
        novel = repo.get_novel(state.novel_id)
        if not novel:
            return []
        world_type = novel.get("world_type", "")
        desc = novel.get("description", "")
        story_type = world_type + " " + desc[:300]
        urban_signals = ["都市", "重生", "现代", "校园", "现实"]
        if not any(s in story_type for s in urban_signals):
            return []
    except Exception:
        return []

    if state.chapter_id < 2:
        return []

    apocalypse_count = sum(1 for kw in _SETTING_DRIFT_APOCALYPSE_KEYWORDS if kw in text)
    urban_count = sum(1 for kw in _SETTING_DRIFT_URBAN_KEYWORDS if kw in text)

    if apocalypse_count >= 8 and urban_count <= 1:
        return [ChapterIssue(
            code="SETTING_DRIFT",
            description=(
                f"场景设定漂移检测：文中出现{apocalypse_count}个末世/废墟类关键词"
                f"（如废墟/清剿网/熔渣/冷凝液等），"
                f"但仅{urban_count}个都市类关键词（如城中村/网吧/地铁等）。"
                f"本故事类型为「{world_type}」，场景已严重偏离设定！此问题需修复师修正。"
            ),
            severity=IssueSeverity.HIGH,
        )]

    if apocalypse_count >= 5 and urban_count <= 3:
        return [ChapterIssue(
            code="SETTING_DRIFT",
            description=(
                f"场景设定有漂移风险：末世类关键词{apocalypse_count}个 vs 都市类{urban_count}个。"
                f"故事类型为都市类，但末世/废墟类描写已占主导。"
            ),
            severity=IssueSeverity.MEDIUM,
        )]

    strong_drift_kw = ["清剿网", "守墓人", "熔渣浮带", "冷却管道", "脊髓直连"]
    strong_hits = [kw for kw in strong_drift_kw if kw in text]
    if strong_hits:
        return [ChapterIssue(
            code="SETTING_DRIFT",
            description=(
                f"场景设定可能漂移：文中出现了都市类故事不应有的术语——"
                f"{'、'.join(strong_hits)}。"
                f"请确保场景在「{world_type}」设定下发展，不要切换到末世/废土场景。"
            ),
            severity=IssueSeverity.MEDIUM,
        )]

    return []
