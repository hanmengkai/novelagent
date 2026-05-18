"""
Unit tests for db/cache.py — in-memory KV store with TTL and persistence.
All tests use isolated_cache fixture to avoid cross-test state.
"""
import time
import pytest
import db.cache as cache_module


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset(isolated_cache):
    pass


class TestBasicOperations:
    def test_rset_rget_roundtrip(self):
        cache_module.rset("key1", {"data": 123})
        assert cache_module.rget("key1") == {"data": 123}

    def test_rget_missing_returns_none(self):
        assert cache_module.rget("no_such_key") is None

    def test_rget_missing_returns_custom_default(self):
        assert cache_module.rget("no_such_key", default="fallback") == "fallback"

    def test_rset_overwrites(self):
        cache_module.rset("k", "first")
        cache_module.rset("k", "second")
        assert cache_module.rget("k") == "second"

    def test_rdel_removes_key(self):
        cache_module.rset("del_key", "value")
        cache_module.rdel("del_key")
        assert cache_module.rget("del_key") is None

    def test_rdel_nonexistent_key_no_error(self):
        cache_module.rdel("ghost_key")  # should not raise

    def test_rset_list(self):
        cache_module.rset("list_key", [1, 2, 3])
        assert cache_module.rget("list_key") == [1, 2, 3]

    def test_rset_string(self):
        cache_module.rset("str_key", "hello")
        assert cache_module.rget("str_key") == "hello"


class TestTTL:
    def test_expired_key_returns_none(self):
        cache_module.rset("ttl_key", "value", ttl=100)
        # Force expiry by backdating
        cache_module._cache_ttl["ttl_key"] = time.time() - 1
        assert cache_module.rget("ttl_key") is None

    def test_fresh_ttl_not_expired(self):
        cache_module.rset("fresh_key", "value", ttl=3600)
        assert cache_module.rget("fresh_key") == "value"

    def test_no_ttl_does_not_expire(self):
        cache_module.rset("no_ttl", "value")
        assert "no_ttl" not in cache_module._cache_ttl
        assert cache_module.rget("no_ttl") == "value"


class TestPatternKeys:
    def test_prefix_wildcard_match(self):
        cache_module.rset("novel:abc:ctx:1", "d1")
        cache_module.rset("novel:abc:ctx:2", "d2")
        cache_module.rset("novel:xyz:ctx:1", "other")
        keys = cache_module.rkeys("novel:abc:*")
        assert len(keys) == 2
        assert all(k.startswith("novel:abc:") for k in keys)

    def test_exact_key_match(self):
        cache_module.rset("exact", "v")
        assert cache_module.rkeys("exact") == ["exact"]

    def test_no_match_returns_empty(self):
        assert cache_module.rkeys("nonexistent:*") == []


class TestStopFlag:
    def test_stop_flag_lifecycle(self):
        novel_id = "test-novel-stop"
        assert not cache_module.is_stop_requested(novel_id)
        cache_module.request_stop(novel_id)
        assert cache_module.is_stop_requested(novel_id)
        cache_module.clear_stop(novel_id)
        assert not cache_module.is_stop_requested(novel_id)

    def test_stop_flag_isolated_per_novel(self):
        cache_module.request_stop("novel-a")
        assert not cache_module.is_stop_requested("novel-b")


class TestNovelHelpers:
    def test_novel_key_format(self):
        assert cache_module.novel_key("n123", "ctx:1") == "novel:n123:ctx:1"

    def test_recent_summaries_roundtrip(self):
        novel_id = "summ-novel"
        data = [{"chapter": 1, "text": "summary"}]
        cache_module.set_recent_summaries(novel_id, data)
        assert cache_module.get_recent_summaries(novel_id) == data

    def test_recent_summaries_default_empty(self):
        assert cache_module.get_recent_summaries("ghost-novel") == []

    def test_narrative_state_roundtrip(self):
        novel_id = "narr-novel"
        state = {"arc": "rising", "tension": 8}
        cache_module.set_narrative_state(novel_id, state)
        assert cache_module.get_narrative_state(novel_id) == state

    def test_ping(self):
        assert cache_module.ping() is True
