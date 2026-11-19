"""声明式 JSON 映射适配器（C2-04）：用配置描述字段路径，避免为每个站点写代码。"""
from __future__ import annotations

from typing import Any

from ..httpclient import FetchError
from .base import CollectResult, Collector, canonicalize_url, clean_text, content_hash, to_utc_iso


def get_path(data: Any, path: str) -> Any:
    """支持 ``a.b[0].c`` / ``a.b.0.c`` / ``$``（根对象）。"""
    if path in ("", "$", None):
        return data
    current = data
    for raw in str(path).split("."):
        segment = raw.strip()
        if not segment:
            continue
        while "[" in segment and segment.endswith("]"):
            head, _, tail = segment.partition("[")
            if head:
                if isinstance(current, dict):
                    current = current.get(head)
                elif isinstance(current, list) and head.isdigit():
                    current = current[int(head)] if int(head) < len(current) else None
                else:
                    return None
            index = tail[:-1]
            if isinstance(current, list) and index.isdigit():
                position = int(index)
                current = current[position] if position < len(current) else None
            else:
                return None
            segment = ""
        if not segment:
            continue
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            position = int(segment)
            current = current[position] if position < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _stringify(value: Any, *, join: str = "; ", limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [clean_text(_stringify(item, join=join, limit=limit), limit=limit) for item in value]
        return clean_text(join.join(part for part in parts if part), limit=limit)
    if isinstance(value, dict):
        for key in ("name", "title", "value", "display_name", "text"):
            if key in value:
                return _stringify(value[key], join=join, limit=limit)
        return ""
    return clean_text(value, limit=limit)


def _dig(data: Any, candidates: Any, *, join: str) -> str:
    paths = candidates if isinstance(candidates, list) else [candidates]
    for path in paths:
        value = get_path(data, str(path))
        text = _stringify(value, join=join)
        if text:
            return text
    return ""


class JsonMapCollector(Collector):
    adapter = "jsonmap"

    def collect(self, source: dict[str, Any], subscription: dict[str, Any]) -> CollectResult:
        collector_cfg = source.get("collector") or {}
        params = {**(collector_cfg.get("params") or {}), **(subscription.get("parameters") or {})}
        url = params.get("url") or (source.get("access") or {}).get("base_url")
        if not url:
            raise FetchError("config", "JSON 映射缺少 url")
        fields = params.get("fields") or {}
        if not isinstance(fields, dict) or not fields.get("external_id"):
            raise FetchError("config", "JSON 映射缺少 fields.external_id")
        max_items = int(params.get("max_items") or collector_cfg.get("max_items_per_run") or 30)
        join = str(params.get("join") or "; ")
        date_parts = bool(params.get("date_parts"))

        response = self.client.get(str(url))
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise FetchError("invalid_json", "响应不是合法 JSON") from exc

        node = get_path(payload, str(params.get("items_path") or "$"))
        if node is None:
            raise FetchError("shape", "items_path 未找到数据")
        if isinstance(node, dict):
            rows = [node]
        elif isinstance(node, list):
            rows = node
        else:
            raise FetchError("shape", "items_path 必须指向对象或数组")

        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        for row in rows[:max_items]:
            if not isinstance(row, dict):
                continue
            external_id = _dig(row, fields["external_id"], join=join)
            if not external_id:
                warnings.append("跳过缺少 external_id 的条目")
                continue
            title = _dig(row, fields.get("title", []), join=join) or "(无标题)"
            published_raw: Any = None
            if fields.get("published_at"):
                for path in (fields["published_at"] if isinstance(fields["published_at"], list) else [fields["published_at"]]):
                    published_raw = get_path(row, str(path))
                    if published_raw:
                        break
            if date_parts and isinstance(published_raw, list) and published_raw:
                parts = published_raw[0] if isinstance(published_raw[0], list) else published_raw
                try:
                    year = int(parts[0])
                    month = int(parts[1]) if len(parts) > 1 else 1
                    day = int(parts[2]) if len(parts) > 2 else 1
                    published_raw = f"{year:04d}-{month:02d}-{day:02d}"
                except (TypeError, ValueError, IndexError):
                    published_raw = None
            summary = _dig(row, fields.get("summary", []), join=join)
            url_value = _dig(row, fields.get("url", []), join=join)
            if url_value and not url_value.startswith(("http://", "https://")):
                template = params.get("url_template")
                url_value = template.format(value=url_value) if template else url_value
            items.append(
                {
                    "external_id": external_id[:500],
                    "title": title[:500],
                    "canonical_url": canonicalize_url(url_value),
                    "published_at": to_utc_iso(published_raw),
                    "summary": summary[:800],
                    "author": _dig(row, fields.get("author", []), join=join)[:200] or None,
                    "kind": str(params.get("kind") or "article"),
                    "content_hash": content_hash(title, summary),
                }
            )
        return CollectResult(items=items, warnings=warnings, raw_bytes=len(response.content))
