"""
Unit tests for narrative/arc_planner.py — Cross-volume arc planning.
"""
import pytest
from unittest.mock import patch, MagicMock

from narrative.arc_planner import init_arc_plan, get_arc_guidance, update_arc_progress

pytestmark = pytest.mark.unit


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_arc_plan(n_volumes: int = 3) -> dict:
    return {
        "arc_plan": [
            {
                "volume_no": i,
                "arc_role": f"第{i}卷叙事职责",
                "must_setup": [f"元素{i}a", f"元素{i}b"],
                "must_resolve": [] if i == 1 else [f"遗留{i - 1}"],
                "protagonist_state_target": f"主角第{i}卷末状态",
                "emotional_peak": f"第{i}卷情绪高峰",
            }
            for i in range(1, n_volumes + 1)
        ]
    }


# ─── init_arc_plan ───────────────────────────────────────────────────────────

class TestInitArcPlan:
    def test_returns_arc_plan_dict(self):
        expected = _make_arc_plan(3)
        with (
            patch("narrative.arc_planner.repo") as mock_repo,
            patch("narrative.arc_planner.simple_chat_json") as mock_llm,
        ):
            mock_repo.get_world_memory.return_value = {"story_title": "测试小说", "act_structure": []}
            mock_llm.return_value = expected
            result = init_arc_plan("novel-1")

        assert result == expected
        mock_repo.set_world_memory.assert_called_once_with(
            "novel-1", "cross_volume_arc_plan", expected
        )

    def test_empty_story_outline_returns_empty_dict(self):
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = {}
            result = init_arc_plan("novel-empty")

        assert result == {}

    def test_none_story_outline_returns_empty_dict(self):
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = None
            result = init_arc_plan("novel-none")

        assert result == {}

    def test_llm_prompt_includes_story_outline(self):
        outline = {"story_title": "英雄传", "core_theme": "热血", "act_structure": []}
        with (
            patch("narrative.arc_planner.repo") as mock_repo,
            patch("narrative.arc_planner.simple_chat_json") as mock_llm,
        ):
            mock_repo.get_world_memory.return_value = outline
            mock_llm.return_value = {"arc_plan": []}
            init_arc_plan("novel-2")

        call_kwargs = mock_llm.call_args
        user_prompt = call_kwargs[1]["user_prompt"] if call_kwargs[1] else call_kwargs[0][1]
        assert "英雄传" in user_prompt or "story_title" in user_prompt


# ─── get_arc_guidance ────────────────────────────────────────────────────────

class TestGetArcGuidance:
    def test_returns_formatted_string_for_existing_volume(self):
        arc_data = _make_arc_plan(3)
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = arc_data
            guidance = get_arc_guidance("novel-1", 2)

        assert "第2卷叙事职责" in guidance
        assert "元素2a" in guidance
        assert "遗留1" in guidance
        assert "主角第2卷末状态" in guidance
        assert "第2卷情绪高峰" in guidance

    def test_returns_empty_string_when_no_arc_plan(self):
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = None
            guidance = get_arc_guidance("novel-1", 1)

        assert guidance == ""

    def test_returns_empty_string_when_arc_plan_is_empty_list(self):
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = {"arc_plan": []}
            guidance = get_arc_guidance("novel-1", 1)

        assert guidance == ""

    def test_returns_empty_string_for_missing_volume(self):
        arc_data = _make_arc_plan(2)
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = arc_data
            guidance = get_arc_guidance("novel-1", 99)

        assert guidance == ""

    def test_volume_1_has_empty_must_resolve(self):
        arc_data = _make_arc_plan(3)
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = arc_data
            guidance = get_arc_guidance("novel-1", 1)

        # Volume 1 must_resolve is [] so that line should not appear
        assert "必须收束" not in guidance

    def test_reads_cross_volume_arc_plan_key(self):
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = None
            get_arc_guidance("novel-1", 1)

        mock_repo.get_world_memory.assert_called_once_with("novel-1", "cross_volume_arc_plan")


# ─── update_arc_progress ─────────────────────────────────────────────────────

class TestUpdateArcProgress:
    def test_marks_volume_as_completed(self):
        arc_data = _make_arc_plan(3)
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.side_effect = lambda nid, key: (
                arc_data if key == "cross_volume_arc_plan" else "卷末主角昂然而立，新的旅程开始了。"
            )
            update_arc_progress("novel-1", 2)

        saved = mock_repo.set_world_memory.call_args[0][2]
        vol2 = next(v for v in saved["arc_plan"] if v["volume_no"] == 2)
        assert vol2["completed"] is True

    def test_records_actual_ending(self):
        arc_data = _make_arc_plan(3)
        ending_text = "他回望来时的路，心中满是感慨。" * 10  # 100+ chars
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.side_effect = lambda nid, key: (
                arc_data if key == "cross_volume_arc_plan" else ending_text
            )
            update_arc_progress("novel-1", 1)

        saved = mock_repo.set_world_memory.call_args[0][2]
        vol1 = next(v for v in saved["arc_plan"] if v["volume_no"] == 1)
        assert "actual_ending" in vol1
        assert len(vol1["actual_ending"]) <= 200

    def test_noop_when_no_arc_plan(self):
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.return_value = None
            update_arc_progress("novel-1", 1)

        mock_repo.set_world_memory.assert_not_called()

    def test_noop_when_volume_not_in_plan(self):
        arc_data = _make_arc_plan(2)
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.side_effect = lambda nid, key: (
                arc_data if key == "cross_volume_arc_plan" else "ending text"
            )
            update_arc_progress("novel-1", 99)

        mock_repo.set_world_memory.assert_not_called()

    def test_other_volumes_unchanged(self):
        arc_data = _make_arc_plan(3)
        with patch("narrative.arc_planner.repo") as mock_repo:
            mock_repo.get_world_memory.side_effect = lambda nid, key: (
                arc_data if key == "cross_volume_arc_plan" else "ending"
            )
            update_arc_progress("novel-1", 1)

        saved = mock_repo.set_world_memory.call_args[0][2]
        vol2 = next(v for v in saved["arc_plan"] if v["volume_no"] == 2)
        assert "completed" not in vol2
        vol3 = next(v for v in saved["arc_plan"] if v["volume_no"] == 3)
        assert "completed" not in vol3
