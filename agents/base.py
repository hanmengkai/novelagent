"""
agents/base.py — Shared utilities for all agent nodes.

Keeps agent modules DRY without forcing a class hierarchy onto function-based nodes.
"""
from loguru import logger
from langgraph_engine.state import NovelState


def guard_error(state: NovelState, label: str) -> bool:
    """Return True (and log) if state carries an upstream error — agent should return early.

    Usage::
        if guard_error(state, "编辑"):
            return state
    """
    if state.error:
        logger.warning(f"⚠️  [{label}] 跳过（上游错误: {state.error}）")
        return True
    return False
