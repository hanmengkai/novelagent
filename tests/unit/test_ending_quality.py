"""
Unit tests for agents/checker.py — ending chapter quality gate.
"""
import pytest
from unittest.mock import patch, MagicMock
from langgraph_engine.state import NovelState, ChapterIssue, IssueSeverity

# Import the functions under test
from agents.checker import (
    _is_ending_chapter, _check_ending_quality,
    _ENDING_EMOTION_KEYWORDS, _ENDING_POSITIVE_KEYWORDS,
)

pytestmark = pytest.mark.unit


class TestIsEndingChapter:
    def make_state(self, novel_id="n1", volume_no=10, chapter_id=200):
        state = MagicMock(spec=NovelState)
        state.novel_id = novel_id
        state.volume_no = volume_no
        state.chapter_id = chapter_id
        state.error = None
        return state

    def test_last_chapter_of_last_volume_returns_true(self):
        state = self.make_state(volume_no=10, chapter_id=200)
        novel = {"total_volumes": 10}
        outlines = [{"chapter_no": i} for i in range(181, 201)]
        volume_plan = {"chapter_outlines": outlines}
        with patch("agents.checker.repo") as mock_repo:
            mock_repo.get_novel.return_value = novel
            mock_repo.get_world_memory.return_value = volume_plan
            assert _is_ending_chapter(state) is True

    def test_not_last_volume_returns_false(self):
        state = self.make_state(volume_no=9, chapter_id=180)
        novel = {"total_volumes": 10}
        with patch("agents.checker.repo") as mock_repo:
            mock_repo.get_novel.return_value = novel
            assert _is_ending_chapter(state) is False

    def test_not_last_chapter_returns_false(self):
        state = self.make_state(volume_no=10, chapter_id=199)
        novel = {"total_volumes": 10}
        outlines = [{"chapter_no": i} for i in range(181, 201)]
        volume_plan = {"chapter_outlines": outlines}
        with patch("agents.checker.repo") as mock_repo:
            mock_repo.get_novel.return_value = novel
            mock_repo.get_world_memory.return_value = volume_plan
            assert _is_ending_chapter(state) is False

    def test_error_state_returns_false(self):
        state = self.make_state()
        state.error = "something broke"
        novel = {"total_volumes": 10}
        with patch("agents.checker.repo") as mock_repo:
            mock_repo.get_novel.return_value = novel
            assert _is_ending_chapter(state) is False

    def test_no_novel_returns_false(self):
        state = self.make_state()
        with patch("agents.checker.repo") as mock_repo:
            mock_repo.get_novel.return_value = None
            assert _is_ending_chapter(state) is False

    def test_no_outlines_returns_false(self):
        state = self.make_state()
        novel = {"total_volumes": 10}
        with patch("agents.checker.repo") as mock_repo:
            mock_repo.get_novel.return_value = novel
            mock_repo.get_world_memory.return_value = {}
            assert _is_ending_chapter(state) is False


class TestEndingQualityChecks:
    def make_state(self, novel_id="n1"):
        state = MagicMock(spec=NovelState)
        state.novel_id = novel_id
        return state

    def test_good_ending_passes_all_checks(self):
        text = (
            "陆铮站在重建的城市前，心中一片释然。他终于放下了仇恨。\n"
            "这既是结局，也是新的开始。他回首一路走来的历程，感慨万千。\n"
            "希望就在前方，未来充满了光明。新生活即将开始，黎明已经到来。"
        )
        # Pad to 4000+ chars so TOO_SHORT check doesn't fire
        text = text + ("\n人们开始了新生活。城市在重建。明天会更好。" * 200)
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs:
            mock_fs.get_all_unresolved.return_value = []
            issues = _check_ending_quality(state, text)
        assert len(issues) == 0, f"Expected no issues, got: {[(i.code,i.description) for i in issues]}"

    def test_missing_emotional_beats(self):
        text = "战斗结束了。他转身离开。"
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs:
            mock_fs.get_all_unresolved.return_value = []
            issues = _check_ending_quality(state, text)
        codes = {i.code for i in issues}
        assert "ENDING_EMOTION_MISSING" in codes
        emotion_issues = [i for i in issues if i.code == "ENDING_EMOTION_MISSING"]
        assert len(emotion_issues) >= 2

    def test_too_short_fails(self):
        text = "A" * 3000
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs:
            mock_fs.get_all_unresolved.return_value = []
            issues = _check_ending_quality(state, text)
        assert any(i.code == "ENDING_TOO_SHORT" for i in issues)

    def test_long_enough_passes(self):
        text = "A" * 4500
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs:
            mock_fs.get_all_unresolved.return_value = []
            issues = _check_ending_quality(state, text)
        assert not any(i.code == "ENDING_TOO_SHORT" for i in issues)

    def test_lacks_positive_outlook(self):
        text = "一切都结束了。黑暗笼罩大地。再也没有人了。"
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs, \
             patch("agents.checker.repo") as mock_repo:
            mock_fs.get_all_unresolved.return_value = []
            mock_repo.get_world_memory.return_value = {"world_type": "末世重生"}
            issues = _check_ending_quality(state, text)
        assert any(i.code == "ENDING_LACKS_HOPE" for i in issues)

    def test_has_positive_outlook(self):
        text = "希望在未来。重建即将开始。新的黎明就在前方。"
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs, \
             patch("agents.checker.repo") as mock_repo:
            mock_fs.get_all_unresolved.return_value = []
            mock_repo.get_world_memory.return_value = {"world_type": "末世重生"}
            issues = _check_ending_quality(state, text)
        assert not any(i.code == "ENDING_LACKS_HOPE" for i in issues)

    def test_non_apocalyptic_genre_skips_hope_check(self):
        """玄幻/修仙小说不应触发 ENDING_LACKS_HOPE。"""
        text = "他飞升成仙，证道永恒，大道在前。"
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs, \
             patch("agents.checker.repo") as mock_repo:
            mock_fs.get_all_unresolved.return_value = []
            mock_repo.get_world_memory.return_value = {"world_type": "玄幻修仙"}
            issues = _check_ending_quality(state, text)
        assert not any(i.code == "ENDING_LACKS_HOPE" for i in issues)

    def test_unresolved_core_foreshadows(self):
        text = "一切都结束了。他释然地放下武器，回首这段历程，心中充满希望。"
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs:
            mock_fs.get_all_unresolved.return_value = [
                {"importance": "core", "description": "苏婉的背叛真相"},
                {"importance": "core", "description": "天基武器终极秘密"},
                {"importance": "minor", "description": "路边的猫"},
            ]
            issues = _check_ending_quality(state, text)
        assert any(i.code == "ENDING_FORESHADOW_UNRESOLVED" for i in issues)

    def test_all_foreshadows_resolved(self):
        text = "一切都结束了。他释然地放下武器，回首这段历程，心中充满希望。"
        state = self.make_state()
        with patch("agents.checker.foreshadow_mcp") as mock_fs:
            mock_fs.get_all_unresolved.return_value = []
            issues = _check_ending_quality(state, text)
        assert not any(i.code == "ENDING_FORESHADOW_UNRESOLVED" for i in issues)

    def test_keyword_constants_are_defined(self):
        assert len(_ENDING_EMOTION_KEYWORDS) == 3
        assert "acceptance" in _ENDING_EMOTION_KEYWORDS
        assert "closure" in _ENDING_EMOTION_KEYWORDS
        assert "reflection" in _ENDING_EMOTION_KEYWORDS
        assert len(_ENDING_POSITIVE_KEYWORDS) >= 8
