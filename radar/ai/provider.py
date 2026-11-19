"""AI 分析提供方：显式手动触发、失败分类、UNKNOWN 结果保护。

约束（需求第 9 节）：
* 只有用户手动点击/命令才会调用；本模块不做任何后台自动批量；
* 未配置 Key 时分析入口直接拒绝，不以假数据冒充真实结果；
* 请求一旦发出但结果未知（超时/连接中断），标记 UNKNOWN_OUTCOME，禁止自动重试；
* 输入先做 PII 脱敏，且只发送标题/摘要（正文仅在 rights.fulltext=allowed 时发送）。
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import config

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
ID_RE = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")

SYSTEM_PROMPT = (
    "你是个人情报助理。只根据给定标题与摘要输出严格 JSON，不要编造事实。"
    "字段：summary(<=120字)、key_points(1-4条)、tags(1-5个)、category(一个短词)、"
    "relevance(0-5整数)、action(建议动作，<=40字)。不要输出 Markdown。"
)


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, unknown_outcome: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.unknown_outcome = unknown_outcome


@dataclass
class ProviderResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def redact(text: str) -> str:
    text = EMAIL_RE.sub("[email]", text)
    text = PHONE_RE.sub("[phone]", text)
    text = ID_RE.sub("[id]", text)
    return text


def build_input(item: dict[str, Any], *, include_body: bool, max_chars: int) -> str:
    parts = [f"标题：{item.get('title') or ''}"]
    if item.get("source_name"):
        parts.append(f"来源：{item['source_name']}")
    if item.get("published_at"):
        parts.append(f"时间：{item['published_at']}")
    if item.get("summary"):
        parts.append(f"摘要：{item['summary']}")
    if include_body and item.get("body"):
        parts.append(f"正文：{item['body']}")
    if item.get("canonical_url"):
        parts.append(f"链接：{item['canonical_url']}")
    return redact("\n".join(parts))[:max_chars]


class FakeProvider:
    """仅用于测试与演示的确定性提供方；名称始终标记为 fake。"""

    name = "fake"
    model = "fake-v1"

    def __init__(self, *, fail_with: ProviderError | None = None) -> None:
        self.fail_with = fail_with

    def analyze(self, prompt: str, *, max_output_tokens: int = 800) -> ProviderResult:
        if self.fail_with:
            raise self.fail_with
        payload = {
            "summary": "演示分析结果（fake provider，不可用于真实决策）",
            "key_points": ["测试要点一", "测试要点二"],
            "tags": ["demo", "fake"],
            "category": "demo",
            "relevance": 3,
            "action": "仅用于测试",
        }
        text = json.dumps(payload, ensure_ascii=False)
        return ProviderResult(
            text=text, prompt_tokens=len(prompt) // 3, completion_tokens=len(text) // 3,
            request_id=f"fake-{uuid.uuid4().hex[:8]}", raw={"fake": True},
        )


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, api_key: str, *, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com",
                 timeout_seconds: float = 60) -> None:
        if not api_key:
            raise ProviderError("no_api_key", "未配置 DeepSeek API Key", retryable=False)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def analyze(self, prompt: str, *, max_output_tokens: int = 800) -> ProviderResult:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": int(max_output_tokens),
                "temperature": 0.2,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "MindOS-Radar/0.1",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds, context=ssl.create_default_context()) as response:
                raw_bytes = response.read(1_000_000)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(500).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            code = f"http_{exc.code}"
            if exc.code in (401, 403):
                raise ProviderError(code, "认证失败，请检查 API Key", retryable=False) from exc
            if exc.code == 429:
                raise ProviderError(code, "触发限流，稍后再试", retryable=True) from exc
            if exc.code >= 500:
                raise ProviderError(code, "服务端错误，请求可能已被处理", retryable=True, unknown_outcome=True) from exc
            raise ProviderError(code, f"请求被拒绝: {detail[:200]}", retryable=False) from exc
        except TimeoutError as exc:
            raise ProviderError("timeout", "请求超时，结果未知，禁止自动重试", retryable=False, unknown_outcome=True) from exc
        except urllib.error.URLError as exc:
            raise ProviderError("network", f"网络错误，结果未知: {type(exc.reason).__name__}", retryable=False, unknown_outcome=True) from exc
        except OSError as exc:
            raise ProviderError("network", f"网络错误，结果未知: {type(exc).__name__}", retryable=False, unknown_outcome=True) from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ProviderError("bad_response", "响应不是合法 JSON，结果未知", retryable=False, unknown_outcome=True) from exc
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError("empty_response", "响应缺少 choices，结果未知", retryable=False, unknown_outcome=True)
        text = ((choices[0] or {}).get("message") or {}).get("content") or ""
        usage = payload.get("usage") or {}
        return ProviderResult(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            request_id=str(payload.get("id") or ""),
            raw={"elapsed_ms": elapsed_ms, "model": payload.get("model")},
        )


def provider_from_settings(settings: dict[str, Any] | None = None, *, fake: bool = False):
    settings = settings or config.load_settings()
    ai = settings.get("ai") or {}
    if fake:
        return FakeProvider()
    key = config.get_secret("deepseek_api_key", "RADAR_DEEPSEEK_API_KEY")
    if not key:
        raise ProviderError("no_api_key", "未配置 DeepSeek API Key（设置页或环境变量）", retryable=False)
    return DeepSeekProvider(
        key,
        model=str(ai.get("model") or "deepseek-chat"),
        base_url=str(ai.get("base_url") or "https://api.deepseek.com"),
        timeout_seconds=float(ai.get("timeout_seconds") or 60),
    )
