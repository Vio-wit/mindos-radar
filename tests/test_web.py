"""本地 Web（U3）测试：页面、CSRF/Origin/Host、XSS 转义、API、导入与 AI 假运行。"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from helpers import make_response  # noqa: F401  (radar 路径)

from radar import db, registry, store
from radar.web import create_server


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._env_backup = {key: os.environ.get(key) for key in ("RADAR_DATA_DIR", "RADAR_NOTICE_DB")}
        os.environ["RADAR_DATA_DIR"] = str(self.dir)
        os.environ["RADAR_NOTICE_DB"] = str(self.dir / "missing-notices.sqlite3")
        conn = db.connect()
        result = registry.load_registry()
        registry.sync_to_db(conn, result.sources, now=store.now_iso())
        _, self.item_id = store.upsert_item(
            conn,
            source_id="pypi-requests",
            item={
                "external_id": "web-1",
                "title": "NVIDIA 发布 <script>alert(1)</script>",
                "summary": "用于测试转义的摘要",
                "canonical_url": "https://example.com/web-1",
                "content_hash": "web-hash-1",
            },
            rights_state="metadata_only",
        )
        conn.close()
        self.server, self.app = create_server(port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _request(self, path: str, *, data: dict | None = None, headers: dict | None = None,
                 method: str | None = None, host: str | None = None) -> tuple[int, dict, str]:
        body = None
        base_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if data is not None:
            if headers and headers.get("Content-Type") == "application/json":
                body = json.dumps(data).encode()
            else:
                body = urllib.parse.urlencode(data).encode()
                base_headers["Content-Type"] = "application/x-www-form-urlencoded"
        base_headers.update(headers or {})
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body,
            headers=base_headers, method=method or ("POST" if body is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            headers = dict(exc.headers)
            status = exc.code
            exc.close()
            return status, headers, body

    def _form(self, path: str, data: dict) -> tuple[int, str]:
        status, _, body = self._request(path, data={"..": "x", **data, "_csrf": self.app.csrf})
        return status, body

    def _json(self, path: str, payload: dict | None = None, method: str | None = None) -> tuple[int, dict]:
        status, _, body = self._request(
            path, data=payload, method=method,
            headers={"Content-Type": "application/json", "X-CSRF-Token": self.app.csrf},
        )
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, {"raw": body}

    # -- tests -----------------------------------------------------------
    def test_pages_render_with_security_headers(self) -> None:
        for path in ("/", "/feed", "/sources", "/subscriptions", "/runs", "/topics", "/stats",
                     "/ai", "/import", "/settings", "/notices"):
            status, headers, body = self._request(path)
            self.assertEqual(status, 200, path)
            self.assertIn("Content-Security-Policy", headers)
            self.assertEqual(headers.get("X-Frame-Options"), "DENY")
            self.assertIn("MindOS Radar", body)

    def test_xss_is_escaped(self) -> None:
        status, _, body = self._request("/feed")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)

    def test_host_header_allowlist(self) -> None:
        status, _, _ = self._request("/", host="evil.example.com")
        self.assertEqual(status, 400)

    def test_csrf_required(self) -> None:
        status, _, _ = self._request("/topics", data={"name": "x", "include": "y"})
        self.assertEqual(status, 403)

    def test_origin_must_match(self) -> None:
        status, _, _ = self._request(
            "/topics", data={"name": "x", "include": "y", "_csrf": self.app.csrf},
            headers={"Origin": "https://evil.example.com"},
        )
        self.assertEqual(status, 403)

    def test_cross_site_get_not_possible_for_state(self) -> None:
        # 状态修改只接受带 CSRF 的 POST；GET 详情不改变数据
        status, _, _ = self._request(f"/feed/{self.item_id}")
        self.assertEqual(status, 200)
        conn = db.connect()
        item = store.get_item(conn, self.item_id)
        conn.close()
        self.assertIsNone(item["read_at"])

    def test_subscription_form_and_collect_guard(self) -> None:
        status, body = self._form("/subscriptions", {"source_id": "pypi-requests", "interval": "120"})
        self.assertEqual(status, 200)
        conn = db.connect()
        subs = store.list_subscriptions(conn)
        conn.close()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["interval_minutes"], 120)
        self.assertFalse(subs[0]["enabled"])

    def test_item_state_form_and_api(self) -> None:
        status, body = self._form(f"/feed/{self.item_id}/state", {"read": "1", "favorite": "1", "note": "测试备注"})
        self.assertEqual(status, 200)
        status, payload = self._json(f"/api/radar/items/{self.item_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["note"], "测试备注")
        self.assertTrue(payload["favorite"])
        self.assertIsNotNone(payload["read_at"])

    def test_api_sources_and_filters(self) -> None:
        status, payload = self._json("/api/radar/sources?limit=5&status=integrated")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 13)
        self.assertTrue(all(item["verification"]["status"] == "integrated" for item in payload["items"]))

    def test_unverified_source_probe_is_blocked(self) -> None:
        status, payload = self._json("/api/radar/sources/openai-news/probe", {})
        self.assertEqual(status, 403)
        self.assertIn("error", payload)

    def test_api_items_has_next(self) -> None:
        conn = db.connect()
        for index in range(3):
            store.upsert_item(
                conn, source_id="pypi-requests",
                item={"external_id": f"extra-{index}", "title": f"条目 {index}", "content_hash": f"h{index}"},
                rights_state="metadata_only",
            )
        conn.close()
        status, payload = self._json("/api/radar/items?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 2)
        self.assertTrue(payload["has_next"])

    def test_import_preview_and_commit(self) -> None:
        csv_path = self.dir / "import.csv"
        csv_path.write_text(
            "id,title,summary,url,date\nimp-1,导入标题,导入摘要,https://example.com/imp1,2026-11-16T00:00:00Z\n",
            encoding="utf-8",
        )
        status, body = self._form("/import/preview", {"path": str(csv_path)})
        self.assertEqual(status, 200)
        snapshot = re.search(r'name="snapshot" value="([a-f0-9]+)"', body)
        self.assertIsNotNone(snapshot)
        status, _ = self._form(
            "/import/commit",
            {"path": str(csv_path), "snapshot": snapshot.group(1), "source_id": "pypi-requests"},
        )
        self.assertEqual(status, 200)
        conn = db.connect()
        items = store.list_items(conn, query="导入标题")
        conn.close()
        self.assertEqual(len(items), 1)

    def test_ai_preview_create_and_fake_run(self) -> None:
        status, payload = self._json("/api/radar/ai/preview", {"item_ids": [self.item_id], "task_type": "brief"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        status, payload = self._json("/api/radar/ai/jobs", {"item_ids": [self.item_id], "task_type": "brief"})
        self.assertEqual(status, 201)
        job_id = payload["job_id"]
        status, payload = self._json(f"/api/radar/ai/jobs/{job_id}/run", {"fake": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "COMPLETED")
        status, payload = self._json(f"/api/radar/ai/jobs/{job_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "COMPLETED")

    def test_real_ai_run_without_key_is_refused(self) -> None:
        status, payload = self._json("/api/radar/ai/jobs", {"item_ids": [self.item_id]})
        job_id = payload["job_id"]
        status, payload = self._json(f"/api/radar/ai/jobs/{job_id}/run", {})
        self.assertEqual(status, 409)

    def test_static_traversal_blocked(self) -> None:
        status, _, _ = self._request("/static/../radar/db.py")
        self.assertEqual(status, 404)

    def test_healthz(self) -> None:
        status, payload = self._json("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
