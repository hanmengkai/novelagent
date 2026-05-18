"""
Unit tests for db/file_store.py — atomic JSON file storage.
Uses isolated_file_store fixture to redirect DATA_ROOT to a temp dir.
"""
import json
import os
import pytest
import db.file_store as fs


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def store(isolated_file_store):
    return isolated_file_store


class TestSaveLoadRoundtrip:
    def test_dict(self):
        fs.save_json("n1", "config", {"title": "My Novel", "chapters": 10})
        assert fs.load_json("n1", "config") == {"title": "My Novel", "chapters": 10}

    def test_list(self):
        data = [{"id": 1}, {"id": 2}]
        fs.save_json("n1", "characters", data)
        assert fs.load_json("n1", "characters") == data

    def test_nested(self):
        data = {"meta": {"lang": "zh", "genre": "玄幻"}, "volumes": [1, 2, 3]}
        fs.save_json("n1", "meta", data)
        assert fs.load_json("n1", "meta") == data

    def test_overwrite(self):
        fs.save_json("n1", "sec", {"v": 1})
        fs.save_json("n1", "sec", {"v": 2})
        assert fs.load_json("n1", "sec") == {"v": 2}


class TestMissingFile:
    def test_missing_returns_custom_default(self):
        result = fs.load_json("n1", "nonexistent", default={"empty": True})
        assert result == {"empty": True}

    def test_missing_returns_empty_dict_by_default(self):
        assert fs.load_json("n1", "nonexistent") == {}


class TestDiskPersistence:
    def test_file_exists_on_disk(self, isolated_file_store):
        fs.save_json("n2", "meta", {"k": "v"})
        path = os.path.join(str(isolated_file_store), "n2", "meta.json")
        assert os.path.exists(path)

    def test_file_is_valid_json(self, isolated_file_store):
        fs.save_json("n2", "meta", {"k": "v"})
        path = os.path.join(str(isolated_file_store), "n2", "meta.json")
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == {"k": "v"}

    def test_cache_serves_after_disk_corrupt(self, isolated_file_store):
        fs.save_json("n3", "data", {"v": 1})
        # Corrupt the on-disk file — cache should still return original
        path = os.path.join(str(isolated_file_store), "n3", "data.json")
        with open(path, "w") as f:
            f.write("INVALID JSON")
        assert fs.load_json("n3", "data") == {"v": 1}


class TestCacheInvalidation:
    def test_invalidate_specific_section(self):
        fs.save_json("n1", "s1", {"a": 1})
        fs.save_json("n1", "s2", {"b": 2})
        fs.invalidate_cache("n1", "s1")
        assert "n1:s1" not in fs._FILE_CACHE
        assert "n1:s2" in fs._FILE_CACHE

    def test_invalidate_all_for_novel(self):
        fs.save_json("n1", "s1", {"a": 1})
        fs.save_json("n1", "s2", {"b": 2})
        fs.invalidate_cache("n1")
        assert not any(k.startswith("n1:") for k in fs._FILE_CACHE)

    def test_invalidate_all(self):
        fs.save_json("n1", "d", {})
        fs.save_json("n2", "d", {})
        fs.invalidate_cache()
        assert len(fs._FILE_CACHE) == 0


class TestDeleteAndList:
    def test_delete_novel_removes_dir(self, isolated_file_store):
        fs.save_json("del_novel", "meta", {"x": 1})
        assert os.path.exists(os.path.join(str(isolated_file_store), "del_novel"))
        fs.delete_novel_all("del_novel")
        assert not os.path.exists(os.path.join(str(isolated_file_store), "del_novel"))

    def test_delete_novel_clears_cache(self):
        fs.save_json("del_novel2", "meta", {"x": 1})
        fs.delete_novel_all("del_novel2")
        assert not any(k.startswith("del_novel2:") for k in fs._FILE_CACHE)

    def test_list_novel_ids(self):
        fs.save_json("novel_a", "data", {})
        fs.save_json("novel_b", "data", {})
        ids = fs.list_novel_ids()
        assert "novel_a" in ids
        assert "novel_b" in ids
        assert ids == sorted(ids)

    def test_list_novel_ids_empty(self, isolated_file_store):
        ids = fs.list_novel_ids()
        assert ids == []
