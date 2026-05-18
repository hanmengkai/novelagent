"""
Settings — V2 config loaded from .env
支持 DeepSeek / 千问(Qwen) / GLM-5 / Kimi 四套模型配置，运行时按 DEFAULT_PROVIDER 路由。
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # === DeepSeek ===
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL")

    # === 千问 (Qwen) ===
    qwen_api_key: str = Field(default="", alias="QWEN_API_KEY")
    qwen_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", alias="QWEN_BASE_URL")
    qwen_model: str = Field(default="qwen-plus", alias="QWEN_MODEL")

    # === GLM-5 ===
    glm_api_key: str = Field(default="", alias="GLM_API_KEY")
    glm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", alias="GLM_BASE_URL")
    glm_model: str = Field(default="glm-5", alias="GLM_MODEL")

    # === Kimi (Moonshot) ===
    kimi_api_key: str = Field(default="", alias="KIMI_API_KEY")
    kimi_base_url: str = Field(default="https://api.moonshot.cn/v1", alias="KIMI_BASE_URL")
    kimi_model: str = Field(default="moonshot-v1-32k", alias="KIMI_MODEL")

    # === Ollama（本地部署） ===
    ollama_base_url: str = Field(default="http://localhost:11434/v1", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen3.6", alias="OLLAMA_MODEL")

    # === 模型路由 ===
    default_provider: str = Field(default="deepseek", alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="deepseek-chat", alias="DEFAULT_MODEL")
    strong_model: str = Field(default="deepseek-reasoner", alias="STRONG_MODEL")
    json_model: str = Field(default="deepseek-chat", alias="JSON_MODEL")
    search_model: str = Field(default="deepseek-chat", alias="SEARCH_MODEL")

    # === MinIO ===
    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin123", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="novel-texts", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    # === Vector store ===
    embedding_cuda_device: str = Field(default="", alias="EMBEDDING_CUDA_DEVICE")

    # === App ===
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    web_port: int = Field(default=9101, alias="WEB_PORT")
    web_secret_path: str = Field(default="", alias="WEB_SECRET_PATH")

    # === Writing ===
    chapter_target_chars: int = 5000
    chapter_parts: int = 2
    max_retry_per_chapter: int = 3
    compaction_interval: int = 20
    context_recent_chapters: int = 3

    # === LLM call limits ===
    llm_max_tokens: int = 12000
    llm_temperature: float = 0.85
    llm_timeout: int = 300

    model_config = {"env_file": ".env", "extra": "ignore", "populate_by_name": True}

    def get_provider_config(self, provider: Optional[str] = None) -> dict:
        """返回指定 provider 的 (api_key, base_url) 配置。"""
        p = provider or self.default_provider
        if p == "qwen":
            return {"api_key": self.qwen_api_key, "base_url": self.qwen_base_url}
        if p == "glm":
            return {"api_key": self.glm_api_key, "base_url": self.glm_base_url}
        if p == "kimi":
            return {"api_key": self.kimi_api_key, "base_url": self.kimi_base_url}
        if p == "ollama":
            return {"api_key": "ollama", "base_url": self.ollama_base_url}
        return {"api_key": self.deepseek_api_key, "base_url": self.deepseek_base_url}

    def get_model_for_provider(self, provider: Optional[str] = None) -> str:
        """返回指定 provider 的默认 model name。"""
        p = provider or self.default_provider
        if p == "qwen":
            return self.qwen_model
        if p == "glm":
            return self.glm_model
        if p == "kimi":
            return self.kimi_model
        if p == "ollama":
            return self.ollama_model
        return self.default_model


@lru_cache()
def get_settings() -> Settings:
    return Settings()

