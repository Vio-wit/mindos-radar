"""R1：注册表 schema 校验、秘密拒绝、导入与筛选。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import DbTestCase
from radar import registry, store


def sample_source(**overrides):
    data = {
        "id": "sample-feed",
        "name": "Sample Feed",
        "category": "technology",
        "schema_version": 1,
        "provider_url": "https://example.com",
        "docs_url": "https://example.com/docs",
        "access": {"type": "rss_atom", "base_url": "https://example.com/feed.xml", "auth": "none", "cost_tier": "F0"},
        "collector": {"adapter": "rss", "default_interval_minutes": 360, "max_items_per_run": 20},
        "verification": {"status": "live_test_pass"},
        "tag": "x",
    }
    data.update(overrides)
    return data


class RegistryValidationTests(unittest.TestCase):
    def test_valid_source_passes(self):
        self.assertEqual(registry.validate(sample_source()), [])

    def test_bad_id_and_category(self):
        errors = registry.validate(sample_source(id="Bad ID", category="nope"))
        self.assertTrue(any("id" in error for error in errors))
        self.assertTrue(any("category" in error for error in errors))

    def test_defaults_are_restrictive(self):
        data = registry.normalize(sample_source())
        self.assertFalse(data["enabled_by_default"])
        self.assertEqual(data["rights"]["download"], "prohibited_by_default")
        self.assertEqual(data["rights"]["fulltext"], "review_required")

    def test_secret_values_rejected(self):
        data = sample_source()
        data["access"]["api_key"] = "sk-real-secret"
        errors = registry.validate(data)
        self.assertTrue(any("秘密" in error for error in errors))

    def test_secret_ref_name_allowed(self):
        data = sample_source()
        data["access"]["auth"] = "api_key"
        data["access"]["secret_ref"] = "my_api_key"
        self.assertEqual(registry.validate(data), [])

    def test_bad_cost_tier_and_adapter_interval(self):
        data = sample_source()
        data["access"]["cost_tier"] = "FREE"
        data["collector"]["default_interval_minutes"] = 0
        errors = registry.validate(data)
        self.assertTrue(any("cost_tier" in error for error in errors))
        self.assertTrue(any("default_interval_minutes" in error for error in errors))

    def test_enabled_by_default_must_be_false(self):
        errors = registry.validate(sample_source(enabled_by_default=True))
        self.assertTrue(any("enabled_by_default" in error for error in errors))


class RegistryLoadTests(unittest.TestCase):
    def test_load_documents_list_and_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "registry"
            (root / "curated").mkdir(parents=True)
            (root / "curated" / "a.yaml").write_text(
                "sources:\n  - id: a-one\n    name: A\n    category: news\n"
                "    access: {type: rss_atom, base_url: 'https://a.example/feed'}\n"
                "    collector: {adapter: rss}\n"
                "    verification: {status: docs_verified}\n",
                encoding="utf-8",
            )
            (root / "curated" / "b.yaml").write_text(
                "id: b-two\nname: B\ncategory: books\n"
                "access: {type: rest_json, base_url: 'https://b.example/api'}\n"
                "collector: {adapter: jsonmap}\n"
                "verification: {status: candidate}\n",
                encoding="utf-8",
            )
            result = registry.load_registry(root)
            self.assertTrue(result.ok, result.errors)
            self.assertEqual(set(result.sources), {"a-one", "b-two"})
            self.assertEqual(len(result.warnings), 2)

    def test_duplicate_id_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = (
                "id: dup\nname: X\ncategory: news\n"
                "access: {type: rss_atom, base_url: 'https://x.example/feed'}\n"
                "collector: {adapter: rss}\nverification: {status: candidate}\n"
            )
            (root / "one.yaml").write_text(body, encoding="utf-8")
            (root / "two.yaml").write_text(body, encoding="utf-8")
            result = registry.load_registry(root)
            self.assertFalse(result.ok)
            self.assertTrue(any("重复" in error for error in result.errors))

    def test_filter_sources(self):
        sources = [
            registry.normalize(sample_source(id="a", category="news", access={"cost_tier": "F0", "type": "rss_atom"})),
            registry.normalize(sample_source(id="b", category="books", access={"cost_tier": "F1", "type": "rest_json"})),
        ]
        self.assertEqual([s["id"] for s in registry.filter_sources(sources, category="books")], ["b"])
        self.assertEqual([s["id"] for s in registry.filter_sources(sources, cost_tier="F0")], ["a"])
        self.assertEqual([s["id"] for s in registry.filter_sources(sources, query="sample")], ["b", "a"])


class RegistrySyncTests(DbTestCase):
    def test_sync_idempotent(self):
        sources = {"sample-feed": registry.normalize(sample_source())}
        first = registry.sync_to_db(self.conn, sources, now=store.now_iso())
        self.assertEqual(first, {"inserted": 1, "updated": 0, "total": 1})
        second = registry.sync_to_db(self.conn, sources, now=store.now_iso())
        self.assertEqual(second["updated"], 1)
        row = store.get_source(self.conn, "sample-feed")
        self.assertEqual(row["verification_status"], "live_test_pass")
        self.assertEqual(row["config"]["access"]["cost_tier"], "F0")

    def test_import_candidates_skips_duplicates(self):
        (Path(self.tmp) / "cand.yaml").write_text(
            "sources:\n  - id: imported-one\n    name: I\n    category: news\n"
            "    access: {type: rest_json, base_url: 'https://i.example/api'}\n"
            "    collector: {adapter: jsonmap}\n    verification: {status: integrated}\n",
            encoding="utf-8",
        )
        stats = registry.import_candidates(self.conn, Path(self.tmp) / "cand.yaml", now=store.now_iso())
        self.assertEqual(stats["imported"], 1)
        row = store.get_source(self.conn, "imported-one")
        self.assertEqual(row["verification_status"], "candidate")  # 导入源不得直接标记为已验证
        again = registry.import_candidates(self.conn, Path(self.tmp) / "cand.yaml", now=store.now_iso())
        self.assertEqual(again["skipped_duplicate"], 1)


if __name__ == "__main__":
    unittest.main()
