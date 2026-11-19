"""AI 分析任务状态机（L5-02/03/05/06/07/08）。

状态：
  job:  DRAFT → RUNNING → COMPLETED / PARTIAL / PAUSED / CANCELLED / FAILED
  item: PENDING → RUNNING → SUCCEEDED / FAILED / SKIPPED / CANCELLED / UNKNOWN_OUTCOME

安全规则：
* 每个条目的结果立即落库，进程中断不会重复扣费；
* RUNNING 条目带租约，租约过期一律转 UNKNOWN_OUTCOME，绝不自动重试；
* 每次调用前检查预算与请求数上限；
* 只发送标题/摘要（正文需 rights=fulltext_local）且先做 PII 脱敏。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .. import config, store
from .budget import budget_status, cost_of_usage, estimate_cost_cny, estimate_tokens
from .provider import ProviderError, ProviderResult, build_input, redact

JOB_STATUSES = {"DRAFT", "PREVIEWED", "RUNNING", "PAUSED", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
ITEM_RETRYABLE = {"PENDING", "FAILED"}


def _now() -> str:
    return store.now_iso()


def _lease(seconds: int = 600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _job(conn, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("select * from radar_ai_jobs where id=?", (job_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["limits"] = store.loads(data.pop("limits_json", "{}"), {})
    data["selection"] = store.loads(data.pop("selection_snapshot_json", "[]"), [])
    return data


def _input_for(conn, item: dict[str, Any], settings: dict[str, Any], *, task_type: str, provider: str, model: str, prompt_version: str) -> tuple[str, str]:
    ai = settings.get("ai") or {}
    include_body = item.get("rights_state") == "fulltext_local"
    text = build_input(item, include_body=include_body, max_chars=int(ai.get("max_input_chars") or 6000))
    digest = hashlib.sha256(
        f"{task_type}|{provider_version(provider, model)}|{prompt_version}|{text}".encode("utf-8")
    ).hexdigest()
    return text, digest


def provider_version(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def _cached(conn, item_id: int, input_hash: str, task_type: str, provider: str, model: str, prompt_version: str) -> bool:
    row = conn.execute(
        "select 1 from radar_analyses where item_id=? and input_hash=? and task_type=? and provider=? and model=? "
        "and prompt_version=? and stale=0",
        (item_id, input_hash, task_type, provider, model, prompt_version),
    ).fetchone()
    return row is not None


def preview(
    conn,
    item_ids: list[int],
    settings: dict[str, Any] | None = None,
    *,
    provider: str = "deepseek",
    model: str | None = None,
    task_type: str = "brief",
) -> dict[str, Any]:
    settings = settings or config.load_settings()
    ai = settings.get("ai") or {}
    model = model or str(ai.get("model") or "deepseek-chat")
    prompt_version = str(ai.get("prompt_version") or "v1")
    max_output = int(ai.get("max_output_tokens") or 800)
    rows = []
    total_prompt = 0
    for item_id in item_ids[: int(ai.get("max_items_per_job") or 10)]:
        item = store.get_item(conn, item_id)
        if item is None:
            rows.append({"item_id": item_id, "status": "missing"})
            continue
        text, digest = _input_for(
            conn, item, settings, task_type=task_type, provider=provider, model=model, prompt_version=prompt_version
        )
        tokens = estimate_tokens(text)
        total_prompt += tokens
        rows.append(
            {
                "item_id": item_id,
                "title": item["title"],
                "source_name": item.get("source_name"),
                "input_chars": len(text),
                "estimated_prompt_tokens": tokens,
                "cached": _cached(conn, item_id, digest, task_type, provider, model, prompt_version),
                "rights_state": item.get("rights_state"),
            }
        )
    estimated = estimate_cost_cny(total_prompt, max_output * max(1, len(rows)), settings)
    return {
        "items": rows,
        "count": len(rows),
        "task_type": task_type,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "estimated_prompt_tokens": total_prompt,
        "estimated_output_tokens_max": max_output * max(1, len(rows)),
        "estimated_cost_cny": str(estimated),
        "budget": budget_status(conn, settings, estimated_cost=estimated),
    }


def create_job(
    conn,
    item_ids: list[int],
    *,
    settings: dict[str, Any] | None = None,
    provider: str = "deepseek",
    model: str | None = None,
    task_type: str = "brief",
    note: str = "",
    include_cached: bool = False,
    max_items: int | None = None,
) -> int:
    settings = settings or config.load_settings()
    ai = settings.get("ai") or {}
    model = model or str(ai.get("model") or "deepseek-chat")
    prompt_version = str(ai.get("prompt_version") or "v1")
    limit = int(max_items or ai.get("max_items_per_job") or 10)
    selection: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item_id in item_ids:
        if item_id in seen or len(selection) >= limit:
            continue
        item = store.get_item(conn, item_id)
        if item is None:
            continue
        text, digest = _input_for(
            conn, item, settings, task_type=task_type, provider=provider, model=model, prompt_version=prompt_version
        )
        if not include_cached and _cached(conn, item_id, digest, task_type, provider, model, prompt_version):
            continue
        selection.append({"item_id": item_id, "input_hash": digest, "title": item["title"][:200], "chars": len(text)})
        seen.add(item_id)
    if not selection:
        raise ValueError("没有需要分析的条目（可能全部已有结果）")
    stamp = _now()
    with conn:
        cursor = conn.execute(
            "insert into radar_ai_jobs(status, provider, model, task_type, selection_snapshot_json, limits_json, "
            "prompt_version, created_at, updated_at, note) values(?,?,?,?,?,?,?,?,?,?)",
            (
                "DRAFT", provider, model, task_type, store.dumps(selection),
                store.dumps({"max_items": limit, "include_cached": include_cached}),
                prompt_version, stamp, stamp, note[:500],
            ),
        )
        job_id = int(cursor.lastrowid)
        for row in selection:
            conn.execute(
                "insert into radar_ai_job_items(job_id, item_id, input_hash, status, updated_at) values(?,?,?,?,?)",
                (job_id, row["item_id"], row["input_hash"], "PENDING", stamp),
            )
    return job_id


def _parse_output(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("输出不是 JSON 对象")
    from . import schema as output_schema

    errors = output_schema.validate_output(data)
    if errors:
        raise ValueError("输出不符合 schema: " + "; ".join(errors[:3]))
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("输出缺少 summary")
    key_points = data.get("key_points") or []
    tags = data.get("tags") or []
    if not isinstance(key_points, list) or not isinstance(tags, list):
        raise ValueError("key_points/tags 必须是数组")
    try:
        relevance = int(data.get("relevance") or 0)
    except (TypeError, ValueError):
        relevance = 0
    return {
        "summary": summary[:400],
        "key_points": [str(point)[:200] for point in key_points][:4],
        "tags": [str(tag)[:40] for tag in tags][:5],
        "category": str(data.get("category") or "")[:40],
        "relevance": max(0, min(5, relevance)),
        "action": str(data.get("action") or "")[:80],
        "provider_label": "ai",
    }


def _record_usage(
    conn,
    *,
    job_id: int,
    item_id: int,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    settings: dict[str, Any],
    request_id: str,
    attempt_id: str,
    reconciliation: str = "estimated",
) -> str:
    cost = cost_of_usage(prompt_tokens, completion_tokens, settings)
    with conn:
        conn.execute(
            "insert into radar_ai_usage(request_id, attempt_id, created_at, provider, model, prompt_tokens, "
            "completion_tokens, cost_estimate, currency, rate_version, reconciliation_status) values(?,?,?,?,?,?,?,?,?,?,?)",
            (
                request_id or attempt_id, attempt_id, _now(), provider, model, prompt_tokens, completion_tokens,
                str(cost), "CNY", str((settings.get("ai") or {}).get("price_checked_at") or ""), reconciliation,
            ),
        )
    return str(cost)


def _set_item(conn, job_id: int, item_id: int, *, status: str, error_code: str | None = None,
              safe_error: str | None = None, usage: dict[str, Any] | None = None,
              attempt_id: str | None = None, lease_until: str | None = None, increment: bool = False) -> None:
    with conn:
        conn.execute(
            "update radar_ai_job_items set status=?, error_code=?, safe_error=?, usage_json=?, "
            "attempt_id=coalesce(?, attempt_id), lease_until=?, attempt_count=attempt_count + ?, updated_at=? "
            "where job_id=? and item_id=?",
            (
                status, error_code, (safe_error or "")[:300] or None, store.dumps(usage or {}),
                attempt_id, lease_until, 1 if increment else 0, _now(), job_id, item_id,
            ),
        )


def run_job(
    conn,
    job_id: int,
    provider,
    *,
    settings: dict[str, Any] | None = None,
    max_items: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    settings = settings or config.load_settings()
    ai = settings.get("ai") or {}
    job = _job(conn, job_id)
    if job is None:
        return {"status": "FAILED", "error": "job_not_found"}
    if job["status"] in {"COMPLETED", "CANCELLED"}:
        return {"status": job["status"], "job_id": job_id, "skipped": "already_finished"}
    prompt_version = job["prompt_version"]
    provider_name = getattr(provider, "name", job["provider"])
    model = getattr(provider, "model", job["model"])
    max_output = int(ai.get("max_output_tokens") or 800)
    limit = int(max_items or job["limits"].get("max_items") or 10)

    with conn:
        conn.execute("update radar_ai_jobs set status='RUNNING', updated_at=? where id=?", (_now(), job_id))

    processed = succeeded = failed = unknown = skipped = 0
    rows = conn.execute(
        "select * from radar_ai_job_items where job_id=? and status='PENDING' order by item_id limit ?",
        (job_id, limit),
    ).fetchall()
    pause_reason = ""

    for row in rows:
        current = conn.execute("select cancellation_flag, status from radar_ai_jobs where id=?", (job_id,)).fetchone()
        if current and current["cancellation_flag"]:
            with conn:
                conn.execute("update radar_ai_jobs set status='CANCELLED', updated_at=? where id=?", (_now(), job_id))
                conn.execute(
                    "update radar_ai_job_items set status='CANCELLED', updated_at=? where job_id=? and status='PENDING'",
                    (_now(), job_id),
                )
            return {"status": "CANCELLED", "job_id": job_id, "processed": processed}

        item = store.get_item(conn, int(row["item_id"]))
        if item is None:
            _set_item(conn, job_id, int(row["item_id"]), status="FAILED", error_code="missing_item")
            failed += 1
            continue
        text, digest = _input_for(
            conn, item, settings, task_type=job["task_type"], provider=provider_name, model=model,
            prompt_version=prompt_version,
        )
        if not force and _cached(conn, item["id"], digest, job["task_type"], provider_name, model, prompt_version):
            _set_item(conn, job_id, item["id"], status="SKIPPED")
            skipped += 1
            continue

        estimated = estimate_cost_cny(estimate_tokens(text), max_output, settings)
        gate = budget_status(conn, settings, estimated_cost=estimated)
        if not gate["allowed"]:
            pause_reason = "; ".join(gate["reasons"]) or "预算检查未通过"
            break

        attempt_id = uuid.uuid4().hex
        _set_item(conn, job_id, item["id"], status="RUNNING", attempt_id=attempt_id, lease_until=_lease(), increment=True)
        processed += 1
        try:
            result: ProviderResult = provider.analyze(text, max_output_tokens=max_output)
        except ProviderError as exc:
            if exc.unknown_outcome:
                _set_item(conn, job_id, item["id"], status="UNKNOWN_OUTCOME", error_code=exc.code,
                          safe_error=exc.message, usage={"reconciliation": "unknown"}, attempt_id=attempt_id)
                _record_usage(
                    conn, job_id=job_id, item_id=item["id"], provider=provider_name, model=model,
                    prompt_tokens=0, completion_tokens=0, settings=settings, request_id=attempt_id,
                    attempt_id=attempt_id, reconciliation="unknown",
                )
                unknown += 1
            else:
                _set_item(conn, job_id, item["id"], status="FAILED", error_code=exc.code,
                          safe_error=exc.message, attempt_id=attempt_id)
                failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 未预期异常：结果未知，禁止重试
            _set_item(conn, job_id, item["id"], status="UNKNOWN_OUTCOME", error_code="unexpected",
                      safe_error=f"{type(exc).__name__}", attempt_id=attempt_id)
            unknown += 1
            continue

        try:
            output = _parse_output(result.text)
        except (ValueError, json.JSONDecodeError) as exc:
            _record_usage(
                conn, job_id=job_id, item_id=item["id"], provider=provider_name, model=model,
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                settings=settings, request_id=result.request_id, attempt_id=attempt_id,
            )
            _set_item(conn, job_id, item["id"], status="FAILED", error_code="invalid_output",
                      safe_error=str(exc), attempt_id=attempt_id)
            failed += 1
            continue

        cost = _record_usage(
            conn, job_id=job_id, item_id=item["id"], provider=provider_name, model=model,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            settings=settings, request_id=result.request_id, attempt_id=attempt_id,
        )
        with conn:
            conn.execute(
                "insert into radar_analyses(item_id, input_hash, task_type, provider, model, prompt_version, "
                "schema_version, output_json, quality_status, usage_json, generated_at, stale) "
                "values(?,?,?,?,?,?,?,?,?,?,?,0) "
                "on conflict(item_id, input_hash, task_type, provider, model, prompt_version, schema_version) "
                "do update set output_json=excluded.output_json, quality_status=excluded.quality_status, "
                "usage_json=excluded.usage_json, generated_at=excluded.generated_at, stale=0",
                (
                    item["id"], digest, job["task_type"], provider_name, model, prompt_version, "v1",
                    store.dumps(output), "ok",
                    store.dumps({"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
                                 "cost_cny": cost, "request_id": result.request_id}),
                    _now(),
                ),
            )
        _set_item(conn, job_id, item["id"], status="SUCCEEDED", attempt_id=attempt_id,
                  usage={"cost_cny": cost, "prompt_tokens": result.prompt_tokens,
                         "completion_tokens": result.completion_tokens})
        succeeded += 1

    remaining = conn.execute(
        "select count(*) as n from radar_ai_job_items where job_id=? and status='PENDING'", (job_id,)
    ).fetchone()["n"]
    if pause_reason:
        status = "PAUSED"
    elif failed or unknown:
        status = "PARTIAL"
    else:
        status = "COMPLETED"
    if remaining and status == "COMPLETED":
        status = "PAUSED"
    with conn:
        conn.execute(
            "update radar_ai_jobs set status=?, updated_at=?, note=case when ?='' then note else ? end where id=?",
            (status, _now(), pause_reason, pause_reason[:500], job_id),
        )
    return {
        "status": status, "job_id": job_id, "processed": processed, "succeeded": succeeded,
        "failed": failed, "unknown": unknown, "skipped": skipped, "remaining": int(remaining),
        "pause_reason": pause_reason,
    }


def status(conn, job_id: int) -> dict[str, Any] | None:
    job = _job(conn, job_id)
    if job is None:
        return None
    counts = {
        row["status"]: row["n"]
        for row in conn.execute(
            "select status, count(*) as n from radar_ai_job_items where job_id=? group by status", (job_id,)
        )
    }
    usage = conn.execute(
        "select coalesce(sum(prompt_tokens),0) as p, coalesce(sum(completion_tokens),0) as c, count(*) as n "
        "from radar_ai_usage where attempt_id in (select attempt_id from radar_ai_job_items where job_id=?)",
        (job_id,),
    ).fetchone()
    costs = [row["cost_estimate"] for row in conn.execute(
        "select cost_estimate from radar_ai_usage where attempt_id in "
        "(select attempt_id from radar_ai_job_items where job_id=? and attempt_id is not null)", (job_id,)
    )]
    total = Decimal("0")
    for value in costs:
        try:
            total += Decimal(str(value or "0"))
        except Exception:  # noqa: BLE001
            continue
    job["item_counts"] = counts
    job["usage"] = {
        "requests": usage["n"], "prompt_tokens": usage["p"], "completion_tokens": usage["c"],
        "cost_cny": str(total.quantize(Decimal("0.000001"))),
    }
    return job


def pause_job(conn, job_id: int, reason: str = "手动暂停") -> None:
    with conn:
        conn.execute("update radar_ai_jobs set status='PAUSED', note=?, updated_at=? where id=? and status not in ('COMPLETED','CANCELLED')",
                     (reason[:500], _now(), job_id))


def resume_job(conn, job_id: int) -> None:
    with conn:
        conn.execute("update radar_ai_jobs set status='RUNNING', cancellation_flag=0, updated_at=? where id=? and status='PAUSED'",
                     (_now(), job_id))


def cancel_job(conn, job_id: int) -> None:
    with conn:
        conn.execute("update radar_ai_jobs set status='CANCELLED', cancellation_flag=1, updated_at=? where id=?", (_now(), job_id))
        conn.execute("update radar_ai_job_items set status='CANCELLED', updated_at=? where job_id=? and status='PENDING'", (_now(), job_id))


def retry_failed(conn, job_id: int, *, include_unknown: bool = False) -> int:
    allowed = ["FAILED"] + (["UNKNOWN_OUTCOME"] if include_unknown else [])
    placeholders = ",".join("?" for _ in allowed)
    with conn:
        cursor = conn.execute(
            f"update radar_ai_job_items set status='PENDING', error_code=null, safe_error=null, lease_until=null, updated_at=? "
            f"where job_id=? and status in ({placeholders})",
            (_now(), job_id, *allowed),
        )
        conn.execute("update radar_ai_jobs set status='PAUSED', cancellation_flag=0, updated_at=? where id=?", (_now(), job_id))
    return cursor.rowcount or 0


def recover_stale(conn, *, job_id: int | None = None, lease_seconds: int = 600) -> int:
    """租约过期的 RUNNING 条目 → UNKNOWN_OUTCOME，绝不自动重试。"""
    threshold = (datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    sql = "update radar_ai_job_items set status='UNKNOWN_OUTCOME', error_code='lease_expired', safe_error='进程中断或超时，结果未知' where status='RUNNING' and (lease_until is null or lease_until < ?)"
    args: list[Any] = [threshold]
    if job_id is not None:
        sql += " and job_id=?"
        args.append(job_id)
    with conn:
        cursor = conn.execute(sql, args)
    return cursor.rowcount or 0


def list_jobs(conn, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = []
    for row in conn.execute("select * from radar_ai_jobs order by id desc limit ?", (max(1, min(limit, 100)),)):
        data = dict(row)
        data["counts"] = {
            item["status"]: item["n"]
            for item in conn.execute(
                "select status, count(*) as n from radar_ai_job_items where job_id=? group by status", (data["id"],)
            )
        }
        rows.append(data)
    return rows
