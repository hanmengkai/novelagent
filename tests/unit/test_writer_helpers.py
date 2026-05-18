"""
Unit tests for agents/writer.py helper functions.
All tested functions are pure (no LLM calls, no I/O).
"""
import pytest
from agents.writer import (
    _filter_characters,
    _format_character_info,
    _format_recent_summaries,
    _format_scenes,
    _strip_meta_headers,
    _vol_in_act,
    _format_foreshadow_ops,
    _format_semantic_context,
    _build_emotion_directive,
)
from langgraph_engine.state import NovelState, NarrativeDirective, ArcPhase, ChapterPlan

pytestmark = pytest.mark.unit


# ── fixtures ────────────────────────────────────────────────


def _make_char(**kwargs):
    base = {
        "name": "张伟",
        "char_id": "zhang_wei",
        "power_level": "筑基期",
        "location": "山洞",
        "emotion_state": "平静",
        "physical_state": "正常",
        "personality": ["刚毅", "沉默"],
        "relationships": {"李明": "竞争对手"},
        "backstory": "出身贫寒，自幼习武",
        "emotion_expression": {"anger": "拳头紧握", "speech_style": "简短有力"},
    }
    base.update(kwargs)
    return base


def _make_state(**kwargs):
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


# ── _filter_characters ──────────────────────────────────────


class TestFilterCharacters:
    def test_empty_key_chars_returns_first_6(self):
        chars = [_make_char(name=f"角色{i}", char_id=f"char{i}") for i in range(10)]
        result = _filter_characters(chars, [])
        assert len(result) == 6

    def test_matches_by_name(self):
        chars = [_make_char(name="张伟"), _make_char(name="李明")]
        result = _filter_characters(chars, ["张伟"])
        assert len(result) == 1
        assert result[0]["name"] == "张伟"

    def test_matches_by_char_id(self):
        chars = [_make_char(name="张伟", char_id="hero"), _make_char(name="李明", char_id="villain")]
        result = _filter_characters(chars, ["hero"])
        assert result[0]["char_id"] == "hero"

    def test_no_match_falls_back_to_first_4(self):
        chars = [_make_char(name=f"角色{i}", char_id=f"char{i}") for i in range(6)]
        result = _filter_characters(chars, ["不存在的角色"])
        assert len(result) == 4

    def test_key_chars_all_empty_strings(self):
        chars = [_make_char(name=f"角色{i}") for i in range(3)]
        result = _filter_characters(chars, ["", ""])
        assert len(result) == 3

    def test_partial_name_match(self):
        chars = [_make_char(name="张伟强"), _make_char(name="李明")]
        result = _filter_characters(chars, ["张伟"])
        assert any(c["name"] == "张伟强" for c in result)


# ── _format_character_info ──────────────────────────────────


class TestFormatCharacterInfo:
    def test_empty_returns_placeholder(self):
        assert _format_character_info([]) == "无人物信息"

    def test_single_char_early_chapter(self):
        result = _format_character_info([_make_char()], chapter_id=1)
        assert "张伟" in result
        assert "筑基期" in result
        assert "山洞" in result

    def test_late_chapter_trims_backstory(self):
        char = _make_char(backstory="这是一段非常长的人物背景描述" * 10)
        early = _format_character_info([char], chapter_id=1)
        late = _format_character_info([char], chapter_id=31)
        assert len(late) < len(early)

    def test_late_chapter_strips_relationship_values(self):
        char = _make_char(relationships={"盟友": "一起战斗过", "敌人": "杀父仇人"})
        early = _format_character_info([char], chapter_id=1)
        late = _format_character_info([char], chapter_id=31)
        assert "一起战斗过" in early
        assert "一起战斗过" not in late

    def test_emotion_expression_rendered(self):
        result = _format_character_info([_make_char()], chapter_id=1)
        assert "拳头紧握" in result


# ── _format_recent_summaries ────────────────────────────────


class TestFormatRecentSummaries:
    def test_empty_returns_placeholder(self):
        assert _format_recent_summaries([]) == "（无近期摘要）"

    def test_shows_last_3(self):
        summaries = [{"chapter_no": i, "summary_text": f"第{i}章摘要"} for i in range(1, 6)]
        result = _format_recent_summaries(summaries)
        assert "第3章" in result
        assert "第4章" in result
        assert "第5章" in result
        assert "第1章" not in result

    def test_truncates_long_summary(self):
        long_summary = "很长的摘要" * 100
        summaries = [{"chapter_no": 1, "summary_text": long_summary}]
        result = _format_recent_summaries(summaries)
        assert len(result) < len(long_summary)


# ── _format_scenes ──────────────────────────────────────────


