"""
Unit tests for mcp/foreshadow_mcp.py — state machine logic.
All repo calls mocked — no DB needed.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from mcp.foreshadow_mcp import (
    resolve, format_for_prompt, plant,
    _MIN_LIFESPAN, _DEFAULT_DUE_WINDOW, _COLLECTION_WINDOW,
    BURIED, ACTIVE, DUE, RESOLVED,
)


pytestmark = pytest.mark.unit


class TestPlant:
    def _capture_upsert(self, mock_repo):
        """Return the data dict passed to repo.upsert_foreshadow."""
        return mock_repo.upsert_foreshadow.call_args[0][2]

    def test_auto_computes_due_range_start_for_minor(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc", importance="minor", due_range_end=50)
            data = self._capture_upsert(mock_repo)
        # window=10, max(2, 50-10)=40
        assert data["due_range_start"] == 40
        assert data["due_range_end"] == 50

    def test_auto_computes_due_range_start_for_core(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc", importance="core", due_range_end=200)
            data = self._capture_upsert(mock_repo)
        # window=20, max(2, 200-20)=180
        assert data["due_range_start"] == 180
        assert data["due_range_end"] == 200

    def test_explicit_due_range_start_not_overridden(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc", importance="core", due_range_start=50, due_range_end=200)
            data = self._capture_upsert(mock_repo)
        assert data["due_range_start"] == 50  # explicit value preserved

    def test_no_auto_compute_when_due_range_end_absent(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc", importance="minor")
            data = self._capture_upsert(mock_repo)
        assert data["due_range_start"] is None

    def test_buried_state_via_extra(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc", importance="core", due_range_end=200,
                  extra={"state": "BURIED"})
            data = self._capture_upsert(mock_repo)
        assert data["state"] == BURIED

    def test_extra_state_not_leaked_to_extra_field(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc", importance="core", due_range_end=200,
                  extra={"state": "BURIED", "notes": "test"})
            data = self._capture_upsert(mock_repo)
        assert "state" not in data["extra"]
        assert data["extra"]["notes"] == "test"

    def test_default_state_is_active(self):
        with patch("mcp.foreshadow_mcp.repo") as mock_repo, \
             patch("mcp.foreshadow_mcp._next_seq", return_value=1):
            plant("n1", 1, "desc")
            data = self._capture_upsert(mock_repo)
        assert data["state"] == ACTIVE

    def test_collection_window_constants(self):
        assert _COLLECTION_WINDOW["core"] == 20
        assert _COLLECTION_WINDOW["major"] == 15
        assert _COLLECTION_WINDOW["minor"] == 10


class TestConstants:
    def test_state_values(self):
        assert BURIED == "BURIED"
        assert ACTIVE == "ACTIVE"
        assert DUE == "DUE"
        assert RESOLVED == "RESOLVED"

    def test_min_lifespan(self):
        assert _MIN_LIFESPAN["core"] == 20
        assert _MIN_LIFESPAN["major"] == 10
        assert _MIN_LIFESPAN["minor"] == 3

    def test_default_due_window(self):
        assert _DEFAULT_DUE_WINDOW["core"] == 40
        assert _DEFAULT_DUE_WINDOW["major"] == 25
        assert _DEFAULT_DUE_WINDOW["minor"] == 15


class TestResolve:
    def _mock_fs(self, buried_chapter: int, importance: str) -> dict:
        return {"buried_chapter": buried_chapter, "importance": importance}

    def test_blocks_minor_below_min_lifespan(self):
        # age = 12 - 10 = 2, min = 3 → blocked
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = self._mock_fs(10, "minor")
            resolve("novel1", "C10F1", chapter_no=12)
            mock_repo.transition_foreshadow_state.assert_not_called()

    def test_allows_minor_at_exact_min_lifespan(self):
        # age = 13 - 10 = 3, min = 3 → allowed
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = self._mock_fs(10, "minor")
            resolve("novel1", "C10F1", chapter_no=13)
            mock_repo.transition_foreshadow_state.assert_called_once_with(
                "novel1", "C10F1", RESOLVED, resolve_chapter=13
            )

    def test_allows_minor_above_min_lifespan(self):
        # age = 20 - 10 = 10 > 3 → allowed
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = self._mock_fs(10, "minor")
            resolve("novel1", "C10F1", chapter_no=20)
            mock_repo.transition_foreshadow_state.assert_called_once()

    def test_blocks_major_below_min_lifespan(self):
        # age = 8 - 1 = 7, min = 10 → blocked
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = self._mock_fs(1, "major")
            resolve("novel1", "C1F1", chapter_no=8)
            mock_repo.transition_foreshadow_state.assert_not_called()

    def test_blocks_core_below_min_lifespan(self):
        # age = 15 - 1 = 14, min = 20 → blocked
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = self._mock_fs(1, "core")
            resolve("novel1", "C1F1", chapter_no=15)
            mock_repo.transition_foreshadow_state.assert_not_called()

    def test_allows_core_at_min_lifespan(self):
        # age = 21 - 1 = 20, min = 20 → allowed
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = self._mock_fs(1, "core")
            resolve("novel1", "C1F1", chapter_no=21)
            mock_repo.transition_foreshadow_state.assert_called_once()

    def test_missing_foreshadow_still_calls_transition(self):
        # If get_foreshadow returns None, skip age check and just resolve
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = None
            resolve("novel1", "C1F1", chapter_no=5)
            mock_repo.transition_foreshadow_state.assert_called_once()

    def test_uses_chapter_no_as_buried_when_missing(self):
        # buried_chapter is None → age = chapter_no - chapter_no = 0
        # For minor: 0 < 3 → blocked
        with patch("mcp.foreshadow_mcp.repo") as mock_repo:
            mock_repo.get_foreshadow.return_value = {
                "buried_chapter": None, "importance": "minor"
            }
            resolve("novel1", "C5F1", chapter_no=5)
            mock_repo.transition_foreshadow_state.assert_not_called()


class TestFormatForPrompt:
    def _fs(self, fsid, desc, importance="minor", buried=1):
        return {"fshadow_id": fsid, "description": desc,
                "importance": importance, "buried_chapter": buried}

    def test_no_foreshadows_returns_placeholder(self):
        with patch("mcp.foreshadow_mcp.get_active", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_due", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_overdue", return_value=[]):
            assert format_for_prompt("n1", 10) == "无待处理伏笔"

    def test_active_foreshadow_appears(self):
        active = [self._fs("C1F1", "主角的秘密", "core")]
        with patch("mcp.foreshadow_mcp.get_active", return_value=active), \
             patch("mcp.foreshadow_mcp.get_due", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_overdue", return_value=[]):
            result = format_for_prompt("n1", 10)
        assert "C1F1" in result
        assert "主角的秘密" in result

    def test_overdue_section_present(self):
        overdue = [self._fs("C1F1", "必须回收")]
        with patch("mcp.foreshadow_mcp.get_active", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_due", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_overdue", return_value=overdue):
            result = format_for_prompt("n1", 20)
        assert "逾期" in result or "⚠️" in result

    def test_due_section_present(self):
        due = [self._fs("C2F1", "本章回收伏笔")]
        with patch("mcp.foreshadow_mcp.get_active", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_due", return_value=due), \
             patch("mcp.foreshadow_mcp.get_overdue", return_value=[]):
            result = format_for_prompt("n1", 10)
        assert "C2F1" in result

    def test_active_sorted_core_before_minor(self):
        active = [
            self._fs("Fminor", "minor伏笔", "minor"),
            self._fs("Fcore", "core伏笔", "core"),
            self._fs("Fmajor", "major伏笔", "major"),
        ]
        with patch("mcp.foreshadow_mcp.get_active", return_value=active), \
             patch("mcp.foreshadow_mcp.get_due", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_overdue", return_value=[]):
            result = format_for_prompt("n1", 10)
        assert result.index("core伏笔") < result.index("major伏笔") < result.index("minor伏笔")

    def test_caps_active_display_at_8(self):
        active = [self._fs(f"C1F{i}", f"伏笔{i}", "minor") for i in range(12)]
        with patch("mcp.foreshadow_mcp.get_active", return_value=active), \
             patch("mcp.foreshadow_mcp.get_due", return_value=[]), \
             patch("mcp.foreshadow_mcp.get_overdue", return_value=[]):
            result = format_for_prompt("n1", 10)
        assert "另有" in result and "4 个次要伏笔未显示" in result
