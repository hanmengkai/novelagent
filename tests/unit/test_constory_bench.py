"""
Unit tests for eval/constory_bench.py — ConStory-Bench evaluation.
"""
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


class TestBuildReport:
    def test_all_dimensions_present(self):
        from eval.constory_bench import _build_report
        raw = {
            "character": {"score": 8, "issues": []},
            "event":     {"score": 7, "issues": ["event conflict"]},
            "setting":   {"score": 9, "issues": []},
            "temporal":  {"score": 6, "issues": ["time skip unclear"]},
            "coherence": {"score": 8, "issues": []},
            "overall_notes": "还不错",
        }
        report = _build_report(1, 1, 20, raw)
        assert set(report["scores"].keys()) == {"character", "event", "setting", "temporal", "coherence"}
        assert report["average_score"] == pytest.approx(7.6, abs=0.05)
        assert report["volume_no"] == 1
        assert report["chapter_range"] == [1, 20]

    def test_passed_when_avg_ge_7(self):
        from eval.constory_bench import _build_report
        raw = {d: {"score": 7.0, "issues": []} for d in
               ["character", "event", "setting", "temporal", "coherence"]}
        raw["overall_notes"] = ""
        report = _build_report(2, 21, 40, raw)
        assert report["passed"] is True

    def test_failed_when_avg_lt_7(self):
        from eval.constory_bench import _build_report
        raw = {d: {"score": 5.0, "issues": []} for d in
               ["character", "event", "setting", "temporal", "coherence"]}
        raw["overall_notes"] = ""
        report = _build_report(2, 21, 40, raw)
        assert report["passed"] is False

    def test_score_clamped_0_to_10(self):
        from eval.constory_bench import _build_report
        raw = {
            "character": {"score": 15, "issues": []},
            "event":     {"score": -3, "issues": []},
            "setting":   {"score": 8,  "issues": []},
            "temporal":  {"score": 8,  "issues": []},
            "coherence": {"score": 8,  "issues": []},
            "overall_notes": "",
        }
        report = _build_report(1, 1, 10, raw)
        assert report["scores"]["character"] == 10.0
        assert report["scores"]["event"] == 0.0

    def test_issues_aggregated_with_labels(self):
        from eval.constory_bench import _build_report
        raw = {
            "character": {"score": 6, "issues": ["角色前后矛盾"]},
            "event":     {"score": 7, "issues": []},
            "setting":   {"score": 7, "issues": []},
            "temporal":  {"score": 7, "issues": []},
            "coherence": {"score": 7, "issues": []},
            "overall_notes": "",
        }
        report = _build_report(1, 1, 10, raw)
        assert any("人物一致性" in i for i in report["issues"])

    def test_summary_contains_volume_and_score(self):
        from eval.constory_bench import _build_report
        raw = {d: {"score": 8.0, "issues": []} for d in
               ["character", "event", "setting", "temporal", "coherence"]}
        raw["overall_notes"] = ""
        report = _build_report(3, 41, 60, raw)
        assert "第3卷" in report["summary"]
        assert "8.0" in report["summary"]

    def test_missing_dimension_defaults_to_5(self):
        from eval.constory_bench import _build_report
        # Only 4 dimensions, missing 'coherence'
        raw = {
            "character": {"score": 8, "issues": []},
            "event":     {"score": 8, "issues": []},
            "setting":   {"score": 8, "issues": []},
            "temporal":  {"score": 8, "issues": []},
            "overall_notes": "",
        }
        report = _build_report(1, 1, 10, raw)
        assert report["scores"]["coherence"] == 5.0


class TestEmptyReport:
    def test_empty_report_structure(self):
        from eval.constory_bench import _empty_report
        report = _empty_report(1, "no data")
        assert report["passed"] is False
        assert report["average_score"] == 0.0
        assert "no data" in report["summary"]
        assert report["volume_no"] == 1
        assert report["chapter_range"] == [0, 0]

    def test_reason_appears_in_summary(self):
        from eval.constory_bench import _empty_report
        report = _empty_report(5, "db timeout")
        assert "db timeout" in report["summary"]


class TestEvaluateVolume:
    def _mock_db_ctx(self, rows):
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_ctx)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_ctx.execute.return_value.fetchall.return_value = rows
        return db_ctx

    def test_returns_empty_report_when_no_summaries(self):
        from eval.constory_bench import evaluate_volume
        db_ctx = self._mock_db_ctx(rows=[])
        with patch("db.repo") as mock_repo, \
             patch("db.json_session.get_db", return_value=db_ctx):
            mock_repo.get_novel.return_value = {"title": "测试书"}
            mock_repo.get_world_memory.return_value = {"world_type": "玄幻"}
            report = evaluate_volume("novel1", 1)
        assert report["passed"] is False
        assert "no chapter summaries" in report["summary"]

    def test_calls_llm_when_summaries_exist(self):
        from eval.constory_bench import evaluate_volume
        rows = [(1, "第1章摘要"), (2, "第2章摘要")]
        db_ctx = self._mock_db_ctx(rows=rows)
        llm_response = {
            "character": {"score": 8, "issues": []},
            "event":     {"score": 8, "issues": []},
            "setting":   {"score": 8, "issues": []},
            "temporal":  {"score": 8, "issues": []},
            "coherence": {"score": 8, "issues": []},
            "overall_notes": "good",
        }
        with patch("db.repo") as mock_repo, \
             patch("db.json_session.get_db", return_value=db_ctx), \
             patch("mcp.memory_mcp") as mock_mem, \
             patch("mcp.foreshadow_mcp") as mock_fs, \
             patch("llm.client.chat_json", return_value=llm_response) as mock_chat:
            mock_repo.get_novel.return_value = {"title": "测试书"}
            mock_repo.get_world_memory.return_value = {"world_type": "玄幻"}
            mock_mem.get_all_characters.return_value = []
            mock_fs.get_all_unresolved.return_value = []
            mock_fs.get_due.return_value = []
            report = evaluate_volume("novel1", 1)
        mock_chat.assert_called_once()
        assert report["average_score"] == pytest.approx(8.0)
        assert report["passed"] is True

    def test_returns_empty_report_on_exception(self):
        from eval.constory_bench import evaluate_volume
        with patch("db.repo") as mock_repo:
            mock_repo.get_novel.side_effect = RuntimeError("db down")
            report = evaluate_volume("novel1", 1)
        assert report["passed"] is False
        assert report["volume_no"] == 1
