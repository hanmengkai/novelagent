"""
Unit tests for agents/checker.py helper functions.
LLM calls are mocked; pure helpers tested directly.
"""
import pytest
from unittest.mock import patch, MagicMock
from agents.checker import (
    _format_char_profiles,
    _format_semantic_facts,
    _check_character_arc_stage,
    _check_foreshadow_resolution_quality,
    _check_outline_compliance,
    _check_ending_quality,
    _is_ending_chapter,
)
from langgraph_engine.state import (
    NovelState, ChapterPlan, IssueSeverity, ChapterIssue,
)

pytestmark = pytest.mark.unit


# ── fixtures ────────────────────────────────────────────────


def _state(**kwargs) -> NovelState:
    defaults = dict(
        novel_id="test-novel",
        chapter_id=5,
        volume_no=1,
        memory_snapshot={},
        recent_summaries=[],
        active_characters=[],
    )
    defaults.update(kwargs)
    return NovelState(**defaults)


def _plan(**kwargs) -> ChapterPlan:
    defaults = dict(
        title="测试章节",
        goal="推进剧情",
        key_scenes=["场景A"],
        key_characters=["主角"],
        must_include=["关键事件"],
        must_avoid=["禁止内容"],
        conflict_setup="主角遭遇强敌",
        foreshadow_ops=[],
    )
    defaults.update(kwargs)
    return ChapterPlan(**defaults)


# ── _format_char_profiles ───────────────────────────────────


class TestFormatCharProfiles:
    def test_empty_returns_placeholder(self):
        assert _format_char_profiles([]) == "无人物档案"

    def test_formats_single_char(self):
        char = {
            "name": "张伟", "power_level": "筑基期",
            "location": "山洞", "status": "alive", "emotion_state": "平静",
        }
        result = _format_char_profiles([char])
        assert "张伟" in result
        assert "筑基期" in result
        assert "山洞" in result

    def test_uses_char_id_when_no_name(self):
        char = {
            "char_id": "hero_001", "power_level": "凝气期",
            "location": "城市", "status": "alive", "emotion_state": "紧张",
        }
        result = _format_char_profiles([char])
        assert "hero_001" in result

    def test_multiple_chars(self):
        chars = [
            {"name": f"角色{i}", "power_level": "?", "location": "?", "status": "alive", "emotion_state": "?"}
            for i in range(3)
        ]
        result = _format_char_profiles(chars)
        for i in range(3):
            assert f"角色{i}" in result


# ── _format_semantic_facts ──────────────────────────────────


class TestFormatSemanticFacts:
    def test_empty_returns_placeholder(self):
        assert _format_semantic_facts([]) == "（无语义相关事实）"

    def test_formats_facts(self):
        facts = [{"chapter_no": 3, "score": 0.91, "fact_type": "character", "fact_text": "张伟获得秘籍"}]
        result = _format_semantic_facts(facts)
        assert "ch3" in result
        assert "0.91" in result
        assert "张伟获得秘籍" in result

    def test_truncates_long_fact(self):
        long_text = "很长的事实描述" * 50
        facts = [{"chapter_no": 1, "score": 0.8, "fact_type": "event", "fact_text": long_text}]
        result = _format_semantic_facts(facts)
        assert len(result) < len(long_text)


# ── _check_character_arc_stage ──────────────────────────────


class TestCheckCharacterArcStage:
    def _char_at_stage(self, stage: str) -> dict:
        return {
            "name": "主角",
            "extra": {"goal": "成为最强", "arc_stage": stage},
        }

    def test_no_protagonist_returns_empty(self):
        state = _state(active_characters=[{"name": "路人", "extra": {}}])
        assert _check_character_arc_stage(state, "无关文本") == []

    def test_non_metamorphosis_stage_returns_empty(self):
        state = _state(active_characters=[self._char_at_stage("初心")])
        assert _check_character_arc_stage(state, "我不行了") == []

    def test_metamorphosis_with_naive_keyword_returns_issue(self):
        state = _state(active_characters=[self._char_at_stage("蜕变")])
        issues = _check_character_arc_stage(state, "主角叹道：果然我不行。")
        assert len(issues) == 1
        assert issues[0].code == "CHARACTER_ARC_REGRESSION"
        assert issues[0].severity == IssueSeverity.MEDIUM

    def test_metamorphosis_without_naive_keyword_returns_empty(self):
        state = _state(active_characters=[self._char_at_stage("蜕变")])
        assert _check_character_arc_stage(state, "主角奋力冲破束缚，突破极限。") == []


# ── _check_foreshadow_resolution_quality ───────────────────


