"""采集编排：单订阅采集、到期批量采集、连通性探测。

原则：
* 任何异常都落库为运行记录，不向上抛出导致整批中断；
* 单源失败不影响其它源；
* 采集只保存元数据与摘要，是否保存正文由来源 rights 决定；
* 不重试 401/403，不绕过任何访问控制。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config, store
from .collectors import build_collector
from .httpclient import FetchError, HttpClient, safe_error, safe_url


def build_client(settings: dict[str, Any] | None = None) -> HttpClient:
    cfg = (settings or config.load_settings()).get("collector", {})
    return HttpClient(
        timeout_seconds=float(cfg.get("timeout_seconds", 20)),
        max_bytes=int(cfg.get("max_bytes", 4 * 1024 * 1024)),
        retry_count=int(cfg.get("retry_count", 2)),
        host_min_interval_seconds=float(cfg.get("host_min_interval_seconds", 1.0)),
        user_agent=str(cfg.get("user_agent", "MindOS-Radar/0.1")),
    )


def _next_run(interval_minutes: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=max(1, interval_minutes))
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rights_state(source: dict[str, Any]) -> str:
    rights = source.get("rights") or {}
    if rights.get("fulltext") == "allowed":
        return "fulltext_local"
    return "metadata_only"


def collect_subscription(
    conn,
    subscription_id: int,
    *,
    client: HttpClient | None = None,
    settings: dict[str, Any] | None = None,
    force: bool = False,
    max_items_override: int | None = None,
) -> dict[str, Any]:
    settings = settings or config.load_settings()
    sub = store.get_subscription(conn, subscription_id)
    if sub is None:
        return {"status": "FAILED", "error_code": "not_found", "safe_error": "订阅不存在"}
    if not sub["enabled"] and not force:
        return {"status": "SKIPPED", "reason": "订阅未启用"}
    source = sub["source"]
    source_cfg = {
        "id": source["id"],
        "name": source.get("name"),
        "access": source.get("access") or {},
        "collector": source.get("collector") or {},
        "rights": source.get("rights") or {},
        "verification": source.get("verification") or {},
    }
    adapter = (source_cfg["collector"].get("adapter") or "").strip()
    run_id = store.start_run(
        conn, subscription_id=subscription_id, source_id=source["id"], cursor_before=sub.get("cursor")
    )
    client = client or build_client(settings)
    if max_items_override:
        source_cfg["collector"] = {**source_cfg["collector"], "max_items_per_run": int(max_items_override)}

    try:
        collector = build_collector(adapter, client, {
            "secret_getter": lambda name: config.get_secret(
                name, {"deepseek_api_key": "RADAR_DEEPSEEK_API_KEY", "github_token": "RADAR_GITHUB_TOKEN"}.get(name)
            )
        })
        result = collector.collect(source_cfg, {
            "id": subscription_id,
            "parameters": sub.get("parameters") or {},
            "cursor": sub.get("cursor"),
        })
    except FetchError as exc:
        store.finish_run(
            conn, run_id, status="FAILED", error_code=exc.code, safe_error=exc.message,
            counts={"adapter": adapter},
        )
        with conn:
            conn.execute(
                "update radar_subscriptions set last_error=?, next_run_at=? where id=?",
                (f"{exc.code}: {exc.message}"[:300], _next_run(int(sub["interval_minutes"])), subscription_id),
            )
        return {"status": "FAILED", "run_id": run_id, "error_code": exc.code, "safe_error": exc.message}
    except ValueError as exc:
        store.finish_run(conn, run_id, status="FAILED", error_code="config", safe_error=safe_error(exc))
        return {"status": "FAILED", "run_id": run_id, "error_code": "config", "safe_error": safe_error(exc)}
    except Exception as exc:  # noqa: BLE001 防御：任何适配器缺陷都不能拖垮批量
        store.finish_run(conn, run_id, status="FAILED", error_code="adapter_error", safe_error=safe_error(exc))
        return {"status": "FAILED", "run_id": run_id, "error_code": "adapter_error", "safe_error": safe_error(exc)}

    if result.not_modified:
        store.finish_run(conn, run_id, status="NOT_MODIFIED", counts={"adapter": adapter}, cursor_after=sub.get("cursor"))
        with conn:
            conn.execute(
                "update radar_subscriptions set last_success_at=?, last_error=null, next_run_at=? where id=?",
                (store.now_iso(), _next_run(int(sub["interval_minutes"])), subscription_id),
            )
        return {"status": "NOT_MODIFIED", "run_id": run_id, "inserted": 0, "updated": 0, "unchanged": 0}

    cap = int(source_cfg["collector"].get("max_items_per_run") or 30)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0, "adapter": adapter}
    inserted_ids: list[int] = []
    for item in result.items[:cap]:
        try:
            outcome, item_id = store.upsert_item(
                conn,
                source_id=source["id"],
                item=item,
                subscription_id=subscription_id,
                rights_state=_rights_state(source_cfg),
            )
        except ValueError:
            counts["skipped"] += 1
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "inserted":
            inserted_ids.append(item_id)
    if result.observations:
        counts.update({f"obs_{key}": value for key, value in store.save_observations(conn, source["id"], result.observations).items()})

    cursor = None
    if result.etag or result.last_modified:
        cursor = json.dumps({"etag": result.etag, "last_modified": result.last_modified})
    store.finish_run(
        conn, run_id, status="SUCCEEDED", counts=counts, cursor_after=cursor
    )
    with conn:
        conn.execute(
            "update radar_subscriptions set cursor=coalesce(?, cursor), last_success_at=?, last_error=null, next_run_at=? where id=?",
            (cursor, store.now_iso(), _next_run(int(sub["interval_minutes"])), subscription_id),
        )
    return {
        "status": "SUCCEEDED",
        "run_id": run_id,
        "inserted": counts["inserted"],
        "updated": counts["updated"],
        "unchanged": counts["unchanged"],
        "warnings": result.warnings[:5],
        "inserted_ids": inserted_ids,
        "raw_bytes": result.raw_bytes,
    }


def collect_due(
    conn,
    *,
    limit: int = 5,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    settings = settings or config.load_settings()
    client = build_client(settings)
    results = []
    for sub in store.due_subscriptions(conn)[:limit]:
        results.append(
            {"subscription_id": sub["id"], "source_id": sub["source_id"], **collect_subscription(
                conn, sub["id"], client=client, settings=settings
            )}
        )
    return results


def probe_source(conn, source_id: str, *, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """连通性探测：只取最多 3 条，不写条目，不改变验证状态。"""
    settings = settings or config.load_settings()
    source = store.get_source(conn, source_id)
    if source is None:
        return {"status": "FAILED", "error_code": "not_found", "safe_error": "来源不存在"}
    adapter = (source["config"].get("collector") or {}).get("adapter") or ""
    source_cfg = {
        "id": source_id,
        "name": source.get("name"),
        "access": source["config"].get("access") or {},
        "collector": {**(source["config"].get("collector") or {}), "max_items_per_run": 3},
        "rights": source["config"].get("rights") or {},
        "verification": source["config"].get("verification") or {},
    }
    client = build_client(settings)
    started = datetime.now(timezone.utc)
    try:
        collector = build_collector(adapter, client)
        result = collector.collect(source_cfg, {"parameters": {}, "cursor": None})
    except FetchError as exc:
        return {
            "status": "FAILED", "adapter": adapter, "error_code": exc.code, "safe_error": exc.message,
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "FAILED", "adapter": adapter, "error_code": "adapter_error", "safe_error": safe_error(exc),
            "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        }
    return {
        "status": "PASS" if (result.items or result.observations) else "EMPTY",
        "adapter": adapter,
        "items": len(result.items),
        "observations": len(result.observations),
        "warnings": result.warnings[:5],
        "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        "sample_titles": [item.get("title", "")[:80] for item in result.items[:3]],
        "endpoint": safe_url(str((source_cfg["access"] or {}).get("base_url") or "")),
    }
