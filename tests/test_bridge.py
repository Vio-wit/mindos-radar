"""旧通知库只读桥接（R1-07）测试：不建库、不可写、字段映射正确。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from helpers import DbTestCase  # noqa: F401

from radar import bridge

SCHEMA = """
create table schema_meta (key text primary key, value text not null);
create table notices (
    notice_key text primary key, external_id text, notice_type text, title text not null,
    department text, published_text text, published_at text, url text, content text,
    content_hash text, first_seen_at text, last_seen_at text, is_unread integer,
    updated_at text, is_favorite integer, note text
);
create table notice_briefs (notice_key text primary key, summary text, key_points text, deadlines text);
create table notice_analysis (notice_key text primary key, categories text, primary_category text,
    confidence real, keywords text, advice text, source text, updated_at text);
create table tracked_events (event_key text primary key, title text, national_signup_deadline text,
    school_signup_deadline text, work_submission_deadline text, competition_date text, status text,
    last_verified_at text);
create table attachments (attachment_id integer primary key);
create table attachment_extractions (attachment_id integer primary key);
create table runs (id integer primary key);
"""


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "notices.sqlite3"
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)
        conn.execute("insert into schema_meta values('schema_version','7')")
        conn.execute(
            "insert into notices(notice_key, title, department, published_at, url, content, is_unread, is_favorite) "
            "values('n-1','测试通知','教务处','2026-11-10T00:00:00Z','https://example.edu/n1','正文内容',1,0)"
        )
        conn.execute("insert into notice_briefs values('n-1','既有摘要','[]','[]')")
        conn.execute(
            "insert into notice_analysis values('n-1','[\"教务\"]','教务',0.9,'[]','建议','rule','2026-11-10T00:00:00Z')"
        )
        conn.execute(
            "insert into tracked_events values('e-1','赛事','2026-12-01','2026-11-20','2026-12-10','2026-12-20','open','2026-11-10T00:00:00Z')"
        )
        conn.commit()
        conn.close()
        self.conn = bridge.connect(self.path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_connect_existing_and_stats(self) -> None:
        stats = bridge.stats(self.conn)
        self.assertEqual(stats["notices"], 1)
        self.assertEqual(stats["schema_version"], "7")

    def test_list_and_detail(self) -> None:
        items = bridge.list_notices(self.conn, query="测试")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["brief_summary"], "既有摘要")
        detail = bridge.get_notice(self.conn, "n-1")
        self.assertEqual(detail["content"], "正文内容")
        self.assertEqual(detail["primary_category"], "教务")

    def test_events_are_mapped(self) -> None:
        events = bridge.list_events(self.conn)
        self.assertEqual(events[0]["title"], "赛事")

    def test_writes_are_denied(self) -> None:
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("update notices set title='x' where notice_key='n-1'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("insert into notices(notice_key,title) values('n-2','x')")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("delete from notices")

    def test_missing_db_does_not_create_file(self) -> None:
        missing = Path(self._tmp.name) / "absent.sqlite3"
        with self.assertRaises(bridge.BridgeUnavailable):
            bridge.connect(missing)
        self.assertFalse(missing.exists())

    def test_unconfigured_db_is_reported(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(bridge.BridgeUnavailable, "RADAR_NOTICE_DB"):
                bridge.connect()


if __name__ == "__main__":
    unittest.main()
