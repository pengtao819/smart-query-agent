"""LLM 客户端：把 OpenAI 兼容接口薄封装一层。

为什么要封装：
- 统一超时/重试，避免一次网络抖动就让整轮评测挂掉
- 统一 token 统计，方便算成本
- JSON 模式封装，SQL 生成要求结构化输出

只有「可重试的错误」（超时/连接/限流/5xx）才重试；
Key 无效、模型不存在这类 4xx 错误直接抛，重试没意义还费额度。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

# 只有这些错误值得重试
RETRYABLE_ERRORS = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)


@dataclass
class LLMResult:
    """一次模型调用的结果（含成本与耗时）。"""

    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    data: dict = field(default_factory=dict)  # JSON 模式解析后的结果


@lru_cache
def get_client() -> OpenAI:
    """进程内复用同一个客户端（内部维护连接池）。"""
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY 为空，请在项目根目录的 .env 里配置")
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=90,
        max_retries=0,  # 重试交给 tenacity，避免双重重试放大调用量
    )


@retry(
    retry=retry_if_exception_type(RETRYABLE_ERRORS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _call(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1200,
    json_mode: bool = False,
) -> LLMResult:
    settings = get_settings()
    used_model = model or settings.llm_model
    started = time.perf_counter()

    kwargs: dict = {
        "model": used_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # qwen3 系列支持关闭思考链：速度提升一个数量级，准确率基本不降
    if used_model.startswith("qwen3"):
        kwargs["extra_body"] = {"enable_thinking": settings.llm_enable_thinking}

    resp = get_client().chat.completions.create(**kwargs)
    latency_ms = (time.perf_counter() - started) * 1000

    usage = getattr(resp, "usage", None)
    return LLMResult(
        content=(resp.choices[0].message.content or "").strip(),
        model=used_model,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        latency_ms=latency_ms,
    )


def chat_text(system: str, user: str, **kwargs) -> LLMResult:
    return _call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        **kwargs,
    )


def chat_json(system: str, user: str, **kwargs) -> LLMResult:
    """要求模型输出 JSON，并替我们解析好放在 result.data 里。"""
    result = _call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        json_mode=True,
        **kwargs,
    )
    result.data = _parse_json(result.content)
    return result


def _parse_json(text: str) -> dict:
    """宽容地解析 JSON：容忍 ```json 包裹或前后有废话。"""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}