"""
langgraph_engine/graph.py — LangGraph StateGraph definition for one chapter.

Flow:
  director → planner → context_builder → writer → editor →
  checker →(issues?)→ repair_agent → checker (loop)
  checker → narrative_controller → fact_extract → memory_commit → foreshadow_update
  →(every N ch, default 20)→ compactor → END
"""
import time
from langgraph.graph import StateGraph, END
from loguru import logger

from db.novel_log import log_info as _log_novel

from .state import NovelState
from .router import (
    route_after_checker,
    route_after_repair,
    route_after_foreshadow,
    route_after_compactor,
)


class ImmediateStopException(Exception):
    """Raised inside LangGraph nodes when an immediate stop is requested."""


def _make_node(agent_fn, name: str):
    """Wrap an agent function with logging + error capture + stop check."""
    def node(state: NovelState) -> NovelState:
        # Check stop flag at every node boundary for immediate stop
        from db import cache as rc
        if rc.is_stop_requested(state.novel_id):
            logger.info(f"[{name}] 🛑 立即停止信号，中断第{state.chapter_id}章 (stage={name})")
            raise ImmediateStopException(f"Stop requested at stage {name}")
        logger.info(f"[{name}] chapter={state.chapter_id} retry={state.retry_count}")
        # Per-novel log for progress display
        _log_novel(state.novel_id, f"⚙️ [{name}] 第{state.chapter_id}章")
        state.stage = name
        start = time.time()
        try:
            result = agent_fn(state)
            elapsed = time.time() - start
            logger.info(f"[{name}] ✅ done ({elapsed:.1f}s) chapter={state.chapter_id}")
            return result
        except ImmediateStopException:
            raise
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[{name}] FAILED ({elapsed:.1f}s): {e}")
            state.error = f"{name}: {e}"
            return state
    node.__name__ = name
    return node


def build_chapter_graph() -> StateGraph:
    """
    Build and compile the chapter generation graph.
    All agents are imported lazily to avoid circular imports.
    """
    from agents.director import run as director_run
    from agents.planner import run as planner_run
    from context_builder.builder import run as context_run
    from agents.writer import run as writer_run
    from agents.editor import run as editor_run
    from agents.checker import run as checker_run
    from agents.repair_agent import run as repair_run
    from narrative.controller import run as narrative_run
    from agents.fact_extractor import run as fact_extractor_run
    from mcp.memory_mcp import commit as memory_commit_run
    from mcp.foreshadow_mcp import update as foreshadow_update_run
    from compactor.compactor import run as compactor_run

    graph = StateGraph(NovelState)

    # Register all nodes
    graph.add_node("director",            _make_node(director_run,            "director"))
    graph.add_node("planner",             _make_node(planner_run,             "planner"))
    graph.add_node("context_builder",     _make_node(context_run,             "context_builder"))
    graph.add_node("writer",              _make_node(writer_run,              "writer"))
    graph.add_node("editor",              _make_node(editor_run,              "editor"))
    graph.add_node("checker",             _make_node(checker_run,             "checker"))
    graph.add_node("repair_agent",        _make_node(repair_run,              "repair_agent"))
    graph.add_node("narrative_controller",_make_node(narrative_run,           "narrative_controller"))
    graph.add_node("fact_extract",        _make_node(fact_extractor_run,      "fact_extract"))
    graph.add_node("memory_commit",       _make_node(memory_commit_run,       "memory_commit"))
    graph.add_node("foreshadow_update",   _make_node(foreshadow_update_run,   "foreshadow_update"))
    graph.add_node("compactor",           _make_node(compactor_run,           "compactor"))

    # Entry point
    graph.set_entry_point("director")

    # Linear edges (always)
    graph.add_edge("director",        "planner")
    graph.add_edge("planner",         "context_builder")
    graph.add_edge("context_builder", "writer")
    graph.add_edge("writer",          "editor")
    graph.add_edge("editor",          "checker")

    # Conditional: checker → repair or narrative
    graph.add_conditional_edges(
        "checker",
        route_after_checker,
        {
            "repair_agent":         "repair_agent",
            "narrative_controller": "narrative_controller",
        },
    )

    # Conditional: repair → checker (loop) or narrative (exhausted)
    graph.add_conditional_edges(
        "repair_agent",
        route_after_repair,
        {
            "checker":              "checker",
            "narrative_controller": "narrative_controller",
        },
    )

    # Narrative → fact_extract → memory
    graph.add_edge("narrative_controller", "fact_extract")
    graph.add_edge("fact_extract",         "memory_commit")
    graph.add_edge("memory_commit",        "foreshadow_update")

    # Conditional: foreshadow → compactor or end
    graph.add_conditional_edges(
        "foreshadow_update",
        route_after_foreshadow,
        {
            "compactor": "compactor",
            "__end__":   END,
        },
    )

    graph.add_conditional_edges(
        "compactor",
        route_after_compactor,
        {"__end__": END},
    )

    return graph.compile()


# Singleton compiled graph
_compiled_graph = None


def get_chapter_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_chapter_graph()
        logger.info("LangGraph chapter graph compiled successfully")
    return _compiled_graph


def run_chapter(state: NovelState) -> NovelState:
    """Execute the full chapter pipeline and return final state."""
    import dataclasses
    graph = get_chapter_graph()
    result = graph.invoke(state)
    # LangGraph.invoke() returns AddableValuesDict (dict-like), not NovelState dataclass.
    # Reconstruct the dataclass so callers can use attribute access.
    if isinstance(result, NovelState):
        return result
    valid_fields = {f.name for f in dataclasses.fields(NovelState)}
    return NovelState(**{k: v for k, v in result.items() if k in valid_fields})
