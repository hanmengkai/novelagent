"""
narrative/arc_planner.py — Cross-volume arc planning

Maintains a global narrative arc skeleton across all volumes.
Prevents each volume from planning in isolation, which causes
long-range plot drift.

Key functions:
  - init_arc_plan(novel_id)           — create skeleton at novel init
  - get_arc_guidance(novel_id, vol)   — return arc notes for volume planning
  - update_arc_progress(novel_id, vol) — update after volume completion
"""
import json
from loguru import logger
from db import repo
from llm import simple_chat_json


def init_arc_plan(novel_id: str) -> dict:
    """
    Called once after story_outline is created.
    Reads story_outline from world_memory and generates a per-volume arc skeleton.

    Returns the arc_plan dict (with key "arc_plan": [...]).
    """
    story_outline = repo.get_world_memory(novel_id, "story_outline") or {}
    if not story_outline:
        logger.warning(f"[ArcPlanner] No story_outline found for novel {novel_id}, skipping arc plan")
        return {}

    prompt = f"""你是小说全局弧线规划师。根据故事大纲，为每一卷分配明确的叙事职责。

【故事大纲】
{json.dumps(story_outline, ensure_ascii=False)[:3000]}

请为每卷输出其核心使命，格式：
{{
  "arc_plan": [
    {{
      "volume_no": 1,
      "arc_role": "本卷在全书中的叙事职责（50字）",
      "must_setup": ["本卷必须铺垫的元素"],
      "must_resolve": ["本卷必须收束的上卷遗留问题"],
      "protagonist_state_target": "主角在本卷末应达到的状态",
      "emotional_peak": "本卷最高情绪点描述"
    }}
  ]
}}"""

    result = simple_chat_json(
        system_prompt="你是小说全局弧线规划师，输出完整的跨卷弧线计划，只输出JSON。",
        user_prompt=prompt,
        fallback={"arc_plan": []},
    )

    repo.set_world_memory(novel_id, "cross_volume_arc_plan", result)
    logger.info(
        f"[ArcPlanner] Cross-volume arc plan saved: "
        f"{len(result.get('arc_plan', []))} volumes"
    )
    return result


def get_arc_guidance(novel_id: str, volume_no: int) -> str:
    """
    Reads cross_volume_arc_plan from world_memory.
    Returns a formatted string with arc notes for the given volume.

    Returns empty string if no arc plan exists.
    """
    arc_data = repo.get_world_memory(novel_id, "cross_volume_arc_plan") or {}
    arc_plan = arc_data.get("arc_plan", [])
    if not arc_plan:
        return ""

    # Find the entry for this volume
    entry = next(
        (v for v in arc_plan if v.get("volume_no") == volume_no),
        None,
    )
    if not entry:
        return ""

    lines = [
        f"本卷叙事职责：{entry.get('arc_role', '')}",
    ]

    must_setup = entry.get("must_setup", [])
    if must_setup:
        lines.append("本卷必须铺垫：" + "、".join(str(s) for s in must_setup))

    must_resolve = entry.get("must_resolve", [])
    if must_resolve:
        lines.append("本卷必须收束的上卷遗留：" + "、".join(str(r) for r in must_resolve))

    state_target = entry.get("protagonist_state_target", "")
    if state_target:
        lines.append(f"主角本卷末目标状态：{state_target}")

    emotional_peak = entry.get("emotional_peak", "")
    if emotional_peak:
        lines.append(f"本卷情绪高峰：{emotional_peak}")

    return "\n".join(lines)


def update_arc_progress(novel_id: str, volume_no: int) -> None:
    """
    After a volume completes, update the arc plan with actual completion status.
    Marks the volume as completed and records the actual ending.
    """
    arc_data = repo.get_world_memory(novel_id, "cross_volume_arc_plan") or {}
    arc_plan = arc_data.get("arc_plan", [])
    if not arc_plan:
        return

    # Find and update the entry for this volume
    updated = False
    for entry in arc_plan:
        if entry.get("volume_no") == volume_no:
            entry["completed"] = True

            # Try to get the last chapter ending from world_memory
            last_ending = repo.get_world_memory(novel_id, "last_chapter_ending") or ""
            if isinstance(last_ending, str) and last_ending:
                entry["actual_ending"] = last_ending[:200]
            elif isinstance(last_ending, dict):
                entry["actual_ending"] = (last_ending.get("summary") or last_ending.get("text") or "")[:200]

            updated = True
            break

    if updated:
        arc_data["arc_plan"] = arc_plan
        repo.set_world_memory(novel_id, "cross_volume_arc_plan", arc_data)
        logger.info(f"[ArcPlanner] Volume {volume_no} marked as completed in arc plan")
    else:
        logger.debug(
            f"[ArcPlanner] Volume {volume_no} not found in arc plan (plan has "
            f"{len(arc_plan)} entries), skipping update"
        )
