"""
mcp/style_mcp.py — Style MCP (文风控制系统)

Stores writing style configuration and provides:
  - Style directives for Writer prompt injection
  - Style consistency checking
  - Variance injection to prevent fatigue
"""
from typing import Optional
from loguru import logger
from db import repo


DEFAULT_STYLE = {
    "dialogue_ratio": 0.35,        # Target ratio of dialogue vs narration
    "emotion_density": "medium",   # low | medium | high
    "action_speed": "medium",      # slow | medium | fast
    "narration_type": "immersive", # omniscient | immersive | cinematic
    "sentence_length": "mixed",    # short | long | mixed
    "forbidden_expressions": [
        "副本", "BOSS", "掉落", "刷怪", "血条", "技能CD",  # Game-language
        "如果你", "你会怎么做", "欲知后事如何",  # Breaking 4th wall
    ],
    "preferred_expressions": {
        "秘境": ["副本"],
        "强敌": ["BOSS"],
        "战利品": ["掉落"],
    },
    "dialogue_mannerisms": {
        "enabled": False,
        "description": "",
        "exclamations": [],
        "swears": [],
        "filler_words": [],
    },
    "overall_tone": "热血成长",
    "emotional_signature": {
        "core_emotion": "",
        "emotional_range": "",
        "rhythm": "",
        "forbidden_tones": [],
    },
    "chapter_requirements": {
        "min_conflicts": 1,
        "min_advances": 1,
        "min_emotion_changes": 1,
    },
}


def get_style(novel_id: str) -> dict:
    """Get the writing style config for this novel."""
    stored = repo.get_world_memory(novel_id, "novel_style")
    if not stored:
        return DEFAULT_STYLE.copy()
    if isinstance(stored, dict):
        return {**DEFAULT_STYLE, **stored}
    return DEFAULT_STYLE.copy()


def set_style(novel_id: str, style: dict) -> None:
    """Store writing style config."""
    repo.set_world_memory(novel_id, "novel_style", style)
    logger.info(f"[StyleMCP] style saved for novel {novel_id}")


def get_style_signature(novel_id: str) -> dict:
    """
    Get compact style signature for Writer prompt injection.
    """
    style = get_style(novel_id)
    return {
        "overall_tone": style.get("overall_tone", "热血成长"),
        "emotional_signature": style.get("emotional_signature", {}),
        "dialogue_ratio": style.get("dialogue_ratio", 0.35),
        "emotion_density": style.get("emotion_density", "medium"),
        "action_speed": style.get("action_speed", "medium"),
        "narration_type": style.get("narration_type", "immersive"),
        "sentence_length": style.get("sentence_length", "mixed"),
        "forbidden": style.get("forbidden_expressions", []),
        "preferred": style.get("preferred_expressions", {}),
        "dialogue_mannerisms": style.get("dialogue_mannerisms", {}),
    }


def get_variance_brief(novel_id: str, recent_chapters: int = 3) -> str:
    """
    Analyze recent chapters to generate variance directive.
    Prevents style fatigue / repetition.
    """
    recent = repo.get_recent_summaries(novel_id, limit=recent_chapters)
    if not recent:
        return ""

    # Simple heuristic: vary emotion density based on recent trend
    phases = [r.get("arc_phase", "") for r in recent]
    recent_text = " ".join([r.get("summary_text", "") for r in recent])

    hints = []
    if phases.count("climax") >= 2:
        hints.append("本章请适度降低紧张感，加入人物内心独白或日常互动")
    if phases.count("setup") >= 2:
        hints.append("本章请加快节奏，提高冲突密度，减少铺垫")
    if "对话" not in recent_text:
        hints.append("本章请适当增加角色对话场景")

    return "；".join(hints) if hints else ""


