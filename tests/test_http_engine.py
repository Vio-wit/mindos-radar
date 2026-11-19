"""R1：HTTP 客户端安全边界（SSRF、协议、降级、大小限制）。"""
from __future__ import annotations

import unittest

from helpers import make_response
from radar import db, store
from radar.engine import build_client, collect_subscription
from radar.httpclient import FetchError, _is_blocked, validate_url
import ipaddress


class UrlValidationTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
            with self.assertRaises(FetchError):
                validate_url(url)

    def test_rejects_loopback_and_private(self):
        for url in ("http://127.0.0.1/x", "http://10.0.0.5/x", "http://192.168.1.1/x", "http://169.254.169.254/latest/meta-data"):
            with self.assertRaises(FetchError) as ctx:
                validate_url(url)
            self.assertEqual(ctx.exception.code, "blocked_address")

    def test_rejects_embedded_credentials(self):
        with self.assertRaises(FetchError) as ctx:
            validate_url("http://user:pass@example.com/x")
        self.assertEqual(ctx.exception.code, "url_credentials")

    def test_allow_private_opt_in(self):
        self.assertEqual(validate_url("http://127.0.0.1/x", allow_private=True), "http://127.0.0.1/x")

    def test_blocked_networks(self):
        for address in ("10.1.2.3", "172.16.0.1", "192.168.0.1", "100.64.0.1", "0.0.0.0", "::1", "fe80::1"):
            self.assertTrue(_is_blocked(ipaddress.ip_address(address)), address)
        for address in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"):
            self.assertFalse(_is_blocked(ipaddress.ip_address(address)), address)


class _StubCollector:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def collect(self, source, subscription):
        if self.error:
            raise self.error
        return self.result


class EngineTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self._tmp.name) / "e.sqlite3")
        with self.conn:
            self.conn.execute(
                "insert into radar_sources(id,name,category,config_json,verification_status,cost_tier,origin,updated_at) "
                "values('fx','FX','technology',?,'integrated','F0','curated',?)",
                ('{"access":{"base_url":"https://example.com"},"collector":{"adapter":"rss","max_items_per_run":5},"rights":{"fulltext":"review_required"},"verification":{"status":"integrated"}}', store.now_iso()),
            )
        self.sub = store.create_subscription(self.conn, "fx", enabled=True, interval_minutes=60)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _patch(self, stub):
        import radar.engine as engine

        self._original = engine.build_collector
        engine.build_collector = lambda adapter, client, settings=None: stub

    def tearDown_patch(self):
        import radar.engine as engine

        engine.build_collector = self._original

    def test_success_run_records_items(self):
        from radar.collectors.base import CollectResult

        self._patch(_StubCollector(CollectResult(items=[
            {"external_id": "a1", "title": "标题一", "summary": "摘要", "content_hash": "h1", "published_at": "2026-01-01T00:00:00Z"},
            {"external_id": "a1", "title": "标题一", "summary": "摘要", "content_hash": "h1"},
        ])))
        try:
            result = collect_subscription(self.conn, self.sub, client=object())
        finally:
            self.tearDown_patch()
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["unchanged"], 1)
        run = store.list_runs(self.conn)[0]
        self.assertEqual(run["status"], "SUCCEEDED")
        self.assertEqual(store.count_items(self.conn), 1)
        sub = store.get_subscription(self.conn, self.sub)
        self.assertIsNotNone(sub["last_success_at"])
        self.assertIsNotNone(sub["next_run_at"])

    def test_failure_is_recorded_not_raised(self):
        self._patch(_StubCollector(error=FetchError("network", "连接超时")))
        try:
            result = collect_subscription(self.conn, self.sub, client=object())
        finally:
            self.tearDown_patch()
        self.assertEqual(result["status"], "FAILED")
        run = store.list_runs(self.conn)[0]
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(run["error_code"], "network")

    def test_disabled_subscription_skipped_without_force(self):
        store.set_subscription(self.conn, self.sub, enabled=False)
        result = collect_subscription(self.conn, self.sub, client=object())
        self.assertEqual(result["status"], "SKIPPED")
        result = collect_subscription(self.conn, self.sub, client=object(), force=True)
        self.assertNotEqual(result["status"], "SKIPPED")

    def test_not_modified_keeps_counter_zero(self):
        from radar.collectors.base import CollectResult

        self._patch(_StubCollector(CollectResult(not_modified=True)))
        try:
            result = collect_subscription(self.conn, self.sub, client=object())
        finally:
            self.tearDown_patch()
        self.assertEqual(result["status"], "NOT_MODIFIED")
        self.assertEqual(store.count_items(self.conn), 0)

    def test_client_defaults_are_safe(self):
        client = build_client()
        self.assertFalse(client.allow_private)
        self.assertGreater(client.max_bytes, 0)


if __name__ == "__main__":
    unittest.main()
