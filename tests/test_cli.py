"""CLI（R1-01/U3-07）与调度（C2-15）测试：不联网、可重复执行。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import make_response  # noqa: F401

from radar import config, db, registry, scheduler, store
from radar.cli import main


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._backup = os.environ.get("RADAR_DATA_DIR")
        os.environ["RADAR_DATA_DIR"] = str(self.dir)
        self.csv = self.dir / "items.csv"
        self.csv.write_text(
            "id,title,summary,link,date\ncli-1,CLI 条目,摘要,https://example.com/cli1,2026-11-16T00:00:00Z\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        if self._backup is None:
            os.environ.pop("RADAR_DATA_DIR", None)
        else:
            os.environ["RADAR_DATA_DIR"] = self._backup
        self._tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, buffer.getvalue()

    def test_init_registry_and_stats(self) -> None:
        code, out = self._run("init")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        code, out = self._run("registry", "validate")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 108)
        code, out = self._run("stats")
        self.assertEqual(code, 0)
        self.assertIn("overview", json.loads(out))

    def test_subscription_and_import_flow(self) -> None:
        self._run("init")
        code, out = self._run("sub", "add", "pypi-requests", "--interval", "120", "--enable")
        self.assertEqual(code, 0)
        code, out = self._run("sub", "list")
        subs = json.loads(out)["items"]
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0]["enabled"])

        code, out = self._run("import", "preview", "--file", str(self.csv))
        self.assertEqual(code, 0)
        preview = json.loads(out)
        self.assertEqual(preview["mapped_rows"], 1)
        code, out = self._run(
            "import", "commit", "--file", str(self.csv), "--source", "pypi-requests",
            "--snapshot", preview["snapshot"],
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["inserted"], 1)
        code, out = self._run("stats")
        self.assertEqual(json.loads(out)["overview"]["totals"]["items"], 1)

    def test_topics_and_analyze(self) -> None:
        self._run("init")
        code, out = self._run("topics", "add", "--name", "AI", "--include", "AI,模型")
        self.assertEqual(code, 0)
        topic_id = json.loads(out)["id"]
        self.assertEqual(topic_id, 1)
        code, out = self._run("analyze")
        self.assertEqual(code, 0)

    def test_export_to_file(self) -> None:
        self._run("init")
        target = self.dir / "out.json"
        code, out = self._run("export", "--format", "json", "--out", str(target))
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("items", payload)

    def test_serve_refuses_schedule_when_disabled(self) -> None:
        self._run("init")
        code, _ = self._run("serve", "--schedule")
        self.assertEqual(code, 1)


class SchedulerTests(unittest.TestCase):
    def test_allowed_requires_setting_and_no_stop_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backup = os.environ.get("RADAR_DATA_DIR")
            os.environ["RADAR_DATA_DIR"] = tmp
            try:
                self.assertFalse(scheduler.allowed({"schedule": {"enabled": False}}))
                self.assertTrue(scheduler.allowed({"schedule": {"enabled": True}}))
                stop = scheduler.emergency_stop_file()
                stop.write_text("stop", encoding="utf-8")
                self.assertFalse(scheduler.allowed({"schedule": {"enabled": True}}))
                stop.unlink()
            finally:
                if backup is None:
                    os.environ.pop("RADAR_DATA_DIR", None)
                else:
                    os.environ["RADAR_DATA_DIR"] = backup

    def test_run_once_without_due_subscriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "radar.sqlite3"

            def factory():
                return db.connect(path)

            conn = db.connect(path)
            conn.close()
            runner = scheduler.Scheduler(factory, interval_seconds=30, settings={"schedule": {"enabled": True}})
            self.assertEqual(runner.run_once(), [])
            self.assertFalse(runner.running)


if __name__ == "__main__":
    unittest.main()
