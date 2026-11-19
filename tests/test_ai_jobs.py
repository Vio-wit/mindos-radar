"""AI 状态机（L5-01/03/04/08）测试：FakeProvider，不发任何网络请求。"""
from __future__ import annotations

import json
import unittest

from helpers import DbTestCase

from radar import config, registry, store
from radar.ai import jobs as ai_jobs
from radar.ai.provider import ProviderError, ProviderResult


def payload(summary: str = "这是离线测试摘要。") -> str:
    return json.dumps({
        "summary": summary,
        "key_points": ["要点一"],
        "tags": ["test"],
        "category": "test",
        "relevance": 3,
        "action": "无",
    }, ensure_ascii=False)


class ScriptedProvider:
    name = "fake"
    model = "fake-v1"

    def __init__(self, script=None) -> None:
        self.script = list(script or [])
        self.calls = 0

    def analyze(self, prompt: str, *, max_output_tokens: int = 800) -> ProviderResult:
        action = self.script[self.calls] if self.calls < len(self.script) else payload()
        self.calls += 1
        if isinstance(action, Exception):
            raise action
        text = action if isinstance(action, str) else payload()
        return ProviderResult(text=text, prompt_tokens=10, completion_tokens=5,
                              request_id=f"scripted-{self.calls}")


class AiJobTests(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        result = registry.load_registry()
        registry.sync_to_db(self.conn, result.sources, now=store.now_iso())
        self.settings = config.load_settings()
        self.settings["ai"].update({
            "enabled": True,
            "max_items_per_job": 20,
            "daily_budget_cny": "10.00",
            "daily_request_limit": 100,
            "max_input_chars": 2000,
        })
        self.ids = []
        for index in range(10):
            _, item_id = store.upsert_item(
                self.conn, source_id="pypi-requests",
                item={
                    "external_id": f"ai-{index}", "title": f"AI 测试条目 {index}",
                    "summary": f"摘要 {index}", "content_hash": f"ai-hash-{index}",
                },
                rights_state="metadata_only",
            )
            self.ids.append(int(item_id))

    def _job(self, count: int, *, include_cached: bool = False) -> int:
        return ai_jobs.create_job(
            self.conn, self.ids[:count], settings=self.settings, task_type="brief",
            provider="fake", model="fake-v1", include_cached=include_cached,
        )

    def test_ten_items_fourth_fails_and_partial(self) -> None:
        script = [payload()] * 3 + [ProviderError("http_429", "限流", retryable=True)] + [payload()] * 6
        provider = ScriptedProvider(script)
        job_id = self._job(10)
        result = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual((result["succeeded"], result["failed"]), (9, 1))
        self.assertEqual(provider.calls, 10)
        rows = {row["item_id"]: row["status"] for row in self.conn.execute(
            "select item_id, status from radar_ai_job_items where job_id=?", (job_id,)
        )}
        self.assertEqual(rows[self.ids[3]], "FAILED")
        self.assertEqual(sum(1 for status in rows.values() if status == "SUCCEEDED"), 9)

    def test_retry_failed_then_complete(self) -> None:
        job_id = self._job(3)
        provider = ScriptedProvider([payload(), ProviderError("http_403", "拒绝"), payload()])
        first = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(first["status"], "PARTIAL")
        self.assertEqual(ai_jobs.retry_failed(self.conn, job_id), 1)
        second = ai_jobs.run_job(self.conn, job_id, ScriptedProvider([payload()]), settings=self.settings)
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(second["succeeded"], 1)

    def test_unknown_outcome_is_not_retried_by_default(self) -> None:
        job_id = self._job(2)
        provider = ScriptedProvider([ProviderError("timeout", "超时", unknown_outcome=True), payload()])
        result = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(result["unknown"], 1)
        self.assertEqual(result["status"], "PARTIAL")
        statuses = {row["status"] for row in self.conn.execute(
            "select status from radar_ai_job_items where job_id=?", (job_id,)
        )}
        self.assertIn("UNKNOWN_OUTCOME", statuses)
        self.assertEqual(ai_jobs.retry_failed(self.conn, job_id), 0)
        self.assertEqual(ai_jobs.retry_failed(self.conn, job_id, include_unknown=True), 1)

    def test_budget_zero_pauses_before_any_request(self) -> None:
        self.settings["ai"]["daily_budget_cny"] = "0"
        provider = ScriptedProvider()
        job_id = self._job(2)
        result = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(result["status"], "PAUSED")
        self.assertEqual(provider.calls, 0)
        self.assertGreater(result["remaining"], 0)

    def test_cancel_before_run_sends_nothing(self) -> None:
        provider = ScriptedProvider()
        job_id = self._job(2)
        ai_jobs.cancel_job(self.conn, job_id)
        result = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(provider.calls, 0)

    def test_cache_hit_skips_second_job(self) -> None:
        job_one = self._job(1)
        first_provider = ScriptedProvider()
        ai_jobs.run_job(self.conn, job_one, first_provider, settings=self.settings)
        job_two = self._job(1, include_cached=True)
        second_provider = ScriptedProvider()
        result = ai_jobs.run_job(self.conn, job_two, second_provider, settings=self.settings)
        self.assertEqual(second_provider.calls, 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["status"], "COMPLETED")

    def test_invalid_json_fails_without_overwriting_original(self) -> None:
        job_id = self._job(1)
        provider = ScriptedProvider(["这不是 JSON"])
        result = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(result["failed"], 1)
        analyses = self.conn.execute(
            "select count(*) from radar_analyses where item_id=? and provider='fake'", (self.ids[0],)
        ).fetchone()[0]
        self.assertEqual(analyses, 0)
        item = store.get_item(self.conn, self.ids[0])
        self.assertEqual(item["title"], "AI 测试条目 0")

    def test_recover_stale_marks_unknown(self) -> None:
        job_id = self._job(1)
        ai_jobs.run_job(self.conn, job_id, ScriptedProvider([payload()]), settings=self.settings)
        with self.conn:
            self.conn.execute(
                "update radar_ai_job_items set status='RUNNING', lease_until='2000-01-01T00:00:00Z' where job_id=?",
                (job_id,),
            )
        self.assertEqual(ai_jobs.recover_stale(self.conn, job_id=job_id), 1)
        row = self.conn.execute(
            "select status, error_code from radar_ai_job_items where job_id=?", (job_id,)
        ).fetchone()
        self.assertEqual(row["status"], "UNKNOWN_OUTCOME")
        self.assertEqual(row["error_code"], "lease_expired")

    def test_schema_rejects_bad_output(self) -> None:
        job_id = self._job(1)
        provider = ScriptedProvider([json.dumps({"summary": "", "tags": "bad"})])
        result = ai_jobs.run_job(self.conn, job_id, provider, settings=self.settings)
        self.assertEqual(result["failed"], 1)
        row = self.conn.execute(
            "select error_code, safe_error from radar_ai_job_items where job_id=?", (job_id,)
        ).fetchone()
        self.assertEqual(row["error_code"], "invalid_output")
        self.assertIn("schema", row["safe_error"])


if __name__ == "__main__":
    unittest.main()
