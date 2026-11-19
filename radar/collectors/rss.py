"""RSS 2.0 / Atom 通用适配器（C2-03）。"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from ..httpclient import FetchError
from .base import CollectResult, Collector, canonicalize_url, clean_text, content_hash, to_utc_iso


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def _first(node: ET.Element, *names: str) -> ET.Element | None:
    for child in list(node):
        if _local(child.tag) in names:
            return child
    return None


def _text(node: ET.Element, *names: str) -> str:
    child = _first(node, *names)
    if child is None:
        return ""
    return clean_text("".join(child.itertext()))


def parse_feed(text: str, *, max_items: int = 50) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if "<!ENTITY" in text:
        raise ValueError("检测到 XML 实体声明，已拒绝解析")
    if "<!DOCTYPE" in text:
        start = text.find("<!DOCTYPE")
        end = text.find(">", start)
        if start != -1 and end != -1:
            text = text[:start] + text[end + 1:]
    root = ET.fromstring(text)
    kind = _local(root.tag)
    items: list[dict[str, Any]] = []

    if kind == "feed":
        entries = [child for child in list(root) if _local(child.tag) == "entry"]
        for entry in entries[:max_items]:
            link = ""
            for child in list(entry):
                if _local(child.tag) == "link":
                    rel = (child.get("rel") or "alternate").lower()
                    if rel == "alternate" and child.get("href"):
                        link = child.get("href") or ""
                        break
                    if not link and child.get("href"):
                        link = child.get("href") or ""
            external_id = _text(entry, "id") or link
            title = _text(entry, "title")
            published = _text(entry, "published") or _text(entry, "updated")
            summary = _text(entry, "summary") or _text(entry, "content")
            author = _text(entry, "name") or _text(entry, "author")
            items.append(
                {
                    "external_id": external_id,
                    "title": title,
                    "canonical_url": canonicalize_url(link),
                    "published_at": to_utc_iso(published),
                    "summary": summary[:800],
                    "author": author or None,
                    "kind": "article",
                }
            )
    else:
        channel_node = _first(root, "channel")
        channel = channel_node if channel_node is not None else root
        entries = [child for child in list(channel) if _local(child.tag) == "item"]
        for entry in entries[:max_items]:
            link = _text(entry, "link")
            guid = _text(entry, "guid") or link
            title = _text(entry, "title")
            published = _text(entry, "pubdate") or _text(entry, "date")
            summary = _text(entry, "description") or _text(entry, "encoded") or _text(entry, "summary")
            author = _text(entry, "creator") or _text(entry, "author")
            items.append(
                {
                    "external_id": guid,
                    "title": title,
                    "canonical_url": canonicalize_url(link),
                    "published_at": to_utc_iso(published),
                    "summary": summary[:800],
                    "author": author or None,
                    "kind": "article",
                }
            )

    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not item["external_id"]:
            warnings.append("跳过缺少 id/guid/link 的条目")
            continue
        if not item["title"]:
            item["title"] = "(无标题)"
        item["content_hash"] = content_hash(item["title"], item["summary"])
        cleaned.append(item)
    return cleaned, warnings


class RssCollector(Collector):
    adapter = "rss"

    def collect(self, source: dict[str, Any], subscription: dict[str, Any]) -> CollectResult:
        params = subscription.get("parameters") or {}
        feed_url = params.get("feed_url") or (source.get("access") or {}).get("base_url")
        if not feed_url:
            raise FetchError("config", "RSS 订阅缺少 feed_url")
        collector_cfg = source.get("collector") or {}
        max_items = int(params.get("max_items") or collector_cfg.get("max_items_per_run") or 30)
        cursor: dict[str, Any] = {}
        try:
            cursor = json.loads(subscription.get("cursor") or "{}")
        except json.JSONDecodeError:
            cursor = {}
        headers = {}
        if cursor.get("etag"):
            headers["If-None-Match"] = cursor["etag"]
        if cursor.get("last_modified"):
            headers["If-Modified-Since"] = cursor["last_modified"]
        try:
            response = self.client.get(feed_url, headers=headers)
        except FetchError as exc:
            if exc.code == "http_304":
                return CollectResult(not_modified=True, status="SUCCEEDED")
            raise
        items, warnings = parse_feed(response.text, max_items=max_items)
        return CollectResult(
            items=items,
            warnings=warnings,
            etag=response.header("etag") or None,
            last_modified=response.header("last-modified") or None,
            raw_bytes=len(response.content),
        )
