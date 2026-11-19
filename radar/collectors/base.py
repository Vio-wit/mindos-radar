"""采集器公共类型与规范化工具。"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)


def clean_text(value: Any, *, limit: int = 2000) -> str:
    if value is None:
        return ""
    text = str(value)
    text = SCRIPT_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text).strip()
    # 中文标点前后不应保留插标签产生的空格
    text = re.sub(r"\s+([。，！？；：、,.!?;:)\]】」』])", r"\1", text)
    text = re.sub(r"([(\[（【「『])\s+", r"\1", text)
    return text[:limit]


def to_utc_iso(value: Any) -> str | None:
    """把常见时间格式统一成 ISO 8601 UTC；无法判断时返回 None（不伪造）。"""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return to_utc_iso(int(text))
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def content_hash(*parts: Any) -> str:
    payload = "\x1f".join(clean_text(part, limit=20000) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonicalize_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if not text.startswith(("http://", "https://")):
        return None
    # 去掉明确的追踪参数，保留业务参数
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "spm"}
    parts = urlsplit(text)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in tracking]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


@dataclass
class CollectResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    warnings: list[str] = field(default_factory=list)
    status: str = "SUCCEEDED"
    etag: str | None = None
    last_modified: str | None = None
    raw_bytes: int = 0
    not_modified: bool = False


class Collector:
    """适配器基类：discover/fetch/normalize 在这里合并为一个 collect()。"""

    adapter = "base"

    def __init__(self, client, settings: dict[str, Any] | None = None) -> None:
        self.client = client
        self.settings = settings or {}

    def collect(self, source: dict[str, Any], subscription: dict[str, Any]) -> CollectResult:  # pragma: no cover
        raise NotImplementedError
