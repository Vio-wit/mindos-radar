"""规则分析与 AI 输出 schema 测试（A4 / L5-07）。"""
from __future__ import annotations

import unittest

from helpers import DbTestCase

from radar import analysis, registry, store
from radar.ai import schema


class AnalysisTests(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        result = registry.load_registry()
        registry.sync_to_db(self.conn, result.sources, now=store.now_iso())

    def _item(self, title: str, summary: str, external_id: str) -> int:
        outcome, item_id = store.upsert_item(
            self.conn,
            source_id="pypi-requests",
            item={
                "external_id": external_id,
                "title": title,
                "summary": summary,
                "canonical_url": f"https://example.com/{external_id}",
                "content_hash": f"hash-{external_id}",
            },
            rights_state="metadata_only",
        )
        return int(item_id)

    def test_classify_and_entities(self) -> None:
        category, confidence = analysis.classify("NVIDIA 发布新 GPU 架构", "AI 芯片与大模型训练")
        self.assertEqual(category, "ai")
        self.assertGreater(confidence, 0.4)
        names = [entity["name"] for entity in analysis.entities("NVIDIA 与 OpenAI 合作")]
        self.assertIn("NVIDIA", names)
        self.assertIn("OpenAI", names)

    def test_store_rule_analysis_is_idempotent(self) -> None:
        item_id = self._item("OpenAI 更新模型", "新模型支持更长上下文", "a-1")
        first = analysis.store_rule_analysis(self.conn, item_id)
        second = analysis.store_rule_analysis(self.conn, item_id)
        self.assertEqual(first["primary_category"], second["primary_category"])
        count = self.conn.execute(
            "select count(*) from radar_analyses where item_id=? and provider='rule'", (item_id,)
        ).fetchone()[0]
        self.assertEqual(count, 1)
        latest = store.get_latest_analysis(self.conn, item_id)
        self.assertEqual(latest["output"]["method"], "rule")

    def test_analyze_items_missing_only(self) -> None:
        self._item("标题 A", "摘要", "a-1")
        self._item("标题 B", "摘要", "a-2")
        first = analysis.analyze_items(self.conn, missing_only=True)
        self.assertEqual(first["analyzed"], 2)
        second = analysis.analyze_items(self.conn, missing_only=True)
        self.assertEqual(second["analyzed"], 0)

    def test_quality_and_trend(self) -> None:
        self._item("NVIDIA 新闻", "摘要", "a-1")
        quality = analysis.quality_metrics(self.conn)
        self.assertEqual(quality["items"], 1)
        self.assertEqual(quality["missing_published_at"], 1)
        trend = analysis.trend(self.conn, days=3)
        self.assertEqual(len(trend), 3)
        self.assertEqual(sum(row["total"] for row in trend), 1)


class SchemaTests(unittest.TestCase):
    def test_accepts_valid_payload(self) -> None:
        payload = {
            "summary": "这是一段有效摘要。",
            "tags": ["AI"],
            "key_points": ["要点"],
            "entities": [{"name": "NVIDIA", "type": "org", "evidence": "NVIDIA"}],
            "relevance": 0.8,
        }
        self.assertEqual(schema.validate_output(payload, input_text="NVIDIA 发布"), [])
        normalized = schema.normalize(payload)
        self.assertEqual(normalized["method"], "llm")

    def test_rejects_missing_summary_and_bad_evidence(self) -> None:
        errors = schema.validate_output({"entities": [{"name": "X", "evidence": "不存在"}]}, input_text="abc")
        self.assertTrue(any("summary" in error for error in errors))
        self.assertTrue(any("evidence" in error for error in errors))

    def test_rejects_bad_types_and_bounds(self) -> None:
        errors = schema.validate_output({
            "summary": "ok",
            "tags": "not-a-list",
            "relevance": 9,
            "entities": [{"name": "", "type": "alien"}],
        })
        self.assertTrue(any("tags" in error for error in errors))
        self.assertTrue(any("relevance" in error for error in errors))
        self.assertTrue(any("alien" in error for error in errors))

    def test_check_and_normalize_raises_on_invalid(self) -> None:
        with self.assertRaises(ValueError):
            schema.check_and_normalize('{"summary": ""}')


if __name__ == "__main__":
    unittest.main()
