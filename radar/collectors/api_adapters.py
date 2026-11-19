"""官方公开 API 适配器：Hacker News、GitHub Releases、Open-Meteo。"""
from __future__ import annotations

import json
from typing import Any

from ..httpclient import FetchError, headers_from_source
from .base import CollectResult, Collector, canonicalize_url, clean_text, content_hash, to_utc_iso


def _json(response) -> Any:
    try:
        return json.loads(response.text)
    except (TypeError, ValueError) as exc:
        raise FetchError("invalid_json", "响应不是合法 JSON") from exc


class HackerNewsCollector(Collector):
    adapter = "hackernews"

    def collect(self, source: dict[str, Any], subscription: dict[str, Any]) -> CollectResult:
        cfg = source.get("collector") or {}
        params = {**(cfg.get("params") or {}), **(subscription.get("parameters") or {})}
        base = str((source.get("access") or {}).get("base_url") or "https://hacker-news.firebaseio.com/v0").rstrip("/")
        story_type = str(params.get("story_type") or "topstories")
        if story_type not in {"topstories", "newstories", "beststories", "askstories", "showstories"}:
            raise FetchError("config", "story_type 非法")
        max_items = min(int(params.get("max_items") or cfg.get("max_items_per_run") or 20), 60)

        ids = _json(self.client.get(f"{base}/{story_type}.json"))
        if not isinstance(ids, list):
            raise FetchError("shape", "topstories 返回值不是数组")
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        for story_id in ids[:max_items]:
            try:
                row = _json(self.client.get(f"{base}/item/{int(story_id)}.json"))
            except FetchError as exc:
                warnings.append(f"条目 {story_id} 获取失败: {exc.code}")
                continue
            if not isinstance(row, dict) or row.get("deleted") or row.get("dead"):
                continue
            title = clean_text(row.get("title"), limit=500) or "(无标题)"
            discussion = f"https://news.ycombinator.com/item?id={row.get('id')}"
            summary = f"{row.get('score', 0)} points · {row.get('descendants', 0)} comments"
            items.append(
                {
                    "external_id": str(row.get("id")),
                    "title": title,
                    "canonical_url": canonicalize_url(row.get("url")) or discussion,
                    "published_at": to_utc_iso(row.get("time")),
                    "summary": summary,
                    "author": row.get("by"),
                    "kind": "article",
                    "content_hash": content_hash(title, summary),
                    "metadata": {"score": row.get("score"), "comments": row.get("descendants"), "discussion_url": discussion},
                }
            )
        return CollectResult(items=items, warnings=warnings, raw_bytes=0)


class GitHubReleasesCollector(Collector):
    adapter = "github_releases"

    def collect(self, source: dict[str, Any], subscription: dict[str, Any]) -> CollectResult:
        cfg = source.get("collector") or {}
        params = {**(cfg.get("params") or {}), **(subscription.get("parameters") or {})}
        base = str((source.get("access") or {}).get("base_url") or "https://api.github.com").rstrip("/")
        repos = params.get("repos") or ([params["repo"]] if params.get("repo") else [])
        if not isinstance(repos, list) or not repos:
            raise FetchError("config", "GitHub Releases 需要 repos 列表")
        per_page = min(int(params.get("per_page") or 5), 30)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        headers.update(headers_from_source(source, self.settings.get("secret_getter")))
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        for repo in repos[:10]:
            if not isinstance(repo, str) or "/" not in repo:
                warnings.append(f"跳过非法仓库名: {repo!r}")
                continue
            try:
                rows = _json(self.client.get(f"{base}/repos/{repo}/releases?per_page={per_page}", headers=headers))
            except FetchError as exc:
                warnings.append(f"{repo}: {exc.code}")
                continue
            if not isinstance(rows, list):
                warnings.append(f"{repo}: 返回结构异常")
                continue
            for row in rows:
                if not isinstance(row, dict) or row.get("draft"):
                    continue
                tag = row.get("tag_name") or row.get("name") or str(row.get("id"))
                title = clean_text(row.get("name") or tag, limit=300)
                summary = clean_text(row.get("body"), limit=800)
                items.append(
                    {
                        "external_id": f"{repo}#{tag}",
                        "title": f"{repo} {title}".strip(),
                        "canonical_url": canonicalize_url(row.get("html_url")),
                        "published_at": to_utc_iso(row.get("published_at") or row.get("created_at")),
                        "summary": summary,
                        "author": (row.get("author") or {}).get("login"),
                        "kind": "release",
                        "content_hash": content_hash(tag, title, summary),
                        "metadata": {"repo": repo, "prerelease": bool(row.get("prerelease")), "tag": tag},
                    }
                )
        return CollectResult(items=items, warnings=warnings, raw_bytes=0)


class OpenMeteoCollector(Collector):
    adapter = "openmeteo"

    def collect(self, source: dict[str, Any], subscription: dict[str, Any]) -> CollectResult:
        cfg = source.get("collector") or {}
        params = {**(cfg.get("params") or {}), **(subscription.get("parameters") or {})}
        base = str((source.get("access") or {}).get("base_url") or "https://api.open-meteo.com/v1/forecast").rstrip("/")
        latitude = float(params.get("latitude", 31.30))
        longitude = float(params.get("longitude", 120.62))
        hourly = params.get("hourly") or ["temperature_2m", "relative_humidity_2m", "precipitation"]
        if isinstance(hourly, str):
            hourly = [hourly]
        allowed = {"temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "cloud_cover", "apparent_temperature"}
        metrics = [m for m in hourly if m in allowed][:6] or ["temperature_2m"]
        days = min(int(params.get("forecast_days") or 3), 7)
        url = (
            f"{base}?latitude={latitude}&longitude={longitude}"
            f"&hourly={','.join(metrics)}&forecast_days={days}&timezone=UTC"
        )
        payload = _json(self.client.get(url))
        hourly_block = (payload or {}).get("hourly") or {}
        times = hourly_block.get("time") or []
        units = (payload or {}).get("hourly_units") or {}
        series_key = f"{latitude:.4f},{longitude:.4f}"
        observations: list[dict[str, Any]] = []
        for index, timestamp in enumerate(times[: 24 * days]):
            observed = to_utc_iso(timestamp)
            if not observed:
                continue
            for metric in metrics:
                series = hourly_block.get(metric) or []
                if index >= len(series) or series[index] is None:
                    continue
                observations.append(
                    {
                        "series_key": series_key,
                        "observed_at": observed,
                        "metric": metric,
                        "value": float(series[index]),
                        "unit": units.get(metric),
                        "params": {"latitude": latitude, "longitude": longitude},
                    }
                )
        return CollectResult(observations=observations, raw_bytes=0)
