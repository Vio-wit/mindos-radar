"""可选调度（C2-15）：单一入口、默认关闭、只处理到期订阅。

* 未显式开启时不会发起任何定时请求；
* 每轮独立连接数据库，避免长事务与线程共享连接；
* 单源失败不影响其它源（engine 已保证）；
* 提供 stop 事件，进程退出时干净结束。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import config, db, engine, store


class Scheduler:
    def __init__(
        self,
        conn_factory: Callable[[], Any] | None = None,
        *,
        interval_seconds: int = 300,
        settings: dict[str, Any] | None = None,
        limit: int = 5,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.conn_factory = conn_factory or (lambda: db.connect())
        self.interval_seconds = max(30, int(interval_seconds))
        self.settings = settings or config.load_settings()
        self.limit = max(1, min(int(limit), 20))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.logger = logger or (lambda message: None)

    def run_once(self) -> list[dict[str, Any]]:
        conn = self.conn_factory()
        try:
            store.recover_interrupted_runs(conn)
            return engine.collect_due(conn, limit=self.limit, settings=self.settings)
        finally:
            conn.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                results = self.run_once()
                for result in results:
                    self.logger(f"collect {result.get('source_id')}: {result.get('status')}")
            except Exception as exc:  # noqa: BLE001 调度器不能因单轮异常退出
                self.logger(f"scheduler error: {type(exc).__name__}")
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="radar-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def schedule_enabled(settings: dict[str, Any] | None = None) -> bool:
    cfg = (settings or config.load_settings()).get("schedule") or {}
    return bool(cfg.get("enabled", False))


def emergency_stop_file() -> Path:
    return config.data_dir() / "scheduler.stop"


def allowed(settings: dict[str, Any] | None = None) -> bool:
    """调度开关 + 人工急停文件双重控制。"""
    return schedule_enabled(settings) and not emergency_stop_file().exists()


def sleep_with_stop(seconds: float, stop: threading.Event) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not stop.is_set() and time.monotonic() < deadline:
        stop.wait(min(0.5, max(0.05, deadline - time.monotonic())))
