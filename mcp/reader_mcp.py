"""
mcp/reader_mcp.py — Reader Metrics MCP (读者体验评分)

Scores chapters on:
  - engagement (吸引力)
  - tension (紧张度)
  - drop_risk (跳出风险)

Scores are used ONLY for trend analysis — never to gate generation.
"""
from typing import Optional
from loguru import logger
from db import repo
from llm import simple_chat_json


SCORE_SYSTEM = """你是一个网文读者体验分析专家。
请从读者视角分析章节内容，给出量化评分（0-1浮点数）。
只输出JSON，不要解释。"""

SCORE_PROMPT = """请分析以下章节内容，从读者体验角度评分：

【章节内容】
{content}

输出JSON：
{{
  "engagement": 0.0-1.0,   // 读者吸引力（越高越好）
  "tension": 0.0-1.0,       // 紧张程度
  "drop_risk": 0.0-1.0,     // 读者流失风险（越低越好）
  "pacing": "too_slow|ok|too_fast",
  "highlights": ["精彩点1", "精彩点2"],
  "weak_points": ["不足1"]
}}"""


def score_chapter(novel_id: str, chapter_no: int, content: str) -> dict:
    """
    Run reader metrics scoring on a chapter.
    Uses a lighter/faster model since this is trend data, not quality gate.
    """
    try:
        result = simple_chat_json(
            system_prompt=SCORE_SYSTEM,
            user_prompt=SCORE_PROMPT.format(content=content[:3000]),  # trim for cost
            fallback={"engagement": 0.5, "tension": 0.5, "drop_risk": 0.5},
        )
        metrics = {
            "engagement": float(result.get("engagement", 0.5)),
            "tension": float(result.get("tension", 0.5)),
            "drop_risk": float(result.get("drop_risk", 0.5)),
            "pacing": result.get("pacing", "ok"),
            "highlights": result.get("highlights", []),
            "weak_points": result.get("weak_points", []),
        }
        repo.upsert_reader_metrics(
            novel_id=novel_id,
            chapter_no=chapter_no,
            engagement=metrics["engagement"],
            tension=metrics["tension"],
            drop_risk=metrics["drop_risk"],
            raw=metrics,
        )
        logger.debug(f"[ReaderMCP] ch{chapter_no}: engagement={metrics['engagement']:.2f} "
                     f"tension={metrics['tension']:.2f} drop_risk={metrics['drop_risk']:.2f}")
        return metrics
    except Exception as e:
        logger.warning(f"[ReaderMCP] scoring failed: {e}")
        return {"engagement": 0.5, "tension": 0.5, "drop_risk": 0.5}


def get_trend(novel_id: str, last_n: int = 10) -> dict:
    """
    Get engagement/tension trend for last N chapters.
    Returns averages + direction.
    """
    from sqlalchemy import text
    from db.json_session import get_db
    with get_db() as db:
        rows = db.execute(text(
            "SELECT chapter_no, engagement, tension, drop_risk "
            "FROM reader_metrics WHERE novel_id=:nid "
            "ORDER BY chapter_no DESC LIMIT :n"
        ), {"nid": novel_id, "n": last_n}).mappings().all()

    if not rows:
        return {"avg_engagement": 0.5, "avg_tension": 0.5, "avg_drop_risk": 0.5, "trend": "stable"}

    rows = list(reversed(rows))
    engagements = [r["engagement"] for r in rows if r["engagement"] is not None]
    tensions = [r["tension"] for r in rows if r["tension"] is not None]
    drop_risks = [r["drop_risk"] for r in rows if r["drop_risk"] is not None]

    avg_eng = sum(engagements) / len(engagements) if engagements else 0.5
    avg_ten = sum(tensions) / len(tensions) if tensions else 0.5
    avg_dr = sum(drop_risks) / len(drop_risks) if drop_risks else 0.5

    # Trend: compare first half vs second half
    mid = len(engagements) // 2
    if mid > 0:
        first_half = sum(engagements[:mid]) / mid
        second_half = sum(engagements[mid:]) / max(len(engagements) - mid, 1)
        if second_half > first_half + 0.1:
            trend = "rising"
        elif second_half < first_half - 0.1:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "stable"

    return {
        "avg_engagement": round(avg_eng, 3),
        "avg_tension": round(avg_ten, 3),
        "avg_drop_risk": round(avg_dr, 3),
        "trend": trend,
        "sample_size": len(engagements),
    }
