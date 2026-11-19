"""R1：数据库 schema、条目去重与状态、运行记录、主题、搜索。"""
from __future__ import annotations

import unittest

from helpers import DbTestCase
from radar import db, store


class DatabaseTests(DbTestCase):
    def test_schema_tables_and_version(self):
        tables = set(db.table_names(self.conn))
        for required in (
            "radar_sources", "radar_subscriptions", "radar_items", "radar_item_state",
            "radar_runs", "radar_topics", "radar_topic_matches", "radar_observations",
            "radar_ai_jobs", "radar_ai_job_items", "radar_analyses", "radar_ai_usage",
        ):
            self.assertIn(required, tables)
        row = self.conn.execute("select value from schema_meta where key='schema_version'").fetchone()
        self.assertEqual(row["value"], "1")

    def test_migrate_is_idempotent(self):
        self.assertEqual(db.migrate(self.conn), 1)
        self.assertEqual(db.migrate(self.conn), 1)

    def test_foreign_keys_enabled(self):
        self.assertEqual(self.conn.execute("pragma foreign_keys").fetchone()[0], 1)


class StoreTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with self.conn:
            self.conn.execute(
                "insert into radar_sources(id,name,category,config_json,verification_status,cost_tier,origin,updated_at) "
                "values('demo','Demo','technology','{}','integrated','F0','curated',?)",
                (store.now_iso(),),
            )

    def test_subscription_unique_per_parameters(self):
        first = store.create_subscription(self.conn, "demo", parameters={"a": 1})
        second = store.create_subscription(self.conn, "demo", parameters={"a": 1})
        third = store.create_subscription(self.conn, "demo", parameters={"a": 2})
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(len(store.list_subscriptions(self.conn)), 2)

    def test_subscription_requires_known_source(self):
        with self.assertRaises(ValueError):
            store.create_subscription(self.conn, "missing")

    def test_item_dedupe_and_update_marks_analysis_stale(self):
        sub = store.create_subscription(self.conn, "demo", enabled=True)
        outcome, item_id = store.upsert_item(
            self.conn, source_id="demo", subscription_id=sub,
            item={"external_id": "1", "title": "Hello", "summary": "world", "published_at": "2026-01-01T00:00:00Z"},
        )
        self.assertEqual(outcome, "inserted")
        again, same_id = store.upsert_item(
            self.conn, source_id="demo", subscription_id=sub,
            item={"external_id": "1", "title": "Hello", "summary": "world"},
        )
        self.assertEqual(again, "unchanged")
        self.assertEqual(same_id, item_id)

        with self.conn:
            self.conn.execute(
                "insert into radar_analyses(item_id,input_hash,task_type,provider,model,prompt_version,output_json,generated_at) "
                "values(?,?,?,?,?,?,?,?)",
                (item_id, "h1", "brief", "fake", "fake", "v1", "{}", store.now_iso()),
            )
        changed, _ = store.upsert_item(
            self.conn, source_id="demo", subscription_id=sub,
            item={"external_id": "1", "title": "Hello", "summary": "changed"},
        )
        self.assertEqual(changed, "updated")
        stale = self.conn.execute("select stale from radar_analyses where item_id=?", (item_id,)).fetchone()["stale"]
        self.assertEqual(stale, 1)

    def test_state_read_favorite_note(self):
        _, item_id = store.upsert_item(self.conn, source_id="demo", item={"external_id": "2", "title": "T"})
        store.set_state(self.conn, item_id, read=True, favorite=True, note="记得看")
        item = store.get_item(self.conn, item_id)
        self.assertIsNotNone(item["read_at"])
        self.assertEqual(item["favorite"], 1)
        self.assertEqual(item["note"], "记得看")
        self.assertEqual(len(store.list_items(self.conn, favorites=True)), 1)
        self.assertEqual(len(store.list_items(self.conn, unread=True)), 0)
        store.set_state(self.conn, item_id, read=False)
        self.assertEqual(len(store.list_items(self.conn, unread=True)), 1)

    def test_run_lifecycle_and_recovery(self):
        run_id = store.start_run(self.conn, subscription_id=None, source_id="demo", cursor_before=None)
        store.finish_run(self.conn, run_id, status="SUCCEEDED", counts={"inserted": 3})
        run = store.list_runs(self.conn)[0]
        self.assertEqual(run["status"], "SUCCEEDED")
        self.assertEqual(run["counts"]["inserted"], 3)

        leftover = store.start_run(self.conn, subscription_id=None, source_id="demo", cursor_before=None)
        self.assertEqual(store.recover_interrupted_runs(self.conn), 1)
        rows = {row["id"]: row["status"] for row in store.list_runs(self.conn)}
        self.assertEqual(rows[leftover], "UNKNOWN")

    def test_search_fts_or_fallback(self):
        store.upsert_item(self.conn, source_id="demo", item={"external_id": "s1", "title": "NVIDIA GPU 发布", "summary": "算力"})
        store.upsert_item(self.conn, source_id="demo", item={"external_id": "s2", "title": "无关内容", "summary": "天气"})
        results = store.list_items(self.conn, query="NVIDIA")
        self.assertEqual(len(results), 1)
        self.assertIn("NVIDIA", results[0]["title"])
        self.assertEqual(len(store.list_items(self.conn, query="天气")), 1)

    def test_topic_matching_with_exclusion(self):
        topic = store.create_topic(self.conn, name="AI", include_terms=["AI", "模型"], exclude_terms=["广告"])
        store.upsert_item(self.conn, source_id="demo", item={"external_id": "t1", "title": "AI 模型更新"})
        store.upsert_item(self.conn, source_id="demo", item={"external_id": "t2", "title": "AI 广告"})
        store.upsert_item(self.conn, source_id="demo", item={"external_id": "t3", "title": "体育新闻"})
        self.assertEqual(store.evaluate_topic(self.conn, topic), 1)
        self.assertEqual(len(store.list_items(self.conn, topic_id=topic)), 1)

    def test_observations_upsert(self):
        rows = [{"series_key": "1,2", "observed_at": "2026-01-01T00:00:00Z", "metric": "temperature_2m", "value": 5.0}]
        self.assertEqual(store.save_observations(self.conn, "demo", rows), {"inserted": 1, "updated": 0})
        rows[0]["value"] = 6.0
        self.assertEqual(store.save_observations(self.conn, "demo", rows), {"inserted": 0, "updated": 1})


if __name__ == "__main__":
    unittest.main()
