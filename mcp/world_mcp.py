"""
mcp/world_mcp.py — World Rules MCP (规则系统)

World rules are IMMUTABLE once set (unless explicitly unlocked).
LLMs read rules but CANNOT modify them.

Provides:
  get_rules(novel_id) → dict of all world rules
  validate_content(novel_id, text) → list of violations
  set_rule(novel_id, key, value) → admin only
"""
import re
from typing import Any, Optional
from loguru import logger
from db import repo


# ═══════════════════════════════════════════════════════
#  Public MCP API
# ═══════════════════════════════════════════════════════

def get_rules(novel_id: str) -> dict:
    """Get all world rules (immutable system rules)."""
    return repo.get_all_world_rules(novel_id)


def get_rule(novel_id: str, key: str, default: Any = None) -> Any:
    rules = get_rules(novel_id)
    return rules.get(key, default)


def set_rule(novel_id: str, key: str, value: Any, immutable: bool = True) -> None:
    """
    Add or update a world rule.
    ONLY callable during initialization — not during generation.
    """
    repo.set_world_rule(novel_id, key, value, immutable)
    logger.info(f"[WorldMCP] rule set: {key}")


def initialize_world_rules(novel_id: str, world_data: dict) -> None:
    """
    Bootstrap world rules from initialization data.
    Called once during novel setup.
    """
    if "cultivation_order" in world_data:
        set_rule(novel_id, "cultivation_order", world_data["cultivation_order"])
    if "timeline_rule" in world_data:
        set_rule(novel_id, "timeline_rule", world_data.get("timeline_rule", "strict_increasing"))
    if "forbidden_zones" in world_data:
        set_rule(novel_id, "forbidden_zones", world_data["forbidden_zones"])
    if "power_system" in world_data:
        set_rule(novel_id, "power_system_rules", world_data["power_system"])
    if "world_constants" in world_data:
        for k, v in world_data["world_constants"].items():
            set_rule(novel_id, f"constant_{k}", v)


def validate_content(novel_id: str, content: str) -> list[dict]:
    """
    Check content against world rules.
    Returns list of violations: [{"rule": str, "violation": str, "severity": str}]
    """
    violations = []
    rules = get_rules(novel_id)

    # Check forbidden zones
    forbidden_zones = rules.get("forbidden_zones", {})
    for zone, restrictions in forbidden_zones.items():
        if zone in content:
            for restriction in (restrictions if isinstance(restrictions, list) else [restrictions]):
                restriction_kw = restriction.replace("no ", "").replace("_", " ")
                if restriction_kw.lower() in content.lower():
                    violations.append({
                        "rule": f"forbidden_zone:{zone}",
                        "violation": f"Content in {zone} uses restricted element: {restriction}",
                        "severity": "high",
                    })

    # Check timeline (basic: no time reversal keywords)
    timeline_rule = rules.get("timeline_rule", "")
    if "strict_increasing" in str(timeline_rule):
        time_reversal_patterns = [r"时间倒流", r"回到过去", r"穿越回", r"时间逆转"]
        for pattern in time_reversal_patterns:
            if re.search(pattern, content):
                violations.append({
                    "rule": "timeline_rule",
                    "violation": f"Time reversal detected: {pattern}",
                    "severity": "high",
                })

    return violations


def get_cultivation_order(novel_id: str) -> list[str]:
    """Get ordered power/cultivation levels for this world."""
    return get_rule(novel_id, "cultivation_order", [])


def format_rules_for_prompt(novel_id: str) -> str:
    """Format world rules as a compact string for injection into prompts."""
    rules = get_rules(novel_id)
    if not rules:
        return "无特殊世界规则"
    lines = []
    for k, v in rules.items():
        if isinstance(v, list):
            lines.append(f"• {k}: {' → '.join(str(x) for x in v)}")
        elif isinstance(v, dict):
            lines.append(f"• {k}: {', '.join(f'{kk}={vv}' for kk, vv in v.items())}")
        else:
            lines.append(f"• {k}: {v}")
    return "\n".join(lines)
