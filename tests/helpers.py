"""测试公共工具：临时数据库、假 HTTP 客户端、样例 feed。"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from radar import db  # noqa: E402
from radar.httpclient import FetchError, HttpResponse  # noqa: E402


def make_response(text: str | bytes, *, url: str = "https://example.com/feed", status: int = 200,
                  content_type: str = "application/json", headers: dict[str, str] | None = None) -> HttpResponse:
    content = text.encode("utf-8") if isinstance(text, str) else text
    return HttpResponse(
        url=url, status=status, headers={k.lower(): v for k, v in (headers or {}).items()},
        content=content, content_type=content_type, elapsed_ms=1,
        sha256=hashlib.sha256(content).hexdigest(),
    )


class FakeClient:
    """按 URL 精确或前缀匹配返回预置响应；未命中抛 404。"""

    def __init__(self, routes: dict[str, HttpResponse | str | bytes]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        self.calls.append(url)
        if url in self.routes:
            value = self.routes[url]
        else:
            match = next((key for key in self.routes if url.startswith(key)), None)
            if match is None:
                raise FetchError("http_404", "HTTP 404")
            value = self.routes[match]
        if isinstance(value, str):
            return make_response(value, url=url)
        if isinstance(value, bytes):
            return make_response(value, url=url)
        return value


class DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = db.connect(self.tmp / "test.sqlite3")

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()
