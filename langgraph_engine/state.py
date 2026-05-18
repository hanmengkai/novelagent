"""
langgraph_engine/state.py — The single source of truth state for one chapter run.

NovelState flows through the entire LangGraph pipeline.
Each node reads its required fields and writes its output fields.
"""
from __future__ import annotations
from typing import Annotated, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ArcPhase(str, Enum):
    SETUP = "setup"
    BUILDUP = "buildup"
    CLIMAX = "climax"
    COOLDOWN = "cooldown"


class IssueSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ChapterIssue:
    code: str                           # e.g. "CHARACTER_INCONSISTENCY"
    description: str
    severity: IssueSeverity
    location: Optional[str] = None      # e.g. "paragraph 3"


@dataclass
class NarrativeDirective:
    arc_phase: ArcPhase
    emotion_curve: str                  # e.g. "low → rising → peak"
    conflict_intensity: float           # 0.0-1.0
    next_chapter_goal: str
    style_notes: Optional[str] = None


@dataclass
class ChapterPlan:
    title: str
    goal: str                           # Main chapter objective
    key_scenes: list[str]               # 3-5 scene descriptions
    key_characters: list[str]           # Character IDs active this chapter
    must_include: list[str]             # Mandatory plot elements
    must_avoid: list[str]               # Forbidden content
    conflict_setup: str                 # The conflict to introduce/resolve
    foreshadow_ops: list[dict]          # {"op": "plant|activate|resolve", "id": "..."}


@dataclass
class NovelState:
    # ── Identity ────────────────────────────────────────
    novel_id: str
    chapter_id: int
    volume_no: int

    # ── World context ───────────────────────────────────
    world_id: str = ""                   # Same as novel_id (kept for clarity)
    active_characters: list[dict] = field(default_factory=list)
    world_snapshot: dict = field(default_factory=dict)

    # ── Planning ────────────────────────────────────────
    chapter_plan: Optional[ChapterPlan] = None
    narrative_directive: Optional[NarrativeDirective] = None

    # ── Content ─────────────────────────────────────────
    draft_text: str = ""
    edited_text: str = ""
    final_text: str = ""

    # ── Memory snapshot (MCP read at start) ─────────────
    memory_snapshot: dict = field(default_factory=dict)
    recent_summaries: list[dict] = field(default_factory=list)

    # ── Foreshadowing ────────────────────────────────────
    foreshadowing_due: list[dict] = field(default_factory=list)   # Must be resolved this chapter
    foreshadowing_active: list[dict] = field(default_factory=list)  # Open foreshadows

    # ── Validation ──────────────────────────────────────
    issues: list[ChapterIssue] = field(default_factory=list)
    repair_patches: list[dict] = field(default_factory=list)   # JSON patches applied

    # ── Thematic core ────────────────────────────────────
    thematic_core: dict = field(default_factory=dict)

    # ── Style ────────────────────────────────────────────
    style_signature: dict = field(default_factory=dict)

    # ── Control ─────────────────────────────────────────
    author_intent: str = ""
    current_focus: str = ""

    # ── Flow control ────────────────────────────────────
    retry_count: int = 0
    max_retries: int = 3
    stage: str = "director"              # current pipeline stage
    error: Optional[str] = None          # set if a stage fails fatally

    # ── Post-chapter ────────────────────────────────────
    extracted_facts: list[dict] = field(default_factory=list)
    character_updates: list[dict] = field(default_factory=list)
    new_foreshadows: list[dict] = field(default_factory=list)
    resolved_foreshadows: list[str] = field(default_factory=list)
    reader_metrics: dict = field(default_factory=dict)

    def has_high_severity_issues(self) -> bool:
        return any(i.severity == IssueSeverity.HIGH for i in self.issues)

    def has_medium_or_higher_issues(self) -> bool:
        return any(i.severity in (IssueSeverity.MEDIUM, IssueSeverity.HIGH) for i in self.issues)

    def should_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def to_log_dict(self) -> dict:
        return {
            "novel_id": self.novel_id,
            "chapter_id": self.chapter_id,
            "volume_no": self.volume_no,
            "stage": self.stage,
            "retry_count": self.retry_count,
            "issues_count": len(self.issues),
            "word_count": len(self.final_text or self.edited_text or self.draft_text),
            "error": self.error,
        }
