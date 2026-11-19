"""本地导入（C2-05）测试：字段别名、预览快照、幂等提交、坏数据不污染。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import DbTestCase  # noqa: F401  (确保 radar 在 sys.path)

from radar import importer, registry, store


class ImportTests(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        result = registry.load_registry()
        registry.sync_to_db(self.conn, result.sources, now=store.now_iso())
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()
        super().tearDown()

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_preview_maps_common_aliases(self) -> None:
        path = self._write(
            "sample.csv",
            "id,title,description,link,date,author\n"
            "x-1,标题一,摘要一,https://example.com/1,2026-11-15T00:00:00Z,作者\n",
        )
        result = importer.preview(path)
        self.assertEqual(result["format"], "csv")
        self.assertEqual(result["mapped_rows"], 1)
        sample = result["sample"][0]
        self.assertEqual(sample["external_id"], "x-1")
        self.assertEqual(sample["title"], "标题一")
        self.assertEqual(sample["url"], "https://example.com/1")
        self.assertTrue(sample["published_at"].startswith("2026-11-15"))

    def test_preview_reports_missing_external_id(self) -> None:
        path = self._write("bad.csv", "title\n只有标题\n")
        result = importer.preview(path)
        self.assertEqual(result["mapped_rows"], 0)
        self.assertTrue(result["errors"])

    def test_commit_is_idempotent(self) -> None:
        path = self._write(
            "sample.json",
            '[{"id":"j-1","title":"JSON 标题","summary":"摘要","url":"https://example.com/j1",'
            '"published_at":"2026-11-16T01:00:00Z"}]',
        )
        preview = importer.preview(path)
        first = importer.commit(self.conn, "pypi-requests", path, expected_snapshot=preview["snapshot"])
        self.assertEqual(first["inserted"], 1)
        second = importer.commit(self.conn, "pypi-requests", path, expected_snapshot=preview["snapshot"])
        self.assertEqual(second["unchanged"], 1)
        items = store.list_items(self.conn, query="JSON 标题")
        self.assertEqual(len(items), 1)

    def test_commit_rejects_changed_file(self) -> None:
        path = self._write("sample.json", '[{"id":"j-2","title":"A"}]')
        preview = importer.preview(path)
        path.write_text('[{"id":"j-2","title":"B"}]', encoding="utf-8")
        with self.assertRaises(importer.ImportProblem):
            importer.commit(self.conn, "pypi-requests", path, expected_snapshot=preview["snapshot"])

    def test_commit_requires_registered_source(self) -> None:
        path = self._write("sample.json", '[{"id":"j-3","title":"A"}]')
        with self.assertRaises(importer.ImportProblem):
            importer.commit(self.conn, "no-such-source", path)

    def test_large_file_rejected(self) -> None:
        path = self.dir / "large.csv"
        path.write_bytes(b"id,title\n" + b"x" * (importer.MAX_BYTES + 1))
        with self.assertRaises(importer.ImportProblem):
            importer.preview(path)

    def test_jsonl_and_xss_title_is_sanitized(self) -> None:
        path = self._write("sample.jsonl", '{"id":"l-1","title":"<script>alert(1)</script>"}\n')
        preview = importer.preview(path)
        self.assertEqual(preview["format"], "jsonl")
        self.assertNotIn("<script>", preview["sample"][0]["title"])
        importer.commit(self.conn, "pypi-requests", path)
        item = store.list_items(self.conn, limit=5)[0]
        self.assertNotIn("<script>", item["title"])


if __name__ == "__main__":
    unittest.main()