def format_for_prompt(novel_id: str) -> str:
    """Format style directives as prompt section."""
    sig = get_style_signature(novel_id)
    forbidden = "、".join(sig["forbidden"][:10]) if sig["forbidden"] else "无"
    preferred_pairs = "; ".join(
        f"用「{k}」代替「{'、'.join(v)}」"
        for k, v in (sig["preferred"] or {}).items()
    ) if sig["preferred"] else "无"

    # Emotional signature section
    emo_sig = sig.get("emotional_signature", {})
    emo_lines = []
    if emo_sig.get("core_emotion"):
        emo_lines.append(f"• 全书情绪主题：{emo_sig['core_emotion']}")
    if emo_sig.get("emotional_range"):
        emo_lines.append(f"• 情绪幅度：{emo_sig['emotional_range']}")
    if emo_sig.get("rhythm"):
        emo_lines.append(f"• 情绪节奏：{emo_sig['rhythm']}")
    if emo_sig.get("forbidden_tones"):
        emo_lines.append(f"• 禁止情绪基调：{'、'.join(emo_sig['forbidden_tones'])}")
    emo_section = ("\n" + "\n".join(emo_lines)) if emo_lines else ""

    # Dialogue mannerisms section
    dm = sig.get("dialogue_mannerisms", {})
    dm_lines = []
    if dm and dm.get("enabled"):
        desc = dm.get("description", "")
        if desc:
            dm_lines.append(f"• 对话风格说明：{desc}")
        excls = dm.get("exclamations", [])
        if excls:
            dm_lines.append(f"• 常用感叹词：{'、'.join(excls)}")
        swears = dm.get("swears", [])
        if swears:
            dm_lines.append(f"• 可控粗口：{'、'.join(swears)}（注意：不用每一句都加，关键时刻使用效果更佳）")
        fillers = dm.get("filler_words", [])
        if fillers:
            dm_lines.append(f"• 口头禅/语气词：{'、'.join(fillers)}")
    dm_section = ("\n" + "\n".join(dm_lines)) if dm_lines else ""

    # Narrative principles section
    style = get_style(novel_id)
    principles = style.get("narrative_principles", [])
    np_section = ""
    if principles:
        np_lines = [f"• {p}" for p in principles]
        np_section = "\n" + "\n".join(np_lines)

    return f"""【文风要求】
• 整体基调：{sig["overall_tone"]}
• 对话比例：约{int(sig['dialogue_ratio']*100)}%
• 情感密度：{sig["emotion_density"]}
• 叙事节奏：{sig["action_speed"]}
• 叙事视角：{sig["narration_type"]}
• 句式风格：{sig["sentence_length"]}
• 禁止词汇：{forbidden}
• 用词替换：{preferred_pairs}{emo_section}{dm_section}{np_section}"""


def detect_style_drift(novel_id: str, chapter_id: int) -> dict:
    """
    Detect style drift by comparing recent chapter text against the configured style.

    Runs every 10 chapters. Returns a drift report dict:
    {
        "drifted": bool,
        "issues": [str],           # human-readable issues found
        "recalibration": str,      # suggested correction for next chapter
    }
    """
    if chapter_id % 10 != 0 or chapter_id == 0:
        return {"drifted": False, "issues": [], "recalibration": ""}

    try:
        style = get_style(novel_id)
        chapters = repo.get_recent_chapters(novel_id, chapter_id, limit=5)
        if not chapters:
            return {"drifted": False, "issues": [], "recalibration": ""}

        issues = []
        forbidden = style.get("forbidden_expressions", [])
        target_dialogue_ratio = style.get("dialogue_ratio", 0.35)

        for ch in chapters:
            text = ch.get("content", "")
            if not text or len(text) < 500:
                continue

            # Check forbidden words
            for word in forbidden:
                if word in text:
                    issues.append(f"第{ch.get('chapter_no', '?')}章含禁词「{word}」")

            # Estimate dialogue ratio by character count (consistent with the config target)
            import re as _re
            dialogue_chars = sum(
                len(m.group())
                for m in _re.finditer(r'[「""][^「""]*[」""]', text)
            )
            ratio = dialogue_chars / len(text) if len(text) > 0 else 0.0
            if abs(ratio - target_dialogue_ratio) > 0.20:
                direction = "过多" if ratio > target_dialogue_ratio else "过少"
                issues.append(
                    f"第{ch.get('chapter_no', '?')}章对话比例{direction}"
                    f"（实际{ratio:.0%} vs 目标{target_dialogue_ratio:.0%}）"
                )

        if not issues:
            return {"drifted": False, "issues": [], "recalibration": ""}

        # Build recalibration hint
        recal_parts = []
        if any("禁词" in i for i in issues):
            recal_parts.append("下一章严格避免游戏化词汇和禁用表达")
        if any("对话比例" in i for i in issues):
            recal_parts.append(f"将对话比例调整至目标{target_dialogue_ratio:.0%}附近")

        recalibration = "；".join(recal_parts)

        # Persist drift report
        repo.set_world_memory(novel_id, f"style_drift_ch{chapter_id}", {
            "chapter_id": chapter_id,
            "issues": issues,
            "recalibration": recalibration,
        })

        return {"drifted": True, "issues": issues, "recalibration": recalibration}
    except Exception as e:
        logger.warning(f"[StyleMCP] detect_style_drift failed: {e}")
        return {"drifted": False, "issues": [], "recalibration": ""}


def validate_style(content: str, novel_id: str) -> list[dict]:
    """Check content against style rules. Returns violations."""
    style = get_style(novel_id)
    violations = []
    forbidden = style.get("forbidden_expressions", [])
    for word in forbidden:
        if word in content:
            violations.append({
                "type": "forbidden_word",
                "word": word,
                "severity": "medium",
            })
    return violations
