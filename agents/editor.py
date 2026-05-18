"""
agents/editor.py — Editor Agent (文字润色师)

Responsibilities:
  - Improve expression and flow
  - Maintain pacing
  - DO NOT alter plot or character actions
"""
from loguru import logger
from db.novel_log import log_info
from langgraph_engine.state import NovelState
from llm import chat
from mcp import style_mcp
from config.prompts import EDITOR_SYSTEM, EDITOR_PROMPT
from config import get_settings
from agents.base import guard_error


def run(state: NovelState) -> NovelState:
    """LangGraph node: Editor polishes the draft."""
    if guard_error(state, "编辑"):
        return state

    novel_id = state.novel_id
    chapter_id = state.chapter_id
    draft = state.draft_text

    if not draft:
        logger.warning(f"⚠️  [编辑] 第{chapter_id}章无草稿，跳过润色")
        return state

    s = get_settings()
    style_sig = state.style_signature or style_mcp.get_style_signature(novel_id)
    pacing_note = state.memory_snapshot.get("director_directive", {}).get("pacing_note", "中速推进")
    emotion_arc = state.memory_snapshot.get("chapter_arc", "") or (
        state.narrative_directive.emotion_curve if state.narrative_directive else "平稳推进"
    )

    # Format style signature for prompt
    style_str = _format_style_sig(style_sig)

    try:
        result, usage = chat(
            [
                {"role": "system", "content": EDITOR_SYSTEM},
                {"role": "user", "content": EDITOR_PROMPT.format(
                    draft_text=draft,
                    style_signature=style_str,
                    emotion_arc=emotion_arc,
                    pacing_note=pacing_note,
                )},
            ],
            max_tokens=max(12000, s.llm_max_tokens),
        )
        edited = _strip_editorial_notes(result.strip())
        is_truncated = usage.get("truncated", False)

        # ── Case 1: truly truncated AND output too short → split-edit ──
        # Threshold 0.5: balanced — 0.7 was too aggressive, discarding usable
        # truncated output and triggering wasted split-edit fallback runs.
        if is_truncated and len(edited) < len(draft) * 0.5:
            logger.warning(
                f"⚠️  [编辑] 第{chapter_id}章润色被截断 ({len(edited)}/{len(draft)} 字)，"
                f"拆分后分段润色"
            )
            split_edited = _split_edit_draft(draft, style_str, emotion_arc, pacing_note)
            if split_edited and len(split_edited) >= len(draft) * 0.5:
                state.edited_text = split_edited
                logger.info(
                    f"✏️  [编辑] 第{chapter_id}章分段润色完成: "
                    f"{len(draft)} → {len(state.edited_text)} 字"
                )
                log_info(novel_id, f"✏️  [编辑] 第{chapter_id}章分段润色: {len(state.edited_text)}字")
            else:
                # Split-edit also failed → keep original draft
                logger.warning(
                    f"⚠️  [编辑] 第{chapter_id}章分段润色也失败，保留原始草稿"
                )
                state.edited_text = draft

        # ── Case 2: non-truncated but extremely short → warn but accept ──
        elif len(edited) < len(draft) * 0.4:
            logger.warning(
                f"⚠️  [编辑] 第{chapter_id}章润色输出偏短 ({len(edited)}/{len(draft)} 字)，"
                f"但未截断，接受润色结果"
            )
            state.edited_text = edited

        # ── Case 3: normal case → accept ──
        else:
            state.edited_text = edited
            logger.info(
                f"✏️  [编辑] 第{chapter_id}章润色完成: {len(draft)} → {len(state.edited_text)} 字"
                f"  (tokens={usage.get('completion_tokens', 0)})"
            )
            log_info(novel_id, f"✏️  [编辑] 第{chapter_id}章润色完成: {len(state.edited_text)}字")
    except Exception as e:
        logger.error(f"❌ [编辑] 第{chapter_id}章润色失败，保留原始草稿: {e}")
        # Fallback: pass draft through unchanged
        state.edited_text = draft

    return state


