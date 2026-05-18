"""
Unit tests for db/vector_store.py — range deletion and hybrid reranking.
"""
import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit


class TestDeleteFactsByChapterRange:
    """Tests for delete_facts_by_chapter_range()."""

    def _make_chroma_collection(self, ids=None):
        col = MagicMock()
        col.get.return_value = {"ids": ids or [], "metadatas": []}
        return col

    def test_deletes_ids_in_range(self):
        from db.vector_store import delete_facts_by_chapter_range
        col = self._make_chroma_collection(ids=["id1", "id2", "id3"])
        with patch("db.vector_store._get_chroma") as mock_chroma, \
             patch("db.vector_store._get_embedder"):
            client = MagicMock()
            client.get_collection.return_value = col
            mock_chroma.return_value = client
            count = delete_facts_by_chapter_range("novel1", 10, 20)
        assert count == 3
        col.delete.assert_called_once_with(ids=["id1", "id2", "id3"])

    def test_returns_zero_when_no_ids(self):
        from db.vector_store import delete_facts_by_chapter_range
        col = self._make_chroma_collection(ids=[])
        with patch("db.vector_store._get_chroma") as mock_chroma:
            client = MagicMock()
            client.get_collection.return_value = col
            mock_chroma.return_value = client
            count = delete_facts_by_chapter_range("novel1", 10, 20)
        assert count == 0
        col.delete.assert_not_called()

    def test_returns_zero_when_collection_missing(self):
        from db.vector_store import delete_facts_by_chapter_range
        with patch("db.vector_store._get_chroma") as mock_chroma:
            client = MagicMock()
            client.get_collection.side_effect = Exception("not found")
            mock_chroma.return_value = client
            count = delete_facts_by_chapter_range("novel1", 10, 20)
        assert count == 0

    def test_returns_zero_when_chroma_unavailable(self):
        from db.vector_store import delete_facts_by_chapter_range
        with patch("db.vector_store._get_chroma", return_value=None):
            count = delete_facts_by_chapter_range("novel1", 10, 20)
        assert count == 0

    def test_passes_correct_where_filter(self):
        from db.vector_store import delete_facts_by_chapter_range
        col = self._make_chroma_collection(ids=[])
        with patch("db.vector_store._get_chroma") as mock_chroma:
            client = MagicMock()
            client.get_collection.return_value = col
            mock_chroma.return_value = client
            delete_facts_by_chapter_range("novel1", 5, 15)
        call_kwargs = col.get.call_args[1]
        where = call_kwargs["where"]
        assert "$and" in where
        conditions = where["$and"]
        assert {"novel_id": "novel1"} in conditions
        assert {"chapter_no": {"$gte": 5}} in conditions
        assert {"chapter_no": {"$lte": 15}} in conditions


class TestDeleteSummariesByChapterRange:
    """Tests for delete_summaries_by_chapter_range()."""

    def test_deletes_summary_ids(self):
        from db.vector_store import delete_summaries_by_chapter_range
        col = MagicMock()
        col.get.return_value = {"ids": ["s1", "s2"]}
        with patch("db.vector_store._get_chroma") as mock_chroma:
            client = MagicMock()
            client.get_collection.return_value = col
            mock_chroma.return_value = client
            count = delete_summaries_by_chapter_range("novel1", 1, 20)
        assert count == 2
        col.delete.assert_called_once_with(ids=["s1", "s2"])

    def test_returns_zero_when_chroma_unavailable(self):
        from db.vector_store import delete_summaries_by_chapter_range
        with patch("db.vector_store._get_chroma", return_value=None):
            count = delete_summaries_by_chapter_range("novel1", 1, 20)
        assert count == 0

    def test_graceful_exception(self):
        from db.vector_store import delete_summaries_by_chapter_range
        with patch("db.vector_store._get_chroma") as mock_chroma:
            client = MagicMock()
            client.get_collection.return_value = MagicMock(
                get=MagicMock(side_effect=RuntimeError("db error"))
            )
            mock_chroma.return_value = client
            count = delete_summaries_by_chapter_range("novel1", 1, 20)
        assert count == 0


class TestHybridReranking:
    """Tests for hybrid cosine + character recall reranking in search_facts()."""

    def _mock_chroma_results(self, hits):
        """hits: list of (fact_text, keywords, chapter_no, cosine_distance)"""
        metadatas = [{"fact_type": "plot", "fact_text": h[0],
                      "keywords": h[1], "chapter_no": h[2], "novel_id": "n1"}
                     for h in hits]
        distances = [h[3] for h in hits]
        return {
            "metadatas": [metadatas],
            "distances": [distances],
            "ids": [[f"id{i}" for i in range(len(hits))]],
        }

    def _mock_embedder(self):
        """Return an embedder mock whose .encode().tolist() works correctly."""
        enc_result = MagicMock()
        enc_result.tolist.return_value = [[0.1] * 10]
        embedder = MagicMock()
        embedder.encode.return_value = enc_result
        return embedder

    def test_chinese_char_match_boosts_score(self):
        from db.vector_store import search_facts
        # Two hits with equal cosine, but only first contains query chars
        raw_hits = [
            ("陆铮击败了苏婉", "陆铮 苏婉", 1, 0.2),   # cosine=0.8, has query chars
            ("无关内容 random text", "无关", 2, 0.2),   # cosine=0.8, no query chars
        ]
        col = MagicMock()
        col.query.return_value = self._mock_chroma_results(raw_hits)
        with patch("db.vector_store._get_chroma") as mc, \
             patch("db.vector_store._get_embedder") as me:
            client = MagicMock()
            client.get_collection.return_value = col
            mc.return_value = client
            me.return_value = self._mock_embedder()
            results = search_facts("n1", "陆铮苏婉")
        # First result must be ranked higher (has matching chars)
        assert len(results) >= 2
        assert results[0]["fact_text"] == "陆铮击败了苏婉"
        assert results[0]["score"] > results[1]["score"]

    def test_no_chinese_chars_in_query_skips_boost(self):
        """ASCII-only query should still return results without boost."""
        from db.vector_store import search_facts
        raw_hits = [("some fact", "kw", 1, 0.3)]
        col = MagicMock()
        col.query.return_value = self._mock_chroma_results(raw_hits)
        with patch("db.vector_store._get_chroma") as mc, \
             patch("db.vector_store._get_embedder") as me:
            client = MagicMock()
            client.get_collection.return_value = col
            mc.return_value = client
            me.return_value = self._mock_embedder()
            results = search_facts("n1", "hello world")
        assert len(results) == 1
        # No Chinese chars → no hybrid boost, score = 1.0 - distance = 0.7
        assert results[0]["score"] == pytest.approx(0.7, abs=0.05)
