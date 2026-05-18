"""
llm/client.py — Unified LLM client
Supports: DeepSeek / 千问(Qwen) / GLM-5 / Kimi — all OpenAI-compatible endpoints
Features:
  - Multi-provider routing (deepseek | qwen | glm | kimi)
  - JSON-mode extraction with fallback
  - Retry with exponential backoff
  - Token usage logging

Note: Embedding functions removed (vector DB is disabled).
"""
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Optional
from loguru import logger
from openai import OpenAI, APIError, APITimeoutError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import get_settings

# ── Per-chapter novel context (thread-local) ──────────────────────────────────
_ctx = threading.local()

# Per-novel file lock for llm_log writes
_log_locks: dict[str, threading.Lock] = {}
_log_locks_mutex = threading.Lock()


def _extract_final_answer(reasoning: str) -> str | None:
    """从 Qwen3 reasoning 文本末尾提取最终答案。
    去掉标记、列表前缀、分析过程，只取最终输出部分。"""
    import re
    # 尝试找到 "Answer:" 或 "最终答案" 之后的内容
    for marker in ["Final Answer:", "最终答案：", "答案：", "Answer:"]:
        idx = reasoning.rfind(marker)
        if idx >= 0:
            after = reasoning[idx + len(marker):].strip()
            # 去掉开头的标点 / 引号
            after = after.lstrip('"\'""').strip()
            if after:
                return after

    # 取最后一段（非空行，不是编号行）
    lines = reasoning.strip().split("\n")
    lines = [l.strip() for l in lines if l.strip()]
    for line in reversed(lines):
        # 跳过编号行 "N.**..." "N." "N)"
        if re.match(r'^\d+[\.\)]\s*\*{0,2}\**\s*$', line):
            continue
        # 跳过 "Here's a thinking process:" 类行
        if re.match(r"^(Here'?s|这是|以下|开始)", line, re.IGNORECASE):
            continue
        # 跳过纯分析行（含分析/总结/考虑等词）
        if re.search(r"(分析|考虑|总结|根据|需要|为了|首先|然后|最后|Identify|Analyze|Formulate|Refinement)", line, re.IGNORECASE):
            continue
        # 剩余的非空短行即为最终答案
        return line

    return None


def _get_log_lock(novel_id: str) -> threading.Lock:
    with _log_locks_mutex:
        if novel_id not in _log_locks:
            _log_locks[novel_id] = threading.Lock()
        return _log_locks[novel_id]


def set_novel_context(novel_id: str) -> None:
    """Call before running a chapter pipeline to enable per-novel LLM logging."""
    _ctx.novel_id = novel_id


def clear_novel_context() -> None:
    _ctx.novel_id = None


def _append_llm_log(entry: dict) -> None:
    """Append one LLM call record to data/<novel_id>/llm_log.json atomically."""
    novel_id = getattr(_ctx, "novel_id", None)
    if not novel_id:
        return
    data_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    path = os.path.join(data_root, novel_id, "llm_log.json")
    if not os.path.exists(os.path.dirname(path)):
        return
    lock = _get_log_lock(novel_id)
    try:
        with lock:
            existing = []
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
            existing.append(entry)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False)
            os.replace(tmp, path)
    except Exception as e:
        logger.debug(f"[LLM log] write failed (non-fatal): {e}")

# Per-provider client cache: {"deepseek": OpenAI(...), "qwen": ..., "glm": ...}
_clients: dict[str, OpenAI] = {}


def get_client(provider: Optional[str] = None) -> OpenAI:
    """Return (and cache) the OpenAI-compatible client for the given provider."""
    s = get_settings()
    p = provider or s.default_provider
    if p not in _clients:
        cfg = s.get_provider_config(p)
        _clients[p] = OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            timeout=s.llm_timeout,
        )
        logger.info(f"LLM client created: provider={p}, base_url={cfg['base_url']}")
    return _clients[p]