class TestCheckForeshadowResolutionQuality:
    def test_no_plan_returns_empty(self):
        state = _state()
        assert _check_foreshadow_resolution_quality(state, "任意文本") == []

    def test_no_resolve_ops_returns_empty(self):
        plan = _plan(foreshadow_ops=[{"op": "plant", "id": "fsh_001"}])
        state = _state(chapter_plan=plan)
        assert _check_foreshadow_resolution_quality(state, "任意文本") == []

    def test_resolve_op_present_but_foreshadow_absent(self):
        plan = _plan(foreshadow_ops=[{"op": "resolve", "id": "fsh_001"}])
        state = _state(chapter_plan=plan)
        due = [{"fshadow_id": "fsh_001", "description": "神秘古剑现世预言"}]
        with patch("agents.checker.foreshadow_mcp.get_due", return_value=due):
            issues = _check_foreshadow_resolution_quality(state, "完全无关的文字内容在这里")
        assert any(i.code == "FORESHADOW_RESOLUTION_ABSENT" for i in issues)

    def test_resolve_op_with_anchor_but_no_emotion(self):
        plan = _plan(foreshadow_ops=[{"op": "resolve", "id": "fsh_001"}])
        state = _state(chapter_plan=plan)
        # description produces anchors: ["神秘古剑现世", "预言"] via _CHINESE_WORD_RE
        due = [{"fshadow_id": "fsh_001", "description": "神秘古剑现世预言"}]
        # "神秘古剑现世" anchor is present; none of EMOTION_KEYWORDS present → WEAK
        text = "神秘古剑现世了，他拿起来看了看，放下了，毫无波澜。"
        with patch("agents.checker.foreshadow_mcp.get_due", return_value=due):
            issues = _check_foreshadow_resolution_quality(state, text)
        assert any(i.code == "FORESHADOW_RESOLUTION_WEAK" for i in issues)

    def test_resolve_op_with_anchor_and_emotion_passes(self):
        plan = _plan(foreshadow_ops=[{"op": "resolve", "id": "fsh_001"}])
        state = _state(chapter_plan=plan)
        due = [{"fshadow_id": "fsh_001", "description": "神秘古剑现世预言"}]
        text = "古剑现世，他心中涌起难以言说的颤抖，泪水模糊了视线。"
        with patch("agents.checker.foreshadow_mcp.get_due", return_value=due):
            issues = _check_foreshadow_resolution_quality(state, text)
        weak = [i for i in issues if i.code == "FORESHADOW_RESOLUTION_WEAK"]
        assert len(weak) == 0


# ── _check_outline_compliance ───────────────────────────────


class TestCheckOutlineCompliance:
    def _outline_with_event(self, chapter_no: int, key_event: str) -> dict:
        return {
            "chapter_outlines": [
                {"chapter_no": chapter_no, "key_event": key_event, "conflict": "", "goal": ""}
            ]
        }

    def test_no_volume_plan_returns_empty(self):
        state = _state(chapter_id=5, volume_no=1, edited_text="有文字")
        with patch("agents.checker.repo.get_world_memory", return_value={}):
            assert _check_outline_compliance(state) == []

    def test_key_event_present_passes(self):
        # key_event splits on "，" → ["主角击败首领", "取得胜利"]; both must appear literally in text
        state = _state(chapter_id=5, volume_no=1, edited_text="主角击败首领，取得胜利，众人欢呼。")
        vplan = self._outline_with_event(5, "主角击败首领，取得胜利")
        with patch("agents.checker.repo.get_world_memory", return_value=vplan):
            issues = _check_outline_compliance(state)
        assert not any(i.code == "OUTLINE_KEY_EVENT_MISSING" for i in issues)

    def test_key_event_absent_reports_issue(self):
        state = _state(chapter_id=5, volume_no=1, edited_text="主角在山中修炼，无事发生。")
        vplan = self._outline_with_event(5, "主角击败首领，夺取神器宝物")
        with patch("agents.checker.repo.get_world_memory", return_value=vplan):
            issues = _check_outline_compliance(state)
        assert any(i.code == "OUTLINE_KEY_EVENT_MISSING" for i in issues)
        assert any(i.severity == IssueSeverity.HIGH for i in issues)


# ── _check_ending_quality ───────────────────────────────────


class TestCheckEndingQuality:
    def _story_outline(self, world_type: str) -> dict:
        return {"world_type": world_type}

    def test_short_text_reports_high_issue(self):
        state = _state(volume_no=1)
        text = "结局" * 100  # < 4000 chars
        with patch("agents.checker.repo.get_world_memory", return_value=self._story_outline("末世")):
            with patch("agents.checker.foreshadow_mcp.get_all_unresolved", return_value=[]):
                issues = _check_ending_quality(state, text)
        assert any(i.code == "ENDING_TOO_SHORT" for i in issues)
        assert any(i.severity == IssueSeverity.HIGH for i in issues)

    def test_missing_emotion_beat_reports_medium(self):
        state = _state(volume_no=1)
        text = "很长的结局文字" * 1000  # >= 4000 chars, no acceptance/closure/reflection
        with patch("agents.checker.repo.get_world_memory", return_value=self._story_outline("玄幻")):
            with patch("agents.checker.foreshadow_mcp.get_all_unresolved", return_value=[]):
                issues = _check_ending_quality(state, text)
        assert any(i.code == "ENDING_EMOTION_MISSING" for i in issues)

    def test_unresolved_core_foreshadow_reports_high(self):
        state = _state(volume_no=1)
        text = "释然。告别。回想往事。未来充满希望，重建家园，明天将更加光明。" * 200
        core_fsh = [{"fshadow_id": "fsh_core", "importance": "core", "description": "主角的身世之谜"}]
        with patch("agents.checker.repo.get_world_memory", return_value=self._story_outline("末世")):
            with patch("agents.checker.foreshadow_mcp.get_all_unresolved", return_value=core_fsh):
                issues = _check_ending_quality(state, text)
        assert any(i.code == "ENDING_FORESHADOW_UNRESOLVED" for i in issues)

    def test_good_ending_has_no_critical_issues(self):
        state = _state(volume_no=1)
        text = (
            "他终于释然了，放下了过去的一切，接受命运的安排。"
            "告别了战友，回想起曾经一路走来的历程，心中满是感慨。"
            "未来充满希望，重建家园，明天将更加光明，新生活即将开始。"
        ) * 80  # >= 4000 chars
        with patch("agents.checker.repo.get_world_memory", return_value=self._story_outline("末世")):
            with patch("agents.checker.foreshadow_mcp.get_all_unresolved", return_value=[]):
                issues = _check_ending_quality(state, text)
        high_issues = [i for i in issues if i.severity == IssueSeverity.HIGH]
        assert len(high_issues) == 0
