"""
eval/constory_bench.py — ConStory-Bench 一致性评测

5-dimension consistency evaluation inspired by Microsoft Research ConStory-Bench.
Scores each dimension 0-10, produces an aggregate consistency report.

Dimensions:
  1. character  — character traits, relationships, power levels stay consistent
  2. event      — events follow causal logic, no contradictions
  3. setting    — locations and world rules remain consistent
  4. temporal   — time progression is logical
  5. coherence  — overall narrative arc cohesion

Usage:
    from eval.constory_bench import evaluate_volume
    report = evaluate_volume(novel_id, volume_no)
    print(report["summary"])
"""

from __future__ import annotations

import json
from typing import Optional
from loguru import logger


_EVAL_PROMPT = """你是一位专业的小说质量评测员。请对以下小说内容进行一致性评测。

【小说基础信息】
{novel_context}

【待评测章节摘要（第{vol_start}章 ~ 第{vol_end}章）】
{chapter_summaries}

【人物状态快照】
{character_snapshot}

【伏笔记录】
{foreshadow_summary}

请从以下5个维度评分（每项0-10分），并给出简短说明：

1. **人物一致性**（character）：人物性格、关系、能力等级是否前后一致，有无自相矛盾
2. **事件一致性**（event）：事件是否有因果逻辑，有无矛盾冲突或时间线错误
3. **场景一致性**（setting）：地点描述、世界规则是否前后统一，有无矛盾
4. **时序一致性**（temporal）：时间流逝、章节衔接是否合理，有无跳跃或重复
5. **叙事连贯性**（coherence）：整体故事弧度是否清晰，情节推进是否顺畅

请严格按JSON格式回复，不要输出其他内容：
{{
  "character": {{"score": <0-10>, "issues": ["<issue1>", "<issue2>"]}},
  "event":     {{"score": <0-10>, "issues": []}},
  "setting":   {{"score": <0-10>, "issues": []}},
  "temporal":  {{"score": <0-10>, "issues": []}},
  "coherence": {{"score": <0-10>, "issues": []}},
  "overall_notes": "<综合评语>"
}}"""


def evaluate_volume(
    novel_id: str,
    volume_no: int,
    chapter_start: Optional[int] = None,
    chapter_end: Optional[int] = None,
) -> dict:
    """
    Run ConStory-Bench evaluation on a completed volume.
    Returns a report dict with scores, issues, and a summary string.
    Falls back to a stub report on any error.
    """
    try:
        from db import repo
        from llm.client import chat_json

        # Load novel context
        novel = repo.get_novel(novel_id) or {}
        story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
        novel_context = (
            f"书名：{novel.get('title', '未知')}\n"
            f"类型：{story_outline.get('world_type', '未知')}\n"
            f"简介：{story_outline.get('description', '')[:200]}"
        )

        # Load chapter summaries for this volume
        from db.json_session import get_db
        from sqlalchemy import text as _text
        with get_db() as db:
            rows = db.execute(_text(
                "SELECT chapter_no, summary_text FROM chapter_summaries "
                "WHERE novel_id=:nid AND volume_no=:vno ORDER BY chapter_no"
            ), {"nid": novel_id, "vno": volume_no}).fetchall()

        if not rows:
            return _empty_report(volume_no, "no chapter summaries found")

        vol_start = rows[0][0]
        vol_end = rows[-1][0]
        summaries_text = "\n".join(
            f"第{r[0]}章: {(r[1] or '')[:120]}" for r in rows
        )

        # Character snapshot
        from mcp import memory_mcp
        characters = memory_mcp.get_all_characters(novel_id)
        if characters:
            char_lines = [
                f"- {c.get('name', c.get('char_id', '?'))}: "
                f"境界={c.get('power_level','')}, 位置={c.get('location','')}, "
                f"状态={c.get('status','')}"
                for c in characters[:10]
            ]
            character_snapshot = "\n".join(char_lines)
        else:
            character_snapshot = "（无人物快照）"

        # Foreshadow summary
        from mcp import foreshadow_mcp
        unresolved = foreshadow_mcp.get_all_unresolved(novel_id)
        due = foreshadow_mcp.get_due(novel_id, vol_end)
        foreshadow_summary = (
            f"未解决伏笔数: {len(unresolved)}, 逾期伏笔数: {len(due)}\n"
        )
        if due:
            foreshadow_summary += "逾期: " + "; ".join(
                f.get("description", "")[:40] for f in due[:5]
            )

        prompt = _EVAL_PROMPT.format(
            novel_context=novel_context,
            vol_start=vol_start,
            vol_end=vol_end,
            chapter_summaries=summaries_text,
            character_snapshot=character_snapshot,
            foreshadow_summary=foreshadow_summary,
        )

        raw = chat_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        return _build_report(volume_no, vol_start, vol_end, raw)

    except Exception as e:
        logger.warning(f"[ConStoryBench] evaluate_volume failed: {e}")
        return _empty_report(volume_no, str(e))


def _build_report(volume_no: int, vol_start: int, vol_end: int, raw: dict) -> dict:
    """Parse LLM response into a structured report."""
    dimensions = ["character", "event", "setting", "temporal", "coherence"]
    dim_labels = {
        "character": "人物一致性",
        "event": "事件一致性",
        "setting": "场景一致性",
        "temporal": "时序一致性",
        "coherence": "叙事连贯性",
    }

    scores = {}
    all_issues = []
    for dim in dimensions:
        entry = raw.get(dim, {})
        score = float(entry.get("score", 5))
        score = max(0.0, min(10.0, score))
        scores[dim] = score
        issues = entry.get("issues", [])
        if isinstance(issues, list):
            for iss in issues:
                if iss:
                    all_issues.append(f"[{dim_labels[dim]}] {iss}")

    avg_score = round(sum(scores.values()) / len(scores), 2)
    passed = avg_score >= 7.0

    # Build human-readable summary
    score_lines = " | ".join(
        f"{dim_labels[d]}={scores[d]:.1f}" for d in dimensions
    )
    summary = (
        f"第{volume_no}卷一致性评分: {avg_score:.1f}/10  {'✅通过' if passed else '⚠️需修改'}\n"
        f"  {score_lines}\n"
    )
    if all_issues:
        summary += "  问题:\n" + "\n".join(f"    • {i}" for i in all_issues[:10])

    return {
        "volume_no": volume_no,
        "chapter_range": [vol_start, vol_end],
        "scores": scores,
        "average_score": avg_score,
        "passed": passed,
        "issues": all_issues,
        "overall_notes": raw.get("overall_notes", ""),
        "summary": summary,
    }


def _empty_report(volume_no: int, reason: str) -> dict:
    return {
        "volume_no": volume_no,
        "chapter_range": [0, 0],
        "scores": {},
        "average_score": 0.0,
        "passed": False,
        "issues": [],
        "overall_notes": f"评测跳过: {reason}",
        "summary": f"第{volume_no}卷评测跳过: {reason}",
    }