class TestFormatScenes:
    def test_empty_returns_placeholder(self):
        assert _format_scenes([]) == "（无具体场景安排）"

    def test_dict_scenes(self):
        scenes = [{"scene_no": 1, "description": "激烈战斗", "emotion": "紧张", "emotion_technique": "意识流"}]
        result = _format_scenes(scenes)
        assert "激烈战斗" in result
        assert "紧张" in result
        assert "意识流" in result

    def test_string_scenes(self):
        result = _format_scenes(["场景描述A", "场景描述B"])
        assert "场景描述A" in result
        assert "场景描述B" in result

    def test_scene_without_technique(self):
        scenes = [{"scene_no": 1, "description": "静默", "emotion": "平静"}]
        result = _format_scenes(scenes)
        assert "技法" not in result


# ── _strip_meta_headers ─────────────────────────────────────


class TestStripMetaHeaders:
    def test_removes_h1_headers(self):
        text = "# 第49章《暗流涌动》\n这是正文内容。"
        result = _strip_meta_headers(text)
        assert "第49章" not in result
        assert "这是正文内容" in result

    def test_removes_h2_headers(self):
        text = "## 前半部分\n正文。\n## 后半部分\n续写。"
        result = _strip_meta_headers(text)
        assert "前半部分" not in result
        assert "后半部分" not in result
        assert "正文" in result

    def test_removes_meta_labels(self):
        for label in ["前半部分", "后半部分", "正文开始", "正文："]:
            result = _strip_meta_headers(label + "\n正文内容")
            assert label not in result

    def test_preserves_normal_content(self):
        text = "他缓缓走入黑暗的房间，心中涌起一阵寒意。"
        assert _strip_meta_headers(text) == text

    def test_empty_string(self):
        assert _strip_meta_headers("") == ""


# ── _vol_in_act ─────────────────────────────────────────────


class TestVolInAct:
    def test_in_range(self):
        assert _vol_in_act(3, "第1-5卷") is True

    def test_out_of_range(self):
        assert _vol_in_act(6, "第1-5卷") is False

    def test_exact_match_single_vol(self):
        assert _vol_in_act(3, "第3卷") is True
        assert _vol_in_act(4, "第3卷") is False

    def test_boundary_values(self):
        assert _vol_in_act(1, "第1-3卷") is True
        assert _vol_in_act(3, "第1-3卷") is True

    def test_invalid_range_returns_false(self):
        assert _vol_in_act(1, "") is False
        assert _vol_in_act(1, "invalid") is False


# ── _format_foreshadow_ops ──────────────────────────────────


class TestFormatForeshadowOps:
    def test_empty_returns_placeholder(self):
        assert _format_foreshadow_ops([]) == "无伏笔操作"

    def test_formats_ops(self):
        ops = [
            {"op": "plant", "description": "神秘剑法出现"},
            {"op": "resolve", "id": "fsh_001", "description": ""},
        ]
        result = _format_foreshadow_ops(ops)
        assert "plant" in result
        assert "神秘剑法出现" in result
        assert "resolve" in result


# ── _format_semantic_context ────────────────────────────────


class TestFormatSemanticContext:
    def test_empty_returns_empty_string(self):
        assert _format_semantic_context([]) == ""

    def test_includes_fact_text(self):
        facts = [{"chapter_no": 3, "score": 0.92, "fact_type": "character", "fact_text": "张伟获得神剑"}]
        result = _format_semantic_context(facts)
        assert "张伟获得神剑" in result
        assert "ch3" in result

    def test_limits_to_8_facts(self):
        facts = [
            {"chapter_no": i, "score": 0.9, "fact_type": "event", "fact_text": f"事件{i}"}
            for i in range(20)
        ]
        result = _format_semantic_context(facts)
        assert result.count("ch") <= 8


# ── _build_emotion_directive ────────────────────────────────


class TestBuildEmotionDirective:
    def test_returns_string_with_arc_info(self):
        state = _make_state(
            memory_snapshot={
                "chapter_arc": "低谷→逆转→高潮",
                "arc_phase": "climax",
            }
        )
        result = _build_emotion_directive(state)
        assert "低谷→逆转→高潮" in result
        assert "短句" in result  # climax phase hint

    def test_uses_fallback_when_no_arc(self):
        state = _make_state()
        result = _build_emotion_directive(state)
        assert "平稳推进" in result
        assert isinstance(result, str)

    def test_uses_narrative_emotion_curve(self):
        nd = NarrativeDirective(
            arc_phase=ArcPhase.BUILDUP,
            emotion_curve="平静→紧张→爆发",
            conflict_intensity=0.7,
            next_chapter_goal="决战",
        )
        state = _make_state(narrative_directive=nd)
        result = _build_emotion_directive(state)
        assert "平静→紧张→爆发" in result
