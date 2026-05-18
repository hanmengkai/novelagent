"""
Shared pytest fixtures for novelagentv2 tests.
"""
import pytest
from unittest.mock import patch
import db.file_store as _fs
import db.cache as _cache


@pytest.fixture
def isolated_file_store(tmp_path):
    """Redirect DATA_ROOT to a temp dir and clear in-memory cache."""
    with patch.object(_fs, "DATA_ROOT", str(tmp_path)):
        _fs._FILE_CACHE.clear()
        yield tmp_path
        _fs._FILE_CACHE.clear()


@pytest.fixture
def isolated_cache():
    """Reset in-memory cache state and disable file persistence."""
    _cache._cache.clear()
    _cache._cache_ttl.clear()
    with patch.object(_cache, "_save_persisted", return_value=None):
        yield
    _cache._cache.clear()
    _cache._cache_ttl.clear()
