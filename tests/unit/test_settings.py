"""
Unit tests for config/settings.py — defaults, env overrides, provider routing.
"""
import os
import pytest
from unittest.mock import patch


pytestmark = pytest.mark.unit


class TestDefaults:
    def test_default_provider(self):
        from config.settings import Settings
        assert Settings().default_provider == "deepseek"

    def test_strong_model_overridable_from_env(self):
        from config.settings import Settings
        with patch.dict(os.environ, {"STRONG_MODEL": "custom-reasoner"}):
            assert Settings().strong_model == "custom-reasoner"

    def test_default_chapter_target_chars(self):
        from config.settings import Settings
        assert Settings().chapter_target_chars == 5000

    def test_default_max_retry(self):
        from config.settings import Settings
        assert Settings().max_retry_per_chapter == 3

    def test_default_compaction_interval(self):
        from config.settings import Settings
        assert Settings().compaction_interval == 20

    def test_default_embedding_cuda_device_empty(self):
        from config.settings import Settings
        assert Settings().embedding_cuda_device == ""


class TestEnvOverrides:
    def test_embedding_cuda_device_from_env(self):
        from config.settings import Settings
        with patch.dict(os.environ, {"EMBEDDING_CUDA_DEVICE": "1"}):
            assert Settings().embedding_cuda_device == "1"

    def test_default_provider_from_env(self):
        from config.settings import Settings
        with patch.dict(os.environ, {"DEFAULT_PROVIDER": "qwen"}):
            assert Settings().default_provider == "qwen"

    def test_web_port_from_env(self):
        from config.settings import Settings
        with patch.dict(os.environ, {"WEB_PORT": "8080"}):
            assert Settings().web_port == 8080


class TestProviderConfig:
    def test_deepseek_config(self):
        from config.settings import Settings
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "dk-test"}):
            s = Settings()
        cfg = s.get_provider_config("deepseek")
        assert cfg["api_key"] == "dk-test"
        assert "deepseek" in cfg["base_url"]

    def test_qwen_config(self):
        from config.settings import Settings
        with patch.dict(os.environ, {"QWEN_API_KEY": "qw-test"}):
            s = Settings()
        cfg = s.get_provider_config("qwen")
        assert cfg["api_key"] == "qw-test"
        assert "dashscope" in cfg["base_url"]

    def test_unknown_provider_falls_back_to_deepseek(self):
        from config.settings import Settings
        s = Settings()
        cfg = s.get_provider_config("unknown")
        assert "deepseek" in cfg["base_url"]

    def test_get_model_for_qwen(self):
        from config.settings import Settings
        s = Settings()
        assert s.get_model_for_provider("qwen") == s.qwen_model

    def test_get_model_for_glm(self):
        from config.settings import Settings
        s = Settings()
        assert s.get_model_for_provider("glm") == s.glm_model

    def test_get_model_for_kimi(self):
        from config.settings import Settings
        s = Settings()
        assert s.get_model_for_provider("kimi") == s.kimi_model

    def test_get_model_default_falls_back_to_deepseek(self):
        from config.settings import Settings
        s = Settings()
        assert s.get_model_for_provider("unknown") == s.default_model
