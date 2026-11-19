"""导出安全与统计序列（A4-01/02/04）测试。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from helpers import DbTestCase

from radar import export, registry, stats, store


class ExportTests(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        result = registry.load_registry()
        registry.sync_to_db(self.conn, result.sources, now=store.now_iso())
        store.upsert_item(
            self.conn, source_id="pypi-requests",
            item={
                "external_id": "formula-1",
                "title": "=HYPERLINK(\"https://evil.example\")",
                "summary": "@SUM(1+1)",
                "content_hash": "formula-hash-1",
            },
            rights_state="metadata_only",
        )

    def test_csv_formula_injection_is_neutralized(self) -> None:
        content = export.export_csv(self.conn)
        self.assertIn("'=HYPERLINK", content)
        self.assertIn("'@SUM", content)
        self.assertNotIn(",=HYPERLINK", content)

    def test_json_and_markdown_hold_original_text(self) -> None:
        payload = export.export_json(self.conn)
        self.assertIn("HYPERLINK", payload)
        markdown = export.export_markdown(self.conn)
        self.assertIn("HYPERLINK", markdown)

    def test_export_writes_file_under_data_scope(self) -> None:
        target = self.tmp / "export.csv"
        written = export.write_export(self.conn, target, fmt="csv")
        self.assertEqual(written, target)
        self.assertTrue(target.is_file())


class SeriesTests(DbTestCase):
    def test_numeric_series_does_not_zero_fill(self) -> None:
        now = datetime.now(timezone.utc)
        present = now.isoformat(timespec="seconds")
        missing = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                "insert into radar_observations(source_id, series_key, observed_at, metric, value, unit) "
                "values('open-meteo-suzhou','suzhou',?, 'temperature_2m',21.5,'°C')", (present,)
            )
            self.conn.execute(
                "insert into radar_observations(source_id, series_key, observed_at, metric, value, unit) "
                "values('open-meteo-suzhou','suzhou',?, 'temperature_2m',null,'°C')", (missing,)
            )
        rows = stats.numeric_series(self.conn, source_id="open-meteo-suzhou")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 21.5)
        self.assertEqual(rows[0]["unit"], "°C")

    def test_keyword_frequency_is_reproducible(self) -> None:
        store.upsert_item(
            self.conn, source_id="hacker-news",
            item={"external_id": "kw-1", "title": "NVIDIA GPU 与 NVIDIA AI", "content_hash": "kw-1"},
            rights_state="metadata_only",
        )
        first = stats.keyword_frequency(self.conn)
        second = stats.keyword_frequency(self.conn)
        self.assertEqual(first, second)
        terms = {row["term"] for row in first}
        self.assertTrue(terms)


if __name__ == "__main__":
    unittest.main()
