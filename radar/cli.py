"""命令行入口：初始化、采集、分析、导出、AI、导入与本地服务。

所有子命令共享 ``data/`` 下的独立雷达库；旧通知库只通过只读桥接访问。
机器可读输出统一为 JSON，便于脚本与接力开发核对。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import analysis, bridge, config, db, engine, export, importer, stats, store
from . import registry as registry_mod
from .ai import jobs as ai_jobs
from .ai import provider as ai_provider


def _out(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _connect():
    return db.connect()


def _parse_ids(text: str) -> list[int]:
    return [int(part) for part in text.replace(" ", ",").split(",") if part.strip().isdigit()]


def _load_mapping(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    target = Path(path)
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("fields"), dict):
        return payload["fields"]
    if not isinstance(payload, dict):
        raise SystemExit("映射文件必须是 YAML/JSON 对象")
    return payload


# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        result = {"schema_version": db.SCHEMA_VERSION, "db": str(config.db_path()), "fts": db.fts_available(conn)}
        if not args.no_sync:
            registry = registry_mod.load_registry()
            if registry.errors:
                _out({**result, "ok": False, "errors": registry.errors[:10]})
                return 1
            result["registry"] = registry_mod.sync_to_db(conn, registry.sources, now=store.now_iso())
        result["recovered_runs"] = store.recover_interrupted_runs(conn)
        result["tables"] = db.table_names(conn)
        _out({**result, "ok": True})
        return 0
    finally:
        conn.close()


def cmd_registry(args: argparse.Namespace) -> int:
    if args.action in {"validate", "list"}:
        result = registry_mod.load_registry()
        if args.action == "validate":
            from . import schema_check

            schema = schema_check.load_schema()
            schema_errors = []
            for source_id, source in result.sources.items():
                for error in schema_check.validate_json_schema(source, schema):
                    schema_errors.append(f"{source_id}: {error}")
            _out({"ok": not result.errors and not schema_errors, "errors": result.errors,
                  "schema_errors": schema_errors[:20], "warnings": result.warnings,
                  "count": len(result.sources)})
            return 0 if not result.errors and not schema_errors else 1
        rows = registry_mod.filter_sources(
            result.sources.values(), category=args.category, status=args.status, query=args.q
        )
        _out({"count": len(rows), "items": rows})
        return 0
    database = _connect()
    try:
        result = registry_mod.load_registry()
        if result.errors:
            _out({"ok": False, "errors": result.errors[:10]})
            return 1
        counts = registry_mod.sync_to_db(database, result.sources, now=store.now_iso())
        _out({"ok": True, **counts})
        return 0
    finally:
        database.close()


def cmd_source(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        if args.action == "probe":
            _out(engine.probe_source(conn, args.source_id, settings=config.load_settings()))
            return 0
        sources = store.list_sources(conn, category=args.category, status=args.status)
        _out({"count": len(sources), "items": sources})
        return 0
    finally:
        conn.close()


def cmd_sub(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        if args.action == "add":
            sub_id = store.create_subscription(
                conn, args.source_id,
                enabled=args.enable,
                interval_minutes=args.interval,
            )
            _out({"ok": True, "id": sub_id})
            return 0
        if args.action == "list":
            _out({"items": store.list_subscriptions(conn)})
            return 0
        if args.action in {"enable", "disable"}:
            ok = store.set_subscription(conn, args.subscription_id, enabled=args.action == "enable")
            _out({"ok": ok, "id": args.subscription_id})
            return 0
        if args.action == "delete":
            store.delete_subscription(conn, args.subscription_id)
            _out({"ok": True})
            return 0
    finally:
        conn.close()
    return 2


def cmd_collect(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        settings = config.load_settings()
        if args.subscription:
            result = engine.collect_subscription(
                conn, args.subscription, settings=settings, force=args.force,
                max_items_override=args.limit,
            )
            _out(result)
            return 0 if result.get("status") in {"SUCCEEDED", "NOT_MODIFIED"} else 1
        if args.all:
            subs = store.list_subscriptions(conn, enabled_only=True)
            results = [
                engine.collect_subscription(
                    conn, int(sub["id"]), settings=settings, force=True,
                    max_items_override=args.limit,
                )
                for sub in subs
            ]
            _out({"count": len(results), "items": results})
            return 0
        results = engine.collect_due(conn, limit=args.batch, settings=settings)
        _out({"count": len(results), "items": results})
        return 0
    finally:
        conn.close()


def cmd_runs(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        if args.cancel:
            _out({"ok": store.cancel_run(conn, args.cancel), "id": args.cancel})
            return 0
        _out({"items": store.list_runs(conn, limit=args.limit)})
        return 0
    finally:
        conn.close()


def cmd_topics(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        if args.action == "add":
            topic_id = store.create_topic(
                conn, name=args.name,
                include_terms=[part.strip() for part in args.include.split(",") if part.strip()],
                exclude_terms=[part.strip() for part in (args.exclude or "").split(",") if part.strip()],
                enabled=not args.disabled,
            )
            matched = store.evaluate_topic(conn, topic_id)
            _out({"ok": True, "id": topic_id, "matched": matched})
            return 0
        if args.action == "list":
            _out({"items": store.list_topics(conn)})
            return 0
        if args.action == "eval":
            _out(store.evaluate_all_topics(conn))
            return 0
    finally:
        conn.close()
    return 2


def cmd_analyze(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        if args.item:
            _out({"item_id": args.item, "analysis": analysis.store_rule_analysis(conn, args.item)})
            return 0
        _out(analysis.analyze_items(conn, limit=args.limit, missing_only=args.missing))
        return 0
    finally:
        conn.close()


def cmd_stats(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        _out({
            "overview": stats.overview(conn),
            "quality": analysis.quality_metrics(conn),
            "usage": stats.usage_summary(conn),
        })
        return 0
    finally:
        conn.close()


def cmd_export(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        fmt = args.format
        filters = {
            "query": args.q or None,
            "source_id": args.source or None,
            "unread": True if args.unread else None,
            "favorites": True if args.favorites else None,
            "limit": args.limit,
        }
        if args.out:
            path = export.write_export(conn, args.out, fmt=fmt, **filters)
            _out({"ok": True, "path": str(path), "warning": export.export_scope_warning()})
            return 0
        content = export.EXPORTERS[fmt](conn, **filters)
        sys.stdout.write(content)
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# AI
# --------------------------------------------------------------------------
def cmd_ai(args: argparse.Namespace) -> int:
    conn = _connect()
    try:
        settings = config.load_settings()
        if args.action == "preview":
            _out(ai_jobs.preview(conn, _parse_ids(args.items), settings, task_type=args.task))
            return 0
        if args.action == "create":
            job_id = ai_jobs.create_job(conn, _parse_ids(args.items), settings=settings, task_type=args.task)
            _out({"ok": True, "job_id": job_id, "status": ai_jobs.status(conn, job_id)})
            return 0
        if args.action == "jobs":
            _out({"items": ai_jobs.list_jobs(conn, limit=args.limit)})
            return 0
        if args.action == "status":
            payload = ai_jobs.status(conn, args.job_id)
            if payload is None:
                _out({"error": "not_found"})
                return 1
            _out(payload)
            return 0
        if args.action in {"pause", "resume", "cancel"}:
            getattr(ai_jobs, f"{args.action}_job")(conn, args.job_id)
            _out(ai_jobs.status(conn, args.job_id))
            return 0
        if args.action == "retry":
            _out({"retried": ai_jobs.retry_failed(conn, args.job_id, include_unknown=args.include_unknown)})
            return 0
        if args.action == "run":
            ai = settings.get("ai") or {}
            if not args.fake:
                if not ai.get("enabled"):
                    _out({"ok": False, "error": "ai_disabled", "message": "先在设置中启用 AI"})
                    return 1
                if not config.secret_status().get("deepseek"):
                    _out({"ok": False, "error": "no_key", "message": "未配置 DeepSeek Key"})
                    return 1
                if not args.yes:
                    _out({"ok": False, "error": "confirm_required",
                          "message": "真实调用会产生费用，请加 --yes 确认"})
                    return 1
            ai_jobs.recover_stale(conn, job_id=args.job_id)
            result = ai_jobs.run_job(
                conn, args.job_id, ai_provider.provider_from_settings(settings, fake=args.fake),
                settings=settings,
            )
            _out(result)
            return 0 if result.get("status") in {"COMPLETED", "SUCCEEDED"} else 1
    finally:
        conn.close()
    return 2


# --------------------------------------------------------------------------
# 导入 / 桥接 / 备份
# --------------------------------------------------------------------------
def cmd_import(args: argparse.Namespace) -> int:
    if args.action == "preview":
        _out(importer.preview(args.file, mapping=_load_mapping(args.mapping), limit=args.limit))
        return 0
    conn = _connect()
    try:
        _out(importer.commit(
            conn, args.source, args.file,
            mapping=_load_mapping(args.mapping),
            expected_snapshot=args.snapshot,
            rights_state=args.rights_state,
        ))
        return 0
    finally:
        conn.close()


def cmd_bridge(args: argparse.Namespace) -> int:
    try:
        conn = bridge.connect(args.db)
    except bridge.BridgeUnavailable as exc:
        _out({"ok": False, "error": "unavailable", "message": str(exc)})
        return 1
    try:
        if args.action == "stats":
            _out({"ok": True, "stats": bridge.stats(conn)})
        else:
            _out({"ok": True, "items": bridge.list_notices(conn, query=args.q, limit=args.limit)})
        return 0
    finally:
        conn.close()


def cmd_backup(args: argparse.Namespace) -> int:
    source = config.db_path()
    if not source.is_file():
        _out({"ok": False, "error": "db_missing", "path": str(source)})
        return 1
    target_dir = Path(args.out)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"radar-{stamp}.sqlite3"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with sqlite3.connect(target) as check:
        tables = [row[0] for row in check.execute("select name from sqlite_master where type='table' order by name")]
        counts = {name: check.execute(f"select count(*) from {name}").fetchone()[0] for name in tables}
        integrity = check.execute("pragma quick_check").fetchone()[0]
    _out({
        "ok": integrity == "ok",
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": digest,
        "quick_check": integrity,
        "counts": counts,
        "warning": "备份含真实数据，禁止提交到 Git 或上传",
    })
    return 0 if integrity == "ok" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from . import web

    settings = config.load_settings()
    if args.schedule:
        from . import scheduler

        if not scheduler.schedule_enabled(settings):
            print("调度未在设置中启用（settings.schedule.enabled=false），已拒绝开启。")
            return 1
        if scheduler.emergency_stop_file().exists():
            print(f"存在急停文件 {scheduler.emergency_stop_file()}，已拒绝开启调度。")
            return 1
    web.serve(host=args.host, port=args.port or int(settings.get("port") or 8770), schedule=args.schedule)
    return 0


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar", description="MindOS Radar v0.1 本地信息雷达")
    parser.add_argument("--version", action="version", version="mindos-radar 0.1")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化数据库并同步注册表")
    init.add_argument("--no-sync", action="store_true")
    init.set_defaults(func=cmd_init)

    registry = sub.add_parser("registry", help="注册表校验/列表/同步")
    registry.add_argument("action", choices=["validate", "list", "sync"])
    registry.add_argument("--category")
    registry.add_argument("--status")
    registry.add_argument("--q")
    registry.set_defaults(func=cmd_registry)

    source = sub.add_parser("source", help="来源列表与探测")
    source.add_argument("action", choices=["list", "probe"])
    source.add_argument("source_id", nargs="?")
    source.add_argument("--category")
    source.add_argument("--status")
    source.set_defaults(func=cmd_source)

    subscription = sub.add_parser("sub", help="订阅管理")
    subscription.add_argument("action", choices=["add", "list", "enable", "disable", "delete"])
    subscription.add_argument("source_id", nargs="?")
    subscription.add_argument("subscription_id", nargs="?", type=int)
    subscription.add_argument("--interval", type=int, default=360)
    subscription.add_argument("--enable", action="store_true")
    subscription.set_defaults(func=cmd_sub)

    collect = sub.add_parser("collect", help="采集")
    collect.add_argument("--subscription", type=int)
    collect.add_argument("--due", action="store_true")
    collect.add_argument("--all", action="store_true")
    collect.add_argument("--force", action="store_true")
    collect.add_argument("--limit", type=int)
    collect.add_argument("--batch", type=int, default=10)
    collect.set_defaults(func=cmd_collect)

    runs = sub.add_parser("runs", help="运行记录")
    runs.add_argument("--limit", type=int, default=30)
    runs.add_argument("--cancel", type=int, metavar="RUN_ID")
    runs.set_defaults(func=cmd_runs)

    topics = sub.add_parser("topics", help="主题")
    topics.add_argument("action", choices=["add", "list", "eval"])
    topics.add_argument("--name")
    topics.add_argument("--include")
    topics.add_argument("--exclude")
    topics.add_argument("--disabled", action="store_true")
    topics.set_defaults(func=cmd_topics)

    analyze = sub.add_parser("analyze", help="本地规则分析")
    analyze.add_argument("--item", type=int)
    analyze.add_argument("--limit", type=int)
    analyze.add_argument("--missing", action="store_true")
    analyze.set_defaults(func=cmd_analyze)

    stats_cmd = sub.add_parser("stats", help="统计与质量")
    stats_cmd.set_defaults(func=cmd_stats)

    export_cmd = sub.add_parser("export", help="导出")
    export_cmd.add_argument("--format", choices=sorted(export.EXPORTERS), default="json")
    export_cmd.add_argument("--out")
    export_cmd.add_argument("--q")
    export_cmd.add_argument("--source")
    export_cmd.add_argument("--unread", action="store_true")
    export_cmd.add_argument("--favorites", action="store_true")
    export_cmd.add_argument("--limit", type=int, default=500)
    export_cmd.set_defaults(func=cmd_export)

    ai = sub.add_parser("ai", help="AI 预览/任务/运行")
    ai.add_argument("action", choices=["preview", "create", "jobs", "status", "run", "pause", "resume", "cancel", "retry"])
    ai.add_argument("job_id", nargs="?", type=int)
    ai.add_argument("--items", default="")
    ai.add_argument("--task", default="brief", choices=["brief", "classify", "entities"])
    ai.add_argument("--fake", action="store_true", help="本地假 provider（不发外部请求）")
    ai.add_argument("--yes", action="store_true", help="确认真实付费调用")
    ai.add_argument("--include-unknown", action="store_true")
    ai.add_argument("--limit", type=int, default=20)
    ai.set_defaults(func=cmd_ai)

    importer_cmd = sub.add_parser("import", help="本地 CSV/JSON 导入")
    importer_cmd.add_argument("action", choices=["preview", "commit"])
    importer_cmd.add_argument("--file", required=True)
    importer_cmd.add_argument("--source")
    importer_cmd.add_argument("--mapping")
    importer_cmd.add_argument("--snapshot")
    importer_cmd.add_argument("--rights-state", default="metadata_only")
    importer_cmd.add_argument("--limit", type=int, default=30)
    importer_cmd.set_defaults(func=cmd_import)

    old = sub.add_parser("bridge", help="旧通知库只读桥接")
    old.add_argument("action", choices=["stats", "list"])
    old.add_argument("--db")
    old.add_argument("--q")
    old.add_argument("--limit", type=int, default=20)
    old.set_defaults(func=cmd_bridge)

    backup = sub.add_parser("backup", help="备份雷达库（含真实数据，勿上传）")
    backup.add_argument("--out", default=str(Path.home() / "mindos-radar-backup"))
    backup.set_defaults(func=cmd_backup)

    serve = sub.add_parser("serve", help="启动本地 Web 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)
    serve.add_argument("--schedule", action="store_true")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", "") == "sub" and args.action in {"add"} and not args.source_id:
        parser.error("sub add 需要 source_id")
    if getattr(args, "command", "") == "sub" and args.action in {"enable", "disable", "delete"} and not args.subscription_id:
        parser.error("需要 subscription_id")
    if getattr(args, "command", "") == "ai" and args.action in {"status", "run", "pause", "resume", "cancel", "retry"} and not args.job_id:
        parser.error("需要 job_id")
    if getattr(args, "command", "") == "import" and args.action == "commit" and not args.source:
        parser.error("import commit 需要 --source")
    if getattr(args, "command", "") == "topics" and args.action == "add" and not (args.name and args.include):
        parser.error("topics add 需要 --name 与 --include")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