# ── Core chat call ────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((APIError, APITimeoutError, RateLimitError)),
    reraise=True,
)
def chat(
    messages: list[dict],
    model: Optional[str] = None,
    provider: Optional[str] = None,    # "deepseek" | "qwen" | "glm" | "ollama" | None=default
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
    thinking: bool = False,
) -> tuple[str, dict]:
    """
    Send a chat completion request.
    provider: override which backend to call (deepseek/qwen/glm).
    Returns: (content_str, usage_dict)
    """
    s = get_settings()
    # Resolve model: explicit > provider default > global default
    if model is None:
        model = s.get_model_for_provider(provider)
    temperature = temperature if temperature is not None else s.llm_temperature
    max_tokens = max_tokens or s.llm_max_tokens

    # DeepSeek R1 (deepseek-reasoner) does not support temperature or json_object format
    is_reasoner = "reasoner" in model or "r1" in model.lower()
    if is_reasoner:
        temperature = None
        json_mode = False

    # ── 思考模式（Qwen3 / DeepSeek R1）───────────────────────────
    # 必须在 kwargs 组装之前设置，否则 max_tokens 覆盖无效
    if thinking:
        p = provider or s.default_provider
        if p == "ollama":
            # Qwen3 自带推理，会占用大量输出 token，给足空间
            # 以便模型有足够 token 输出最终答案
            max_tokens = max(max_tokens or 8192, 16384)
            temperature = 0.7  # 思考模式降温，减少发散
    else:
        p = provider or s.default_provider
        if p == "ollama":
            # Qwen3 即使不开启式 thinking 也会输出 reasoning
            # 保证最小 token 空间，避免 reasoning 截断导致无答案
            max_tokens = max(max_tokens or 8192, 8192)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    t0 = time.time()
    response = get_client(provider).chat.completions.create(**kwargs)
    elapsed_ms = round((time.time() - t0) * 1000)

    content = response.choices[0].message.content or ""

    # Qwen3 on Ollama 把推理过程放 reasoning 字段，content 为空
    # 需要从 reasoning 字段提取作为回退
    p = provider or s.default_provider
    if not content and p == "ollama":
        msg = response.choices[0].message
        msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
        reasoning = getattr(msg, "reasoning", None) or msg_dict.get("reasoning")
        if reasoning:
            # 思考模式：直接取完整推理作为内容（Checker 需要看推理过程）
            # 普通模式：尝试从推理末尾提取最终答案
            if thinking:
                content = reasoning
            else:
                # 非思考模式：从 reasoning 末尾截取最后一段作为最终输出
                cleaned = _extract_final_answer(reasoning)
                content = cleaned or reasoning
    finish_reason = response.choices[0].finish_reason
    usage = {
        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
        "finish_reason": finish_reason,
        "truncated": finish_reason == "length",
    }
    if finish_reason == "length":
        logger.warning(
            f"⚠️  LLM output truncated (finish_reason=length): model={model} "
            f"max_tokens={max_tokens} completion_tokens={usage['completion_tokens']}"
        )

    _append_llm_log({
        "provider": provider or s.default_provider,
        "model": model,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "duration_ms": elapsed_ms,
        "truncated": usage["truncated"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })

    return content, usage


# ── JSON sanitization ─────────────────────────────────────────────────────

def _sanitize_json_text(text: str) -> str:
    """Fix common DeepSeek JSON defects before parsing."""
    # Strip BOM
    text = text.lstrip('﻿')
    # Remove null bytes and non-printable control chars (keep \n \r \t)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    # Remove // line comments
    text = re.sub(r'//[^\n"]*(?=\n|$)', '', text)
    # Remove /* ... */ block comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Fix trailing commas before ] or }
    text = re.sub(r',(\s*[\]}])', r'\1', text)
    # Replace Python-style literals (only when not inside a quoted string)
    text = re.sub(r'(?<!["\w])None(?!["\w])', 'null', text)
    text = re.sub(r'(?<!["\w])True(?!["\w])', 'true', text)
    text = re.sub(r'(?<!["\w])False(?!["\w])', 'false', text)
    return text


def _clean_parsed_json(obj: Any) -> Any:
    """Recursively strip None from lists; leave dict values as-is."""
    if isinstance(obj, list):
        return [_clean_parsed_json(v) for v in obj if v is not None]
    if isinstance(obj, dict):
        return {k: _clean_parsed_json(v) for k, v in obj.items()}
    return obj


# ── JSON extraction ───────────────────────────────────────────────────────

def extract_json(text: str) -> Any:
    """Extract JSON from LLM output; tries multiple fallback strategies."""
    sanitized = _sanitize_json_text(text)

    # 1. Direct parse (sanitized first, then raw)
    for candidate in (sanitized, text):
        try:
            return _clean_parsed_json(json.loads(candidate))
        except Exception:
            pass

    # 2. Code block extraction  ```json ... ```
    for source in (sanitized, text):
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", source)
        if match:
            try:
                return _clean_parsed_json(json.loads(match.group(1)))
            except Exception:
                pass

    # 3. Find first { or [ block
    for source in (sanitized, text):
        match = re.search(r"(\{[\s\S]+\}|\[[\s\S]+\])", source)
        if match:
            try:
                return _clean_parsed_json(json.loads(match.group(1)))
            except Exception:
                pass

    logger.warning(f"JSON extraction failed for text: {text[:200]}")
    return None


def chat_json(
    messages: list[dict],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    expected_keys: Optional[list[str]] = None,
    fallback: Optional[dict] = None,
) -> dict:
    """
    Like chat() but always returns a dict.
    Tries json_mode first; falls back to text extraction.
    GLM-5 不支持 json_object 格式，自动降级为文本提取。
    """
    s = get_settings()
    p = provider or s.default_provider
    model = model or s.get_model_for_provider(p)
    # GLM 不支持 json_object；其余 provider（deepseek/qwen/kimi）均支持
    supports_json_mode = p != "glm"

    try:
        content, _ = chat(messages, model=model, provider=p,
                          json_mode=supports_json_mode)
        result = extract_json(content)
        if result and isinstance(result, dict):
            return result
    except Exception:
        pass

    # Fallback: plain text mode
    try:
        content, _ = chat(messages, model=model, provider=p, json_mode=False)
        result = extract_json(content)
        if result and isinstance(result, dict):
            return result
    except Exception as e:
        logger.error(f"chat_json failed: {e}")

    return fallback or {}


# ── Strong reasoning call ───��─────────────────────────────────────────────

def chat_strong(messages: list[dict], max_tokens: int = 8192) -> tuple[str, dict]:
    """Use the strong/reasoning model with thinking mode.
    根据当前 provider 自动选择合适模型（不使用 deprecated STRONG_MODEL）。"""
    s = get_settings()
    return chat(messages, model=None, provider=s.default_provider,
                max_tokens=max_tokens, json_mode=False, thinking=True)

# ── Simple prompt helpers ─────────────────────────────────────────────────

def simple_chat(system_prompt: str, user_prompt: str,
                model: Optional[str] = None,
                temperature: Optional[float] = None) -> str:
    """Convenience: system + user → content string."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    content, _ = chat(messages, model=model, temperature=temperature)
    return content


def simple_chat_json(system_prompt: str, user_prompt: str,
                     model: Optional[str] = None,
                     fallback: Optional[dict] = None) -> dict:
    """Convenience: system + user → JSON dict."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return chat_json(messages, model=model, fallback=fallback)