def _format_style_sig(sig: dict) -> str:
    return (
        f"基调={sig.get('overall_tone', '热血')}, "
        f"对话比例≈{int(sig.get('dialogue_ratio', 0.35) * 100)}%, "
        f"情感密度={sig.get('emotion_density', 'medium')}, "
        f"节奏={sig.get('action_speed', 'medium')}, "
        f"视角={sig.get('narration_type', 'immersive')}"
    )


def _strip_editorial_notes(text: str) -> str:
    """Remove all 【编辑建议】 meta-content from the editor LLM output.

    Three forms to handle:
    0. Leading markdown headers: the LLM sometimes prepends various meta
       headers like "# 润色后的正文", "# 润色后正文", "# 润色后的完整正文",
       "# 修改后正文", "# 修改后的正文", "# 优化后正文", or even story text
       prefixed by "#".  Strip any leading markdown header line.
    1. Trailing block: the prompt asks the model to append a summary section
       after a '---' separator.  Everything from that separator onward is
       discarded.
    2. Inline annotations: the prompt also allows the model to insert
       【编辑建议…】 markers mid-text.  These are removed so surrounding
       prose is left intact.
    """
    import re

    # ── 0. Strip leading markdown headers (e.g. "# 润色后的正文") ──────
    lines = text.split('\n')
    while lines and re.match(r'^#{1,6}\s+', lines[0].strip()):
        lines.pop(0)
    text = '\n'.join(lines).strip()

    # ── 1. Strip trailing block (--- … 【编辑建议】 … end) ─────────────
    cut = re.search(r'\n---\s*\n.*?【编辑建议】', text, re.DOTALL)
    if cut:
        text = text[:cut.start()].rstrip()
    elif '【编辑建议】' in text:
        # Fallback: no --- separator; only trim if it appears in the last
        # quarter of the text to avoid accidentally cutting mid-text notes.
        idx = text.rfind('【编辑建议】')
        if idx > len(text) * 0.75:
            text = text[:idx].rstrip()

    # ── 2. Remove inline 【编辑建议…】 annotations ──────────────────────
    # Bracketed span that closes with 】
    text = re.sub(r'【编辑建议[\s\S]*?】', '', text)
    # Markdown-bold variant: **【编辑建议…】**
    text = re.sub(r'\*\*【编辑建议[\s\S]*?】\*\*', '', text)
    # Clean up any double blank lines left behind
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _split_edit_draft(draft: str, style_str: str, emotion_arc: str,
                      pacing_note: str) -> str | None:
    """Split a long draft into halves and edit each part separately.

    Each part sees only its half of the text plus the relevant style context.
    Parts are edited independently and then recombined.
    """
    from config.prompts import EDITOR_SPLIT_PROMPT
    s = get_settings()

    # Find split point at a paragraph boundary near the midpoint
    midpoint = len(draft) // 2
    split = draft.rfind('\n\n', 0, midpoint + 200)
    if split < len(draft) // 4:
        split = draft.find('\n\n', midpoint - 200)
    if split < 0:
        split = midpoint  # no paragraph break found, hard split

    part1_text = draft[:split]
    part2_text = draft[split:]

    results = []
    for i, (part, label) in enumerate([
        (part1_text, "前半"),
        (part2_text, "后半"),
    ]):
        try:
            result, _ = chat(
                [
                    {"role": "system", "content": EDITOR_SYSTEM},
                    {"role": "user", "content": EDITOR_SPLIT_PROMPT.format(
                        split_label=label,
                        draft_text=part,
                        style_signature=style_str,
                        emotion_arc=emotion_arc,
                        pacing_note=pacing_note,
                    )},
                ],
                max_tokens=s.llm_max_tokens,
            )
            edited = _strip_editorial_notes(result.strip())
            results.append(edited)
        except Exception as e:
            logger.warning(f"[split-edit] {label}失败: {e}，使用原文")
            results.append(part)

    if len(results) == 2:
        return results[0].rstrip() + "\n\n" + results[1].lstrip()
    return None
