"""
Unit tests for pipeline/run_novel.py — _volume_literary_review (Feature 4).
"""
import json
import pytest
from unittest.mock import patch, MagicMock, call

pytestmark = pytest.mark.unit


# Import the private helper — we test it directly.
from pipeline.run_novel import _volume_literary_review


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_summaries(n: int = 3):
    return [
        {"chapter_no": i, "summary_text": f"第{i}章摘要内容", "word_count": 1000}
        for i in range(1, n + 1)
    ]


def _make_critic_result():
    return {
        "critiques": [
            {
                "dimension": "人物弧线",
                "problem": "主角成长过于平滑",
                "suggestion": "在第X章加入挫折场景",
            },
            {
                "dimension": "情绪节奏",
                "problem": "连续5章都是平稳推进",
                "suggestion": "安排一次突发危机",
            },
        ]
    }


def _make_synthesis_result():
    return {
        "next_volume_guidance": [
            "主角应遭遇重大挫折以触发成长",
            "至少安排一次高强度对抗场景",
            "收束第1卷遗留的核心伏笔",
        ]
    }


# ─── tests ──────────────────────────────────────────────────────────────────

class TestVolumeLiteraryReview:
    def _run(self, novel_id="novel-test", volume_no=1,
             summaries=None, volume_plan=None,
             foreshadow_str="", critic=None, synthesis=None):
        """Helper that patches all deps and calls _volume_literary_review."""
        if summaries is None:
            summaries = _make_summaries()
        if volume_plan is None:
            volume_plan = {"volume_goal": "主角完成第一阶段成长"}
        if critic is None:
            critic = _make_critic_result()
        if synthesis is None:
            synthesis = _make_synthesis_result()

        llm_responses = [critic, synthesis]
        llm_call_count = [0]

        def fake_simple_chat_json(**kwargs):
            idx = llm_call_count[0]
            llm_call_count[0] += 1
            return llm_responses[idx] if idx < len(llm_responses) else {}

        import mcp.foreshadow_mcp as fmcp_module
        with (
            patch("pipeline.run_novel.repo") as mock_repo,
            patch("llm.simple_chat_json", side_effect=fake_simple_chat_json) as mock_llm,
            patch.object(fmcp_module, "format_for_prompt", return_value=foreshadow_str),
        ):
            mock_repo.get_recent_summaries.return_value = summaries
            mock_repo.get_world_memory.return_value = volume_plan
            result = _volume_literary_review(novel_id, volume_no)

        return result, mock_repo, mock_llm

    def test_returns_critiques_and_guidance(self):
        result, _, _ = self._run()

        assert "critiques" in result
        assert "next_volume_guidance" in result
        assert len(result["critiques"]) == 2
        assert len(result["next_volume_guidance"]) == 3

    def test_saves_to_world_memory(self):
        result, mock_repo, _ = self._run(novel_id="nv-1", volume_no=2)

        mock_repo.set_world_memory.assert_called_once()
        call_args = mock_repo.set_world_memory.call_args[0]
        assert call_args[0] == "nv-1"
        assert call_args[1] == "volume_review_2"
        saved = call_args[2]
        assert saved["critiques"] == result["critiques"]
        assert saved["next_volume_guidance"] == result["next_volume_guidance"]

    def test_makes_two_llm_calls(self):
        critic = _make_critic_result()
        synthesis = _make_synthesis_result()

        call_count = [0]

        def fake_llm(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            return [critic, synthesis][idx]

        import mcp.foreshadow_mcp as fmcp_module
        with (
            patch("pipeline.run_novel.repo") as mock_repo,
            patch("llm.simple_chat_json", side_effect=fake_llm),
            patch.object(fmcp_module, "format_for_prompt", return_value=""),
        ):
            mock_repo.get_recent_summaries.return_value = _make_summaries()
            mock_repo.get_world_memory.return_value = {"volume_goal": "目标"}
            _volume_literary_review("novel-x", 1)

        assert call_count[0] == 2

    def test_empty_summaries_handled_gracefully(self):
        import mcp.foreshadow_mcp as fmcp_module
        with (
            patch("pipeline.run_novel.repo") as mock_repo,
            patch("llm.simple_chat_json") as mock_llm,
            patch.object(fmcp_module, "format_for_prompt", return_value=""),
        ):
            mock_repo.get_recent_summaries.return_value = []
            mock_repo.get_world_memory.return_value = {}
            mock_llm.side_effect = [
                {"critiques": []},
                {"next_volume_guidance": []},
            ]
            result = _volume_literary_review("novel-empty", 1)

        assert result["critiques"] == []
        assert result["next_volume_guidance"] == []

    def test_critic_result_passed_to_synthesis(self):
        """Synthesis prompt should reference the critic result."""
        critic = _make_critic_result()
        captured_prompts = []

        def fake_llm(**kwargs):
            captured_prompts.append(kwargs.get("user_prompt", ""))
            if len(captured_prompts) == 1:
                return critic
            return {"next_volume_guidance": []}

        import mcp.foreshadow_mcp as fmcp_module
        with (
            patch("pipeline.run_novel.repo") as mock_repo,
            patch("llm.simple_chat_json", side_effect=fake_llm),
            patch.object(fmcp_module, "format_for_prompt", return_value=""),
        ):
            mock_repo.get_recent_summaries.return_value = _make_summaries()
            mock_repo.get_world_memory.return_value = {"volume_goal": "目标"}
            _volume_literary_review("novel-p", 3)

        # Second prompt (synthesis) should contain critic output
        synthesis_prompt = captured_prompts[1]
        assert "人物弧线" in synthesis_prompt or "critiques" in synthesis_prompt

    def test_volume_number_appears_in_critic_prompt(self):
        captured = []

        def fake_llm(**kwargs):
            captured.append(kwargs.get("user_prompt", ""))
            if len(captured) == 1:
                return {"critiques": []}
            return {"next_volume_guidance": []}

        import mcp.foreshadow_mcp as fmcp_module
        with (
            patch("pipeline.run_novel.repo") as mock_repo,
            patch("llm.simple_chat_json", side_effect=fake_llm),
            patch.object(fmcp_module, "format_for_prompt", return_value=""),
        ):
            mock_repo.get_recent_summaries.return_value = _make_summaries()
            mock_repo.get_world_memory.return_value = {"volume_goal": "目标"}
            _volume_literary_review("novel-v", 5)

        # Volume number should appear in the critic prompt
        assert "5" in captured[0]

    def test_fallback_on_llm_error(self):
        """If LLM returns fallback, result still has correct structure."""
        import mcp.foreshadow_mcp as fmcp_module
        with (
            patch("pipeline.run_novel.repo") as mock_repo,
            patch("llm.simple_chat_json") as mock_llm,
            patch.object(fmcp_module, "format_for_prompt", return_value=""),
        ):
            mock_repo.get_recent_summaries.return_value = _make_summaries()
            mock_repo.get_world_memory.return_value = {}
            # Both calls return fallback
            mock_llm.side_effect = [{"critiques": []}, {"next_volume_guidance": []}]
            result = _volume_literary_review("novel-fb", 1)

        assert isinstance(result["critiques"], list)
        assert isinstance(result["next_volume_guidance"], list)
