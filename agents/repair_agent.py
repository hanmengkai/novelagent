"""
agents/repair_agent.py — Repair Agent (外科修复器)

Only outputs JSON Patch operations.
Applies minimal surgical fixes to resolve checker issues.
Does NOT rewrite entire sections.
"""
import difflib
from loguru import logger
from langgraph_engine.state import NovelState, ChapterIssue, IssueSeverity
from llm import simple_chat_json
from mcp import world_mcp
from config.prompts import REPAIR_SYSTEM, REPAIR_PROMPT
from agents.base import guard_error


def run(state: NovelState) -> NovelState:
    """LangGraph node: RepairAgent applies surgical fixes."""
    if guard_error(state, "修复"):
        return state

    novel_id = state.novel_id
    chapter_id = state.chapter_id

    text_to_repair = state.edited_text or state.draft_text
    if not text_to_repair:
        return state

    # Focus on HIGH + MEDIUM issues only
    issues_to_fix = [i for i in state.issues
                     if i.severity in (IssueSeverity.HIGH, IssueSeverity.MEDIUM)]
    if not issues_to_fix:
        logger.info(f"✅ [修复] 第{chapter_id}章无需修复，跳过")
        return state

    # TEXT_TOO_SHORT: JSON patching cannot add thousands of chars.
    # Delegate back to the writer's expansion logic instead.
    if any(i.code == "TEXT_TOO_SHORT" for i in issues_to_fix):
        return _repair_by_expansion(state)

    issues_str = _format_issues(issues_to_fix)
    world_rules_brief = world_mcp.format_rules_for_prompt(novel_id)

    # Build character constraints (don't change these)
    cannot_change = _build_cannot_change(state)

    result = simple_chat_json(
        system_prompt=REPAIR_SYSTEM,
        user_prompt=REPAIR_PROMPT.format(
            chapter_text=text_to_repair[:5000],
            issues=issues_str,
            character_constraints=_format_char_constraints(state.active_characters),
            world_rules_brief=world_rules_brief[:300],
            cannot_change=cannot_change,
        ),
        fallback={"patches": []},
    )

    patches = result.get("patches", [])
    if not patches:
        logger.warning(f"⚠️  [修复] 第{chapter_id}章修复指令为空，无法修复")
        return state

    # Apply patches to text
    repaired = _apply_patches(text_to_repair, patches)

    state.edited_text = repaired
    state.repair_patches.extend(patches)
    state.issues = []  # Clear issues to let Checker re-evaluate
    state.retry_count += 1  # Count actual repair attempts (not checker invocations)

    logger.info(f"🔧 [修复] 第{chapter_id}章应用了 {len(patches)} 处修复")
    return state


def _apply_patches(text: str, patches: list[dict]) -> str:
    """Apply JSON patches to text via snippet replacement."""
    result = text
    for patch in patches:
        op = patch.get("op", "modify")
        original_snippet = patch.get("original_snippet", "")
        replacement = patch.get("replacement", "")

        if op in ("modify", "replace") and original_snippet:
            if original_snippet in result:
                result = result.replace(original_snippet, replacement, 1)
                logger.debug(f"  ✓ 精确修复: '{original_snippet[:30]}' → '{replacement[:30]}'")
            else:
                match = _fuzzy_find(result, original_snippet)
                if match:
                    start, end = match
                    result = result[:start] + replacement + result[end:]
                    logger.debug(f"  ✓ 模糊修复: '{original_snippet[:30]}' → '{replacement[:30]}'")
                else:
                    logger.warning(f"  ✗ 未找到修复片段: '{original_snippet[:40]}'")
        elif op == "insert":
            target = patch.get("target", "")
            if target and target in result:
                result = result.replace(target, target + "\n" + replacement, 1)

    return result


def _fuzzy_find(text: str, snippet: str, threshold: float = 0.85) -> tuple[int, int] | None:
    """Multi-window sliding fuzzy search. Returns (start, end) or None.

    Tries windows of ±5 chars around snippet length to handle LLM typos that
    shift the snippet length (e.g. 'rooom' vs 'room'). Step=1 for precision;
    snippets are short so the total comparisons are < 10k and run in < 50ms.
    """
    if len(snippet) < 10:
        return None
    slen = len(snippet)
    best_ratio = 0.0
    best_pos: tuple[int, int] | None = None
    for window in range(max(10, slen - 5), slen + 6):
        for i in range(max(1, len(text) - window + 1)):
            candidate = text[i:i + window]
            ratio = difflib.SequenceMatcher(None, snippet, candidate, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_pos = (i, i + window)
    if best_ratio >= threshold and best_pos is not None:
        return best_pos
    return None


def _repair_by_expansion(state: NovelState) -> NovelState:
    """Handle TEXT_TOO_SHORT by re-invoking the writer's expansion logic.

    repair_agent's JSON-patch approach can't add thousands of characters.
    This delegates to _expand_short_draft from writer.py, which does a
    proper continuation call with the original system prompt.
    """
    from agents.writer import _expand_short_draft, _strip_meta_headers
    from config import get_settings
    from mcp import style_mcp
    from config.prompts import WRITER_SYSTEM

    novel_id = state.novel_id
    chapter_id = state.chapter_id
    s = get_settings()

    text = state.edited_text or state.draft_text
    world_snapshot = state.world_snapshot
    world_type = world_snapshot.get("world_rules", {}).get("world_type", "末世") if world_snapshot else "末世"
    style_str = style_mcp.format_for_prompt(novel_id)
    half_chars = s.chapter_target_chars // 2

    system_prompt = WRITER_SYSTEM.format(
        world_type=world_type,
        target_chars=s.chapter_target_chars,
        half_chars=half_chars,
    )
    plan = state.chapter_plan
    emotion_directive = ""  # expansion pass doesn't need full emotion context

    expanded = _expand_short_draft(
        draft=text,
        chapter_id=chapter_id,
        s=s,
        system_prompt=system_prompt,
        plan=plan,
        style_str=style_str,
        emotion_directive=emotion_directive,
        plot_anchor="",
    )

    if len(expanded) > len(text):
        logger.info(
            f"🔧 [修复] 第{chapter_id}章 TEXT_TOO_SHORT 补写: "
            f"{len(text)}字 → {len(expanded)}字"
        )
        state.edited_text = expanded
        state.issues = []
        state.retry_count += 1
    else:
        logger.warning(f"⚠️  [修复] 第{chapter_id}章 TEXT_TOO_SHORT 补写无效，保留原文")

    return state


def _format_issues(issues: list[ChapterIssue]) -> str:
    return "\n".join(
        f"[{i.severity.value.upper()}] {i.code}: {i.description}"
        + (f" (位置：{i.location})" if i.location else "")
        for i in issues
    )


def _format_char_constraints(chars: list[dict]) -> str:
    if not chars:
        return "无"
    constraints = []
    for c in chars[:5]:
        constraints.append(
            f"{c.get('name','?')}: 境界={c.get('power_level','?')}, "
            f"当前位置={c.get('location','?')}, 状态={c.get('status','alive')}"
        )
    return "\n".join(constraints)


def _build_cannot_change(state: NovelState) -> str:
    """List what MUST NOT change during repair."""
    items = []
    if state.chapter_plan:
        items.append(f"章节核心目标：{state.chapter_plan.goal}")
        if state.chapter_plan.must_include:
            items.append(f"必须保留的情节：{', '.join(state.chapter_plan.must_include)}")
    return "\n".join(items) if items else "无特殊约束"
