"""适配器注册表：来源配置里的 ``collector.adapter`` 映射到具体实现。"""
from __future__ import annotations

from typing import Any

from .api_adapters import GitHubReleasesCollector, HackerNewsCollector, OpenMeteoCollector
from .base import CollectResult, Collector, canonicalize_url, clean_text, content_hash, to_utc_iso
from .jsonmap import JsonMapCollector
from .rss import RssCollector, parse_feed

ADAPTERS: dict[str, type[Collector]] = {
    "rss": RssCollector,
    "jsonmap": JsonMapCollector,
    "hackernews": HackerNewsCollector,
    "github_releases": GitHubReleasesCollector,
    "openmeteo": OpenMeteoCollector,
}


def build_collector(adapter: str, client, settings: dict[str, Any] | None = None) -> Collector:
    cls = ADAPTERS.get(adapter)
    if cls is None:
        raise ValueError(f"未知适配器: {adapter}")
    return cls(client, settings)


__all__ = [
    "ADAPTERS",
    "CollectResult",
    "Collector",
    "build_collector",
    "canonicalize_url",
    "clean_text",
    "content_hash",
    "parse_feed",
    "to_utc_iso",
]
