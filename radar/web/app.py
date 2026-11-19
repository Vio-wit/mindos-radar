"""本地 Web 服务（U3）：标准库 ThreadingHTTPServer，仅绑定 127.0.0.1。

安全基线：
* Host 头白名单（localhost / 127.0.0.1 / [::1]），拒绝 DNS rebinding 型请求；
* 所有写操作需要 CSRF 令牌（表单字段 ``_csrf`` 或请求头 ``X-CSRF-Token``）；
* 有 Origin/Referer 时必须与 Host 同源；
* 严格 CSP、nosniff、no-referrer；所有输出转义；
* 数据库连接每请求新建，线程安全；旧通知库只读桥接。
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import analysis, bridge, config, db, engine, export, importer, stats, store
from .. import registry as registry_mod
from ..ai import jobs as ai_jobs
from ..ai import provider as ai_provider
from . import render

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 1 * 1024 * 1024
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "Cache-Control": "no-store",
}


class App:
    def __init__(self, *, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or config.load_settings()
        self.csrf = secrets.token_urlsafe(32)
        self.scheduler = None
        self._lock = threading.Lock()

    def reload_settings(self) -> dict[str, Any]:
        self.settings = config.load_settings()
        return self.settings

    def start_scheduler(self) -> None:
        from ..scheduler import Scheduler

        self.scheduler = Scheduler(interval_seconds=int((self.settings.get("schedule") or {}).get("interval_minutes", 360)) * 60)
        self.scheduler.start()

    def stop_scheduler(self) -> None:
        if self.scheduler:
            self.scheduler.stop()
            self.scheduler = None


def _q(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key) or []
    return (values[0] or "").strip() if values else default


def _qint(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(_q(query, key) or default)
    except ValueError:
        return default


def _qbool(query: dict[str, list[str]], key: str) -> bool:
    return _q(query, key) in {"1", "true", "yes", "on"}


def _form_int(data: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(data.get(key, default)).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _source_view(row: dict[str, Any]) -> dict[str, Any]:
    """把数据库行还原成注册表文档结构，保证页面/API 字段一致。"""
    data = dict(row.get("config") or {})
    for key in ("id", "name", "category", "updated_at"):
        if row.get(key) is not None:
            data[key] = row[key]
    data.setdefault("verification", {})["status"] = row.get("verification_status")
    data.setdefault("access", {})["cost_tier"] = row.get("cost_tier")
    if row.get("origin"):
        data.setdefault("origin", {})["type"] = row["origin"]
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "MindOSRadar/0.1"
    protocol_version = "HTTP/1.1"
    app: App

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Set-Cookie", f"radar_csrf={self.app.csrf}; Path=/; SameSite=Strict; HttpOnly")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def _html(self, body: str, status: int = 200) -> None:
        self._send(status, body.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self._send(303, b"", "text/plain; charset=utf-8", {"Location": location})

    def _error_page(self, status: int, message: str) -> None:
        self._html(render.error_page(status, message, csrf=self.app.csrf), status=status)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        if host.startswith("["):
            return host.startswith("[::1]")
        name = host.split(":", 1)[0]
        return name in ALLOWED_HOSTS

    def _origin_ok(self) -> bool:
        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if not value:
                continue
            parsed = urllib.parse.urlsplit(value)
            if not parsed.hostname or parsed.hostname.lower() not in ALLOWED_HOSTS:
                return False
        return True

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type == "application/json":
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON 必须是对象")
            return payload
        parsed = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def _csrf_ok(self, data: dict[str, Any]) -> bool:
        token = str(data.get("_csrf") or self.headers.get("X-CSRF-Token") or "")
        return bool(token) and secrets.compare_digest(token, self.app.csrf)

    @contextmanager
    def _conn(self):
        conn = db.connect()
        try:
            yield conn
        finally:
            conn.close()

    # -- routing ----------------------------------------------------------
    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._error_page(400, "Host 头不被允许（仅限本机访问）")
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        try:
            if path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path == "/healthz":
                self._json({"status": "ok", "service": "mindos-radar", "bind": "127.0.0.1"})
            elif path.startswith("/api/radar/"):
                self._api_get(path[len("/api/radar"):], query)
            else:
                self._page_get(path, query)
        except bridge.BridgeUnavailable:
            self._error_page(503, "旧通知库不可用（只读桥接失败）")
        except ValueError as exc:
            self._error_page(400, str(exc))
        except Exception as exc:  # noqa: BLE001 兜底：不把异常细节暴露成 500 栈
            self._error_page(500, f"内部错误：{type(exc).__name__}")

    def do_POST(self) -> None:  # noqa: N802
        self._handle_write("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle_write("PATCH")

    def _handle_write(self, method: str) -> None:
        if not self._host_ok() or not self._origin_ok():
            self._json({"error": "forbidden", "message": "Host/Origin 校验未通过"}, 403)
            return
        try:
            data = self._read_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": "bad_request", "message": str(exc)}, 400)
            return
        if not self._csrf_ok(data):
            self._json({"error": "csrf", "message": "CSRF 令牌缺失或不匹配"}, 403)
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            self._route_write(method, path, data)
        except ValueError as exc:
            if path.startswith("/api/"):
                self._json({"error": "invalid", "message": str(exc)}, 400)
            else:
                self._redirect(f"/?msg={urllib.parse.quote(str(exc)[:180])}")
        except bridge.BridgeUnavailable as exc:
            self._json({"error": "bridge_unavailable", "message": str(exc)}, 503)
        except Exception as exc:  # noqa: BLE001
            if path.startswith("/api/"):
                self._json({"error": "internal", "message": type(exc).__name__}, 500)
            else:
                self._redirect(f"/?msg={urllib.parse.quote('操作失败：' + type(exc).__name__)}")

    def _static(self, name: str) -> None:
        if "/" in name or "\\" in name or ".." in name:
            self._error_page(404, "静态资源不存在")
            return
        target = STATIC_DIR / name
        if not target.is_file():
            self._error_page(404, "静态资源不存在")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), content_type)

    # -- pages ------------------------------------------------------------
    def _page_get(self, path: str, query: dict[str, list[str]]) -> None:
        app = self.app
        notice = _q(query, "msg")
        if path == "/":
            with self._conn() as conn:
                overview = stats.overview(conn)
                today = store.list_items(conn, limit=10, order="published")
                health = stats.source_health(conn)
            events: list[dict[str, Any]] = []
            try:
                with bridge.connect() as old:
                    events = bridge.list_events(old)
            except bridge.BridgeUnavailable:
                events = []
            self._html(render.dashboard(
                {"overview": overview, "today_items": today, "health": health, "events": events},
                csrf=app.csrf, notice=notice,
            ))
            return
        if path == "/feed":
            filters = {
                "q": _q(query, "q"), "source": _q(query, "source"), "unread": _qbool(query, "unread"),
                "favorites": _qbool(query, "favorites"), "order": _q(query, "order") or "published",
            }
            topic_id = _qint(query, "topic", 0)
            page = max(1, _qint(query, "page", 1))
            limit = 30
            with self._conn() as conn:
                items = store.list_items(
                    conn,
                    query=filters["q"] or None,
                    source_id=filters["source"] or None,
                    unread=filters["unread"] or None,
                    favorites=filters["favorites"] or None,
                    topic_id=topic_id or None,
                    order=filters["order"],
                    limit=limit + 1,
                    offset=(page - 1) * limit,
                )
                sources = store.list_sources(conn)
            has_next = len(items) > limit
            self._html(render.feed_page(
                items[:limit], filters={**filters, "topic": topic_id or ""},
                page=page, has_next=has_next, sources=sources, csrf=app.csrf, notice=notice,
            ))
            return
        if path.startswith("/feed/"):
            item_id = int(path.rsplit("/", 1)[1])
            with self._conn() as conn:
                item = store.get_item(conn, item_id)
            if not item:
                self._error_page(404, "条目不存在")
                return
            self._html(render.item_page(item, csrf=app.csrf, notice=notice))
            return
        if path == "/sources":
            filters = {
                "q": _q(query, "q"), "category": _q(query, "category"),
                "status": _q(query, "status"), "cost": _q(query, "cost"),
            }
            with self._conn() as conn:
                sources = [_source_view(row) for row in store.list_sources(conn)]
            result = registry_mod.filter_sources(
                sources, category=filters["category"] or None, status=filters["status"] or None,
                cost_tier=filters["cost"] or None, query=filters["q"] or None,
            )
            categories = sorted({source.get("category") or "" for source in sources if source.get("category")})
            self._html(render.sources_page(result, filters, categories, csrf=app.csrf, notice=notice))
            return
        if path == "/subscriptions":
            with self._conn() as conn:
                subs = store.list_subscriptions(conn)
                sources = [_source_view(row) for row in store.list_sources(conn)]
            self._html(render.subscriptions_page(subs, sources, csrf=app.csrf, notice=notice))
            return
        if path == "/runs":
            with self._conn() as conn:
                runs = store.list_runs(conn, limit=50)
            self._html(render.runs_page(runs, csrf=app.csrf, notice=notice))
            return
        if path == "/topics":
            with self._conn() as conn:
                topics = store.list_topics(conn)
                sources = store.list_sources(conn)
            self._html(render.topics_page(topics, sources, csrf=app.csrf, notice=notice))
            return
        if path == "/stats":
            with self._conn() as conn:
                overview = stats.overview(conn)
                keywords = stats.keyword_frequency(conn, limit=24)
                quality = analysis.quality_metrics(conn)
            overview["quality"].update({key: quality[key] for key in ("run_failure_pct", "runs_failed", "runs")})
            self._html(render.stats_page(
                {"overview": overview, "keywords": keywords, "quality": quality},
                csrf=app.csrf, notice=notice,
            ))
            return
        if path == "/ai":
            preview = None
            if _q(query, "preview_items"):
                ids = [int(part) for part in _q(query, "preview_items").split(",") if part.strip().isdigit()]
                with self._conn() as conn:
                    preview = ai_jobs.preview(conn, ids, app.settings, task_type=_q(query, "preview_task") or "brief")
            with self._conn() as conn:
                job_rows = ai_jobs.list_jobs(conn, limit=30)
            self._html(render.ai_page(
                job_rows, preview, ai_enabled=bool((app.settings.get("ai") or {}).get("enabled")),
                key_configured=bool(config.secret_status().get("deepseek")), csrf=app.csrf, notice=notice,
            ))
            return
        if path == "/import":
            with self._conn() as conn:
                sources = store.list_sources(conn)
            self._html(render.import_page(None, sources=sources, csrf=app.csrf, notice=notice))
            return
        if path == "/settings":
            secret_status = config.secret_status()
            paths = {
                "配置目录": str(config.data_dir()),
                "数据库": str(config.db_path()),
                "设置文件": str(config.settings_path()),
                "秘密文件": str(config.secrets_path()),
                "旧通知库（只读）": str(bridge.resolve_path()) if bridge.resolve_path() else "未配置",
            }
            bridge_stats = None
            try:
                with bridge.connect() as old:
                    bridge_stats = bridge.stats(old)
            except bridge.BridgeUnavailable:
                bridge_stats = None
            self._html(render.settings_page(
                app.settings, secret_status=secret_status, paths=paths,
                bridge_stats=bridge_stats, csrf=app.csrf, notice=notice,
            ))
            return
        if path == "/notices":
            try:
                with bridge.connect() as old:
                    stats_data = bridge.stats(old)
                    items = bridge.list_notices(old, limit=50)
            except bridge.BridgeUnavailable:
                stats_data, items = None, []
            self._html(render.notices_page(items, stats_data, csrf=app.csrf, notice=notice))
            return
        if path.startswith("/notices/"):
            key = urllib.parse.unquote(path[len("/notices/"):])
            with bridge.connect() as old:
                notice_item = bridge.get_notice(old, key)
            if not notice_item:
                self._error_page(404, "通知不存在")
                return
            self._html(render.notice_page(notice_item, csrf=app.csrf, notice=notice))
            return
        if path == "/export":
            fmt = _q(query, "format") or "json"
            with self._conn() as conn:
                content = export.EXPORTERS.get(fmt, export.export_json)(
                    conn,
                    query=_q(query, "q") or None,
                    source_id=_q(query, "source") or None,
                    unread=_qbool(query, "unread") or None,
                    favorites=_qbool(query, "favorites") or None,
                    limit=2000,
                )
            content_type = {"csv": "text/csv; charset=utf-8", "md": "text/markdown; charset=utf-8"}.get(
                "csv" if fmt == "csv" else "md" if fmt in {"md", "markdown"} else "json",
                "application/json; charset=utf-8",
            )
            self._send(200, content.encode("utf-8"), content_type, {"Content-Disposition": f'attachment; filename="radar-export.{fmt}"'})
            return
        self._error_page(404, "页面不存在")

    # -- write routes -----------------------------------------------------
    def _route_write(self, method: str, path: str, data: dict[str, Any]) -> None:
        app = self.app
        is_api = path.startswith("/api/radar/")
        if path == "/init":
            with self._conn() as conn:
                result = registry_mod.load_registry()
                if result.errors:
                    raise ValueError("注册表校验失败：" + result.errors[0])
                counts = registry_mod.sync_to_db(conn, result.sources, now=store.now_iso())
                store.recover_interrupted_runs(conn)
            self._redirect(f"/?msg={urllib.parse.quote(f'注册表已同步：新增 {counts['inserted']}，更新 {counts['updated']}')}")
            return
        if path in {"/collect/due", "/collect/all"}:
            with self._conn() as conn:
                if path == "/collect/due":
                    result = engine.collect_due(conn, limit=10, settings=app.settings)
                else:
                    result = [
                        engine.collect_subscription(conn, int(sub["id"]), settings=app.settings, force=True)
                        for sub in store.list_subscriptions(conn, enabled_only=True)
                    ]
            ok = sum(1 for row in result if row.get("status") == "SUCCEEDED")
            self._redirect(f"/runs?msg={urllib.parse.quote(f'采集完成：{ok}/{len(result)} 成功')}")
            return
        if path == "/subscriptions":
            with self._conn() as conn:
                sub_id = store.create_subscription(
                    conn, str(data.get("source_id") or ""),
                    enabled=str(data.get("enabled") or "") in {"1", "true", "on"},
                    interval_minutes=_form_int(data, "interval", 360, 15, 10080),
                )
            if is_api:
                self._json({"id": sub_id}, 201)
            else:
                self._redirect(f"/subscriptions?msg={urllib.parse.quote('订阅已就绪')}")
            return
        if path.startswith("/subscriptions/"):
            parts = path.split("/")
            sub_id = int(parts[2])
            action = parts[3] if len(parts) > 3 else ""
            with self._conn() as conn:
                if action == "toggle":
                    sub = store.get_subscription(conn, sub_id)
                    if not sub:
                        raise ValueError("订阅不存在")
                    store.set_subscription(conn, sub_id, enabled=not bool(sub["enabled"]))
                    self._redirect(f"/subscriptions?msg={urllib.parse.quote('状态已切换')}")
                    return
                if action == "delete":
                    store.delete_subscription(conn, sub_id)
                    self._redirect(f"/subscriptions?msg={urllib.parse.quote('订阅已删除')}")
                    return
                if action == "collect":
                    result = engine.collect_subscription(conn, sub_id, settings=app.settings, force=True)
                    if is_api:
                        self._json(result)
                    else:
                        self._redirect(f"/runs?msg={urllib.parse.quote('采集：' + str(result.get('status')))}")
                    return
        if path.startswith("/sources/"):
            parts = path.split("/")
            source_id = parts[2]
            action = parts[3] if len(parts) > 3 else ""
            if action == "probe":
                with self._conn() as conn:
                    source = store.get_source(conn, source_id)
                    if not source:
                        raise ValueError("来源不存在")
                    status = (source.get("config") or {}).get("verification", {}).get("status")
                    if status not in {"integrated", "live_test_pass"}:
                        raise ValueError("该来源尚未通过审核，禁止发起探测请求")
                    result = engine.probe_source(conn, source_id, settings=app.settings)
                if is_api:
                    self._json(result)
                else:
                    self._redirect(f"/sources?msg={urllib.parse.quote(f'探测 {source_id}: {result.get('status')}')}")
                return
        if path.startswith("/feed/"):
            parts = path.split("/")
            item_id = int(parts[2])
            action = parts[3] if len(parts) > 3 else ""
            with self._conn() as conn:
                if action == "state":
                    store.set_state(
                        conn, item_id,
                        read=str(data.get("read") or "") in {"1", "true", "on"},
                        favorite=str(data.get("favorite") or "") in {"1", "true", "on"},
                        note=str(data.get("note") or ""),
                    )
                    self._redirect(f"/feed/{item_id}?msg={urllib.parse.quote('状态已保存')}")
                    return
                if action == "analyze":
                    payload = analysis.store_rule_analysis(conn, item_id)
                    if payload is None:
                        raise ValueError("条目不存在")
                    self._redirect(f"/feed/{item_id}?msg={urllib.parse.quote('规则分析完成')}")
                    return
        if path == "/topics":
            with self._conn() as conn:
                topic_id = store.create_topic(
                    conn,
                    name=str(data.get("name") or ""),
                    include_terms=[part.strip() for part in str(data.get("include") or "").split(",") if part.strip()],
                    exclude_terms=[part.strip() for part in str(data.get("exclude") or "").split(",") if part.strip()],
                    source_filter=[part.strip() for part in str(data.get("source_filter") or "").split(",") if part.strip()],
                )
                store.evaluate_topic(conn, topic_id)
            self._redirect(f"/topics?msg={urllib.parse.quote('主题已创建并完成首次匹配')}")
            return
        if path.startswith("/topics/"):
            parts = path.split("/")
            topic_id = int(parts[2])
            action = parts[3] if len(parts) > 3 else ""
            with self._conn() as conn:
                if action == "eval":
                    matched = store.evaluate_topic(conn, topic_id)
                    self._redirect(f"/topics?msg={urllib.parse.quote(f'重新匹配：{matched} 条')}")
                    return
                if action == "delete":
                    store.delete_topic(conn, topic_id)
                    self._redirect(f"/topics?msg={urllib.parse.quote('主题已删除')}")
                    return
        if path.startswith("/runs/") and path.endswith("/cancel"):
            run_id = int(path.split("/")[2])
            with self._conn() as conn:
                changed = store.cancel_run(conn, run_id)
            if is_api:
                self._json({"ok": changed, "id": run_id})
            else:
                self._redirect(f"/runs?msg={urllib.parse.quote('已取消' if changed else '该运行已结束，未改动')}")
            return
        if path == "/settings":
            patch = {
                "site_title": str(data.get("site_title") or "MindOS Radar")[:80],
                "collector": {
                    "timeout_seconds": _form_int(data, "timeout_seconds", 20, 3, 120),
                    "max_bytes": _form_int(data, "max_bytes", 4 * 1024 * 1024, 65536, 64 * 1024 * 1024),
                    "max_items_per_run": _form_int(data, "max_items_per_run", 50, 1, 200),
                    "host_min_interval_seconds": max(0.0, min(60.0, float(data.get("host_min_interval_seconds") or 1.0))),
                    "retry_count": _form_int(data, "retry_count", 2, 0, 5),
                },
                "schedule": {
                    "enabled": str(data.get("schedule_enabled") or "") in {"1", "true", "on"},
                    "interval_minutes": _form_int(data, "schedule_interval", 360, 30, 10080),
                },
            }
            config.save_settings(patch)
            app.reload_settings()
            self._redirect(f"/settings?msg={urllib.parse.quote('设置已保存')}")
            return
        if path == "/settings/ai":
            patch = {
                "ai": {
                    "enabled": str(data.get("ai_enabled") or "") in {"1", "true", "on"},
                    "model": str(data.get("model") or "deepseek-chat")[:80],
                    "base_url": str(data.get("base_url") or "https://api.deepseek.com")[:200],
                    "max_items_per_job": _form_int(data, "max_items_per_job", 10, 1, 50),
                    "daily_request_limit": _form_int(data, "daily_request_limit", 50, 0, 10000),
                    "daily_budget_cny": str(data.get("daily_budget_cny") or "1.00")[:20],
                }
            }
            config.save_settings(patch)
            app.reload_settings()
            self._redirect(f"/settings?msg={urllib.parse.quote('AI 配置已保存（Key 不在页面回显）')}")
            return
        if path == "/settings/secret":
            name = str(data.get("name") or "")
            if name not in {"deepseek_api_key", "github_token"}:
                raise ValueError("未知密钥名")
            if str(data.get("action") or "") == "clear":
                config.set_secret(name, None)
            else:
                value = str(data.get("value") or "").strip()
                if len(value) < 8:
                    raise ValueError("密钥过短，未保存")
                config.set_secret(name, value)
            self._redirect(f"/settings?msg={urllib.parse.quote('密钥状态已更新')}")
            return
        if path == "/ai/preview":
            ids = [int(part) for part in str(data.get("item_ids") or "").replace(" ", ",").split(",") if part.strip().isdigit()]
            if not ids:
                raise ValueError("请填写至少一个条目 ID")
            task = str(data.get("task_type") or "brief")
            self._redirect(f"/ai?preview_items={','.join(str(i) for i in ids)}&preview_task={urllib.parse.quote(task)}")
            return
        if path == "/ai/jobs":
            ids = [int(part) for part in str(data.get("item_ids") or "").split(",") if part.strip().isdigit()]
            if not ids:
                raise ValueError("请先预览并选择条目")
            with self._conn() as conn:
                job_id = ai_jobs.create_job(
                    conn, ids, settings=app.settings, task_type=str(data.get("task_type") or "brief"),
                    note="web", max_items=(app.settings.get("ai") or {}).get("max_items_per_job"),
                )
            if is_api:
                self._json({"job_id": job_id}, 201)
            else:
                self._redirect(f"/ai?msg={urllib.parse.quote(f'任务 {job_id} 已创建，确认后可运行')}")
            return
        if path.startswith("/ai/jobs/"):
            parts = path.split("/")
            job_id = int(parts[3])
            action = parts[4] if len(parts) > 4 else ""
            payload: dict[str, Any] | None = None
            with self._conn() as conn:
                if action == "pause":
                    ai_jobs.pause_job(conn, job_id)
                elif action == "resume":
                    ai_jobs.resume_job(conn, job_id)
                elif action == "cancel":
                    ai_jobs.cancel_job(conn, job_id)
                elif action == "retry":
                    ai_jobs.retry_failed(conn, job_id, include_unknown=str(data.get("include_unknown") or "") in {"1", "true"})
                elif action == "run":
                    fake = str(data.get("fake") or "") in {"1", "true", "on"}
                    ai = app.settings.get("ai") or {}
                    if not fake and not ai.get("enabled"):
                        raise ValueError("AI 未启用：请先在设置中启用，再运行任务")
                    if not fake and not config.secret_status().get("deepseek"):
                        raise ValueError("未配置 DeepSeek Key，无法发起真实请求")
                    ai_jobs.recover_stale(conn, job_id=job_id)
                    provider = ai_provider.provider_from_settings(app.settings, fake=fake)
                    result = ai_jobs.run_job(conn, job_id, provider, settings=app.settings)
                    if is_api:
                        self._json(result)
                    else:
                        self._redirect(f"/ai?msg={urllib.parse.quote(f'任务 {job_id}: {result.get('status')}')}")
                    return
                else:
                    raise ValueError("未知操作")
                payload = ai_jobs.status(conn, job_id)
            if is_api:
                self._json(payload or {"error": "not_found"})
            else:
                self._redirect(f"/ai?msg={urllib.parse.quote('任务状态已更新')}")
            return
        if path == "/import/preview":
            result = importer.preview(str(data.get("path") or ""))
            with self._conn() as conn:
                sources = store.list_sources(conn)
            self._html(render.import_page(result, sources=sources, csrf=app.csrf))
            return
        if path == "/import/commit":
            with self._conn() as conn:
                result = importer.commit(
                    conn, str(data.get("source_id") or ""), str(data.get("path") or ""),
                    expected_snapshot=str(data.get("snapshot") or "") or None,
                )
            self._redirect(f"/feed?msg={urllib.parse.quote(f'导入完成：新增 {result['inserted']}，更新 {result['updated']}，未变 {result['unchanged']}')}")
            return
        if is_api:
            self._api_write(method, path[len("/api/radar"):], data)
            return
        raise ValueError("未知操作")

    # -- JSON API ---------------------------------------------------------
    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        with self._conn() as conn:
            if path in {"", "/"}:
                self._json({"service": "mindos-radar", "version": "0.1", "docs": "docs/API.md"})
                return
            if path == "/sources":
                sources = [_source_view(row) for row in store.list_sources(conn)]
                result = registry_mod.filter_sources(
                    sources,
                    category=_q(query, "category") or None,
                    cost_tier=_q(query, "cost") or None,
                    status=_q(query, "status") or None,
                    query=_q(query, "q") or None,
                    adapter=_q(query, "adapter") or None,
                )
                limit = max(1, min(200, _qint(query, "limit", 50)))
                offset = max(0, _qint(query, "offset", 0))
                self._json({"count": len(result), "items": result[offset:offset + limit]})
                return
            if path.startswith("/sources/"):
                row = store.get_source(conn, path.rsplit("/", 1)[1])
                if not row:
                    self._json({"error": "not_found"}, 404)
                else:
                    self._json(_source_view(row))
                return
            if path == "/subscriptions":
                self._json({"items": store.list_subscriptions(conn)})
                return
            if path.startswith("/subscriptions/"):
                self._json(store.get_subscription(conn, int(path.rsplit("/", 1)[1])) or {"error": "not_found"})
                return
            if path.startswith("/runs/"):
                row = conn.execute("select * from radar_runs where id=?", (int(path.rsplit("/", 1)[1]),)).fetchone()
                self._json(dict(row) if row else {"error": "not_found"})
                return
            if path == "/items":
                page = max(1, _qint(query, "page", 1))
                limit = max(1, min(100, _qint(query, "limit", 30)))
                items = store.list_items(
                    conn, query=_q(query, "q") or None, source_id=_q(query, "source") or None,
                    unread=_qbool(query, "unread") or None, favorites=_qbool(query, "favorites") or None,
                    since=_q(query, "since") or None, order=_q(query, "order") or "published",
                    limit=limit + 1, offset=(page - 1) * limit,
                )
                self._json({"page": page, "limit": limit, "has_next": len(items) > limit, "items": items[:limit]})
                return
            if path.startswith("/items/"):
                item = store.get_item(conn, int(path.rsplit("/", 1)[1]))
                self._json(item or {"error": "not_found"})
                return
            if path == "/topics":
                self._json({"items": store.list_topics(conn)})
                return
            if path.startswith("/topics/") and path.endswith("/matches"):
                topic_id = int(path.split("/")[2])
                self._json({"items": store.list_items(conn, topic_id=topic_id, limit=50)})
                return
            if path == "/stats":
                self._json({"overview": stats.overview(conn), "quality": analysis.quality_metrics(conn)})
                return
            if path == "/settings/ai":
                ai = dict((self.app.settings.get("ai") or {}))
                ai.pop("api_key", None)
                self._json({"ai": ai, "secret_configured": config.secret_status()})
                return
            if path == "/ai/jobs":
                self._json({"items": ai_jobs.list_jobs(conn, limit=max(1, min(100, _qint(query, "limit", 20))))})
                return
            if path.startswith("/ai/jobs/"):
                job_id = int(path.split("/")[3] if len(path.split("/")) > 3 else 0)
                self._json(ai_jobs.status(conn, job_id) or {"error": "not_found"})
                return
        self._json({"error": "not_found"}, 404)

    def _api_write(self, method: str, path: str, data: dict[str, Any]) -> None:
        if path.startswith("/sources/") and path.endswith("/probe"):
            source_id = path.split("/")[2]
            with self._conn() as conn:
                source = store.get_source(conn, source_id)
                if not source:
                    self._json({"error": "not_found"}, 404)
                    return
                status = (source.get("config") or {}).get("verification", {}).get("status")
                if status not in {"integrated", "live_test_pass"}:
                    self._json({"error": "not_verified", "message": "来源未通过审核"}, 403)
                    return
                self._json(engine.probe_source(conn, source_id, settings=self.app.settings))
            return
        if path == "/subscriptions":
            with self._conn() as conn:
                sub_id = store.create_subscription(
                    conn, str(data.get("source_id") or ""),
                    enabled=bool(data.get("enabled")),
                    interval_minutes=int(data.get("interval_minutes") or 360),
                )
            self._json({"id": sub_id}, 201)
            return
        if path.startswith("/subscriptions/"):
            sub_id = int(path.split("/")[2])
            with self._conn() as conn:
                if path.endswith("/collect"):
                    self._json(engine.collect_subscription(conn, sub_id, settings=self.app.settings, force=True))
                    return
                store.set_subscription(
                    conn, sub_id,
                    **{key: data[key] for key in ("enabled", "interval_minutes", "next_run_at") if key in data},
                )
                self._json({"ok": True})
            return
        if path.startswith("/items/") and path.endswith("/state"):
            item_id = int(path.split("/")[2])
            with self._conn() as conn:
                store.set_state(
                    conn, item_id,
                    read=data.get("read") if "read" in data else None,
                    favorite=data.get("favorite") if "favorite" in data else None,
                    note=data.get("note"),
                )
            self._json({"ok": True})
            return
        if path == "/topics":
            with self._conn() as conn:
                topic_id = store.create_topic(
                    conn, name=str(data.get("name") or ""),
                    include_terms=data.get("include_terms") or [],
                    exclude_terms=data.get("exclude_terms") or [],
                )
                store.evaluate_topic(conn, topic_id)
            self._json({"id": topic_id}, 201)
            return
        if path == "/ai/preview":
            ids = [int(item) for item in data.get("item_ids") or []]
            with self._conn() as conn:
                self._json(ai_jobs.preview(conn, ids, self.app.settings, task_type=str(data.get("task_type") or "brief")))
            return
        if path == "/ai/jobs":
            ids = [int(item) for item in data.get("item_ids") or []]
            with self._conn() as conn:
                job_id = ai_jobs.create_job(
                    conn, ids, settings=self.app.settings, task_type=str(data.get("task_type") or "brief"),
                )
            self._json({"job_id": job_id}, 201)
            return
        if path.startswith("/ai/jobs/"):
            parts = path.split("/")
            job_id = int(parts[3])
            action = parts[4] if len(parts) > 4 else ""
            with self._conn() as conn:
                if action == "pause":
                    ai_jobs.pause_job(conn, job_id)
                elif action == "resume":
                    ai_jobs.resume_job(conn, job_id)
                elif action == "cancel":
                    ai_jobs.cancel_job(conn, job_id)
                elif action == "retry":
                    ai_jobs.retry_failed(conn, job_id, include_unknown=bool(data.get("include_unknown")))
                elif action == "run":
                    fake = bool(data.get("fake"))
                    if not fake:
                        ai = self.app.settings.get("ai") or {}
                        if not ai.get("enabled") or not config.secret_status().get("deepseek"):
                            self._json({"error": "ai_disabled", "message": "AI 未启用或未配置 Key"}, 409)
                            return
                    ai_jobs.recover_stale(conn, job_id=job_id)
                    result = ai_jobs.run_job(
                        conn, job_id, ai_provider.provider_from_settings(self.app.settings, fake=fake),
                        settings=self.app.settings,
                    )
                    self._json(result)
                    return
                else:
                    self._json({"error": "not_found"}, 404)
                    return
            return
        self._json({"error": "not_found"}, 404)


def create_server(host: str = "127.0.0.1", port: int = 8770, *, settings: dict[str, Any] | None = None,
                  schedule: bool = False) -> tuple[ThreadingHTTPServer, App]:
    if host not in ALLOWED_HOSTS:
        raise ValueError("只允许绑定本机回环地址")
    app = App(settings=settings)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    if schedule:
        app.start_scheduler()
    return server, app


def serve(host: str = "127.0.0.1", port: int = 8770, *, schedule: bool = False) -> None:
    server, app = create_server(host, port, schedule=schedule)
    print(f"MindOS Radar 已启动：http://{host}:{server.server_address[1]}/")
    print("仅本机可访问；Ctrl+C 停止。调度：" + ("开启" if schedule else "关闭"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_scheduler()
        server.server_close()
