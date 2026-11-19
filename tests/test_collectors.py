"""R1/C2：RSS/Atom 解析、JSON 映射、官方 API 适配器。"""
from __future__ import annotations

import json
import unittest

from helpers import FakeClient, make_response
from radar.collectors import ADAPTERS, build_collector, parse_feed
from radar.collectors.jsonmap import JsonMapCollector, get_path
from radar.httpclient import FetchError

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>示例源</title>
    <item>
      <guid>post-1</guid>
      <title>第一条 &amp; 新闻</title>
      <link>https://example.com/p1?utm_source=rss&amp;id=7</link>
      <pubDate>Mon, 03 Nov 2025 10:00:00 GMT</pubDate>
      <description><![CDATA[<p>这是 <b>摘要</b>。</p>]]></description>
      <dc:creator>作者甲</dc:creator>
    </item>
    <item>
      <title>缺 id 但有 link</title>
      <link>https://example.com/p2</link>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 示例</title>
  <entry>
    <id>urn:uuid:1</id>
    <title>Atom 条目</title>
    <link rel="alternate" href="https://example.org/a1"/>
    <published>2026-01-02T03:04:05Z</published>
    <summary>摘要内容</summary>
    <author><name>乙</name></author>
  </entry>
</feed>
"""


class RssParseTests(unittest.TestCase):
    def test_rss_items(self):
        items, warnings = parse_feed(RSS_SAMPLE)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["external_id"], "post-1")
        self.assertEqual(first["title"], "第一条 & 新闻")
        self.assertEqual(first["canonical_url"], "https://example.com/p1?id=7")
        self.assertEqual(first["published_at"], "2025-11-03T10:00:00Z")
        self.assertEqual(first["summary"], "这是 摘要。")
        self.assertEqual(first["author"], "作者甲")
        self.assertEqual(first["content_hash"], items[0]["content_hash"])
        self.assertEqual(items[1]["external_id"], "https://example.com/p2")

    def test_atom_items(self):
        items, _ = parse_feed(ATOM_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["external_id"], "urn:uuid:1")
        self.assertEqual(items[0]["canonical_url"], "https://example.org/a1")
        self.assertEqual(items[0]["published_at"], "2026-01-02T03:04:05Z")
        self.assertEqual(items[0]["author"], "乙")

    def test_entity_declaration_rejected(self):
        payload = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss/>'
        with self.assertRaises(ValueError):
            parse_feed(payload)

    def test_max_items_cap(self):
        items, _ = parse_feed(RSS_SAMPLE, max_items=1)
        self.assertEqual(len(items), 1)


class JsonMapTests(unittest.TestCase):
    def test_get_path_variants(self):
        data = {"a": {"b": [{"c": 1}, {"c": 2}]}, "list": [1, 2]}
        self.assertEqual(get_path(data, "a.b[1].c"), 2)
        self.assertEqual(get_path(data, "a.b.0.c"), 1)
        self.assertEqual(get_path(data, "list[0]"), 1)
        self.assertIsNone(get_path(data, "missing.x"))

    def test_collect_maps_fields_and_template(self):
        payload = {
            "results": [
                {"id": "W1", "display_name": "Paper One", "doi": "https://doi.org/10.1/x", "publication_date": "2026-02-03", "authorships": [{"author": {"display_name": "Alice"}}]},
            ]
        }
        client = FakeClient({"https://api.example/works": make_response(json.dumps(payload))})
        source = {
            "id": "demo", "name": "Demo",
            "access": {"base_url": "https://api.example/works"},
            "collector": {"adapter": "jsonmap", "params": {
                "items_path": "results",
                "fields": {
                    "external_id": "id", "title": ["display_name", "title"], "url": "doi",
                    "published_at": "publication_date", "author": "authorships[0].author.display_name",
                    "summary": ["abstract"],
                },
            }, "max_items_per_run": 5},
        }
        result = JsonMapCollector(client).collect(source, {"parameters": {}})
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item["external_id"], "W1")
        self.assertEqual(item["title"], "Paper One")
        self.assertEqual(item["canonical_url"], "https://doi.org/10.1/x")
        self.assertEqual(item["published_at"], "2026-02-03T00:00:00Z")
        self.assertEqual(item["author"], "Alice")

    def test_missing_external_id_is_warning(self):
        payload = {"results": [{"title": "no id"}, {"id": "x", "title": "ok"}]}
        client = FakeClient({"https://api.example/works": make_response(json.dumps(payload))})
        source = {
            "id": "demo", "name": "Demo", "access": {"base_url": "https://api.example/works"},
            "collector": {"adapter": "jsonmap", "params": {"items_path": "results", "fields": {"external_id": "id", "title": "title"}}},
        }
        result = JsonMapCollector(client).collect(source, {"parameters": {}})
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(result.warnings), 1)

    def test_requires_external_id_mapping(self):
        client = FakeClient({"https://api.example/works": make_response("{}")})
        source = {"id": "d", "name": "D", "access": {"base_url": "https://api.example/works"},
                  "collector": {"adapter": "jsonmap", "params": {"fields": {"title": "t"}}}}
        with self.assertRaises(FetchError):
            JsonMapCollector(client).collect(source, {"parameters": {}})


class HackerNewsTests(unittest.TestCase):
    def test_collect_stories(self):
        client = FakeClient({
            "https://hacker-news.firebaseio.com/v0/topstories.json": make_response("[101, 102]"),
            "https://hacker-news.firebaseio.com/v0/item/101.json": make_response(json.dumps(
                {"id": 101, "title": "Show HN: Radar", "url": "https://example.com/r?utm_source=hn", "time": 1767225600, "by": "alice", "score": 42, "descendants": 7}
            )),
            "https://hacker-news.firebaseio.com/v0/item/102.json": make_response(json.dumps({"id": 102, "deleted": True})),
        })
        source = {"id": "hn", "name": "HN", "access": {"base_url": "https://hacker-news.firebaseio.com/v0"},
                  "collector": {"adapter": "hackernews", "params": {"max_items": 5}}}
        result = build_collector("hackernews", client).collect(source, {"parameters": {}})
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item["external_id"], "101")
        self.assertEqual(item["canonical_url"], "https://example.com/r")
        self.assertEqual(item["published_at"], "2026-01-01T00:00:00Z")
        self.assertIn("42 points", item["summary"])
        self.assertEqual(item["metadata"]["comments"], 7)


class GitHubReleasesTests(unittest.TestCase):
    def test_collect_releases_and_skip_drafts(self):
        payload = [
            {"id": 1, "tag_name": "v1.2.0", "name": "v1.2.0", "html_url": "https://github.com/x/y/releases/tag/v1.2.0",
             "published_at": "2026-03-04T05:06:07Z", "body": "## Changes\n- fixed bug", "author": {"login": "maint"}},
            {"id": 2, "tag_name": "v2", "draft": True, "html_url": "https://github.com/x/y/releases/tag/v2"},
        ]
        client = FakeClient({"https://api.github.com/repos/x/y/releases": make_response(json.dumps(payload))})
        source = {"id": "gh", "name": "GH", "access": {"base_url": "https://api.github.com"},
                  "collector": {"adapter": "github_releases", "params": {"repos": ["x/y"], "per_page": 5}}}
        result = build_collector("github_releases", client).collect(source, {"parameters": {}})
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item["external_id"], "x/y#v1.2.0")
        self.assertIn("## Changes", item["summary"])
        self.assertEqual(item["kind"], "release")
        self.assertFalse(item["metadata"]["prerelease"])

    def test_requires_repos(self):
        client = FakeClient({})
        source = {"id": "gh", "name": "GH", "access": {}, "collector": {"adapter": "github_releases"}}
        with self.assertRaises(FetchError):
            build_collector("github_releases", client).collect(source, {"parameters": {}})


class OpenMeteoTests(unittest.TestCase):
    def test_collect_observations(self):
        payload = {
            "hourly_units": {"temperature_2m": "°C", "precipitation": "mm"},
            "hourly": {
                "time": ["2026-04-01T00:00", "2026-04-01T01:00"],
                "temperature_2m": [12.5, None],
                "precipitation": [0.0, 0.4],
            },
        }
        client = FakeClient({"https://api.open-meteo.com/v1/forecast": make_response(json.dumps(payload))})
        source = {"id": "weather", "name": "W", "access": {"base_url": "https://api.open-meteo.com/v1/forecast"},
                  "collector": {"adapter": "openmeteo", "params": {"latitude": 31.3, "longitude": 120.6, "hourly": ["temperature_2m", "precipitation"], "forecast_days": 1}}}
        result = build_collector("openmeteo", client).collect(source, {"parameters": {}})
        self.assertEqual(len(result.items), 0)
        self.assertEqual(len(result.observations), 3)
        metrics = {row["metric"] for row in result.observations}
        self.assertEqual(metrics, {"temperature_2m", "precipitation"})
        self.assertEqual(result.observations[0]["unit"], "°C")


class AdapterRegistryTests(unittest.TestCase):
    def test_unknown_adapter(self):
        with self.assertRaises(ValueError):
            build_collector("does-not-exist", FakeClient({}))

    def test_known_adapters(self):
        self.assertEqual(set(ADAPTERS), {"rss", "jsonmap", "hackernews", "github_releases", "openmeteo"})


if __name__ == "__main__":
    unittest.main()
