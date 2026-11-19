"""数据读写层：来源、订阅、条目、状态、运行记录、主题、观测值。

所有写操作都在事务内完成；条目按 (source_id, external_id) 去重，
内容变化时更新内容并把已有分析标记为 stale（不删除历史分析）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import db
from .collectors.base import canonicalize_url, content_hash

RUN_STATUSES = {"RUNNING", "SUCCEEDED", "FAILED", "NOT_MODIFIED", "CANCELLED", "UNKNOWN"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# 来源
# --------------------------------------------------------------------------
def get_source(conn, source_id: str) -> dict[str, Any] | None:
    row = conn.execute("select * from radar_sources where id=?", (source_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["config"] = loads(data.pop("config_json", "{}"), {})
    return data


def list_sources(conn, *, category: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    sql = "select * from radar_sources where 1=1"
    args: list[Any] = []
    if category:
        sql += " and category=?"
        args.append(category)
    if status:
        sql += " and verification_status=?"
        args.append(status)
    sql += " order by category, id"
    out = []
    for row in conn.execute(sql, args):
        data = dict(row)
        data["config"] = loads(data.pop("config_json", "{}"), {})
        out.append(data)
    return out


def source_counts(conn) -> dict[str, int]:
    rows = conn.execute("select verification_status, count(*) as n from radar_sources group by verification_status")
    return {row["verification_status"]: row["n"] for row in rows}


# --------------------------------------------------------------------------
# 订阅
# --------------------------------------------------------------------------
def normalize_params(parameters: dict[str, Any] | None) -> str:
    return dumps(parameters or {})


def create_subscription(
    conn,
    source_id: str,
    *,
    parameters: dict[str, Any] | None = None,
    enabled: bool = False,
    interval_minutes: int | None = None,
) -> int:
    if get_source(conn, source_id) is None:
        raise ValueError(f"来源未注册: {source_id}")
    payload = normalize_params(parameters)
    existing = conn.execute(
        "select id from radar_subscriptions where source_id=? and parameters_json=?", (source_id, payload)
    ).fetchone()
    if existing:
        return int(existing["id"])
    source = get_source(conn, source_id)
    interval = int(interval_minutes or (source or {}).get("config", {}).get("collector", {}).get("default_interval_minutes") or 720)
    with conn:
        cursor = conn.execute(
            "insert into radar_subscriptions(source_id, parameters_json, enabled, interval_minutes, created_at) "
            "values(?,?,?,?,?)",
            (source_id, payload, 1 if enabled else 0, interval, now_iso()),
        )
    return int(cursor.lastrowid)


def list_subscriptions(conn, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = (
        "select s.*, coalesce(r.name,'') as source_name, coalesce(r.category,'') as category, "
        "coalesce(r.verification_status,'') as verification_status "
        "from radar_subscriptions s left join radar_sources r on r.id=s.source_id"
    )
    if enabled_only:
        sql += " where s.enabled=1"
    sql += " order by s.id"
    out = []
    for row in conn.execute(sql):
        data = dict(row)
        data["parameters"] = loads(data.get("parameters_json"), {})
        out.append(data)
    return out


def get_subscription(conn, subscription_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "select s.*, r.config_json, r.name as source_name from radar_subscriptions s "
        "join radar_sources r on r.id=s.source_id where s.id=?",
        (subscription_id,),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["parameters"] = loads(data.get("parameters_json"), {})
    config = loads(data.get("config_json"), {})
    data["source"] = {
        "id": data["source_id"],
        "name": data.get("source_name"),
        "access": config.get("access") or {},
        "collector": config.get("collector") or {},
        "rights": config.get("rights") or {},
        "verification": config.get("verification") or {},
    }
    return data


def set_subscription(conn, subscription_id: int, **fields: Any) -> bool:
    allowed = {"enabled", "interval_minutes", "next_run_at"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return False
    clause = ", ".join(f"{key}=?" for key in updates)
    with conn:
        conn.execute(f"update radar_subscriptions set {clause} where id=?", (*updates.values(), subscription_id))
    return True


def delete_subscription(conn, subscription_id: int) -> None:
    with conn:
        conn.execute("delete from radar_subscriptions where id=?", (subscription_id,))


def due_subscriptions(conn, now: str | None = None) -> list[dict[str, Any]]:
    stamp = now or now_iso()
    return [
        row
        for row in list_subscriptions(conn, enabled_only=True)
        if not row.get("next_run_at") or row["next_run_at"] <= stamp
    ]


# --------------------------------------------------------------------------
# 条目
# --------------------------------------------------------------------------
def _existing_item(conn, source_id: str, external_id: str):
    return conn.execute(
        "select id, content_hash from radar_items where source_id=? and external_id=?",
        (source_id, external_id),
    ).fetchone()


def upsert_item(
    conn,
    *,
    source_id: str,
    item: dict[str, Any],
    subscription_id: int | None = None,
    rights_state: str = "metadata_only",
) -> tuple[str, int]:
    external_id = str(item.get("external_id") or "").strip()[:500]
    if not external_id:
        raise ValueError("item 缺少 external_id")
    title = str(item.get("title") or "(无标题)").strip()[:500]
    summary = (item.get("summary") or "")[:2000] or None
    body = (item.get("body") or "")[:20000] or None
    digest = item.get("content_hash") or content_hash(title, summary, body)
    stamp = now_iso()
    existing = _existing_item(conn, source_id, external_id)
    if existing is None:
        with conn:
            cursor = conn.execute(
                "insert into radar_items(source_id, external_id, canonical_url, kind, title, summary, body, author, "
                "published_at, collected_at, last_seen_at, content_hash, metadata_json, language, rights_state, quality) "
                "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id, external_id, canonicalize_url(item.get("canonical_url")), item.get("kind") or "article",
                    title, summary, body, item.get("author"), item.get("published_at"), stamp, stamp, digest,
                    dumps(item.get("metadata") or {}), item.get("language"), rights_state, item.get("quality") or "ok",
                ),
            )
        item_id = int(cursor.lastrowid)
        outcome = "inserted"
    else:
        item_id = int(existing["id"])
        if existing["content_hash"] == digest:
            with conn:
                conn.execute("update radar_items set last_seen_at=? where id=?", (stamp, item_id))
            outcome = "unchanged"
        else:
            with conn:
                conn.execute(
                    "update radar_items set canonical_url=?, title=?, summary=?, body=?, author=?, published_at=?, "
                    "last_seen_at=?, content_hash=?, metadata_json=?, quality=? where id=?",
                    (
                        canonicalize_url(item.get("canonical_url")), title, summary, body, item.get("author"),
                        item.get("published_at"), stamp, digest, dumps(item.get("metadata") or {}),
                        item.get("quality") or "ok", item_id,
                    ),
                )
                conn.execute("update radar_analyses set stale=1 where item_id=?", (item_id,))
            outcome = "updated"
    if subscription_id is not None:
        with conn:
            conn.execute(
                "insert or ignore into radar_item_subscriptions(item_id, subscription_id) values(?,?)",
                (item_id, subscription_id),
            )
    return outcome, item_id


def save_observations(conn, source_id: str, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    inserted = updated = 0
    with conn:
        for row in rows:
            if not row.get("observed_at") or not row.get("metric"):
                continue
            params_json = dumps(row.get("params") or {})
            existing = conn.execute(
                "select id, value from radar_observations where source_id=? and series_key=? and observed_at=? and metric=?",
                (source_id, row.get("series_key") or "", row["observed_at"], row["metric"]),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "insert into radar_observations(source_id, series_key, observed_at, metric, value, unit, params_json) "
                    "values(?,?,?,?,?,?,?)",
                    (
                        source_id, row.get("series_key") or "", row["observed_at"], row["metric"],
                        row.get("value"), row.get("unit"), params_json,
                    ),
                )
                inserted += 1
            elif existing["value"] != row.get("value"):
                conn.execute("update radar_observations set value=? where id=?", (row.get("value"), existing["id"]))
                updated += 1
    return {"inserted": inserted, "updated": updated}


def get_item(conn, item_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "select i.*, coalesce(s.favorite,0) as favorite, s.read_at, coalesce(s.note,'') as note, "
        "coalesce(r.name, i.source_id) as source_name, coalesce(r.category,'') as category "
        "from radar_items i left join radar_item_state s on s.item_id=i.id "
        "left join radar_sources r on r.id=i.source_id where i.id=?",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["metadata"] = loads(data.get("metadata_json"), {})
    data["analysis"] = get_latest_analysis(conn, item_id)
    return data


def _fts_query(text: str) -> str:
    tokens = [token for token in "".join(ch if ch.isalnum() else " " for ch in text).split() if token]
    return " AND ".join(f'"{token}"' for token in tokens[:8])


def list_items(
    conn,
    *,
    query: str | None = None,
    source_id: str | None = None,
    kind: str | None = None,
    favorites: bool | None = None,
    unread: bool | None = None,
    topic_id: int | None = None,
    since: str | None = None,
    limit: int = 30,
    offset: int = 0,
    order: str = "published",
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    select = (
        "select i.*, coalesce(st.favorite,0) as favorite, st.read_at, coalesce(r.name, i.source_id) as source_name, "
        "coalesce(r.category,'') as category "
        "from radar_items i left join radar_item_state st on st.item_id=i.id "
        "left join radar_sources r on r.id=i.source_id "
    )
    where: list[str] = []
    args: list[Any] = []
    if topic_id is not None:
        select += "join radar_topic_matches tm on tm.item_id=i.id "
        where.append("tm.topic_id=?")
        args.append(topic_id)
    if source_id:
        where.append("i.source_id=?")
        args.append(source_id)
    if kind:
        where.append("i.kind=?")
        args.append(kind)
    if favorites:
        where.append("coalesce(st.favorite,0)=1")
    if unread:
        where.append("st.read_at is null")
    if since:
        where.append("coalesce(i.published_at, i.collected_at) >= ?")
        args.append(since)
    if query:
        fts = _fts_query(query)
        if fts:
            select += "join radar_items_fts f on f.rowid=i.id "
            where.append("radar_items_fts match ?")
            args.append(fts)
    sql = select + (" where " + " and ".join(where) if where else "")
    if order == "collected":
        sql += " order by i.collected_at desc, i.id desc"
    elif order == "updated":
        sql += " order by i.last_seen_at desc, i.id desc"
    else:
        sql += " order by coalesce(i.published_at, i.collected_at) desc, i.id desc"
    sql += " limit ? offset ?"
    args.extend([limit, offset])
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception:  # FTS 未启用或查询语法问题 → 回退 LIKE
        if not query:
            raise
        like = f"%{query}%"
        fallback = select.replace("join radar_items_fts f on f.rowid=i.id ", "")
        gates = [gate for gate in where if "radar_items_fts" not in gate]
        fallback += " where " + " and ".join(gates + ["(i.title like ? or coalesce(i.summary,'') like ?)"])
        fallback_args = [arg for arg, gate in zip(args, where) if "radar_items_fts" not in gate]
        fallback_args += [like, like, limit, offset]
        rows = conn.execute(fallback, fallback_args).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["metadata"] = loads(data.get("metadata_json"), {})
        out.append(data)
    return out


def count_items(conn, *, since: str | None = None, source_id: str | None = None) -> int:
    sql = "select count(*) as n from radar_items where 1=1"
    args: list[Any] = []
    if since:
        sql += " and coalesce(published_at, collected_at) >= ?"
        args.append(since)
    if source_id:
        sql += " and source_id=?"
        args.append(source_id)
    return int(conn.execute(sql, args).fetchone()["n"])


def set_state(
    conn,
    item_id: int,
    *,
    read: bool | None = None,
    favorite: bool | None = None,
    note: str | None = None,
) -> None:
    if not conn.execute("select 1 from radar_items where id=?", (item_id,)).fetchone():
        raise ValueError("条目不存在")
    with conn:
        conn.execute("insert or ignore into radar_item_state(item_id) values(?)", (item_id,))
        if read is not None:
            conn.execute(
                "update radar_item_state set read_at=? where item_id=?",
                (now_iso() if read else None, item_id),
            )
        if favorite is not None:
            conn.execute("update radar_item_state set favorite=? where item_id=?", (1 if favorite else 0, item_id))
        if note is not None:
            conn.execute("update radar_item_state set note=? where item_id=?", (note[:5000], item_id))


# --------------------------------------------------------------------------
# 运行记录
# --------------------------------------------------------------------------
def start_run(conn, *, subscription_id: int | None, source_id: str, cursor_before: str | None) -> int:
    with conn:
        cursor = conn.execute(
            "insert into radar_runs(subscription_id, source_id, status, started_at, cursor_before) values(?,?,?,?,?)",
            (subscription_id, source_id, "RUNNING", now_iso(), cursor_before),
        )
    return int(cursor.lastrowid)


def finish_run(
    conn,
    run_id: int,
    *,
    status: str,
    counts: dict[str, Any] | None = None,
    error_code: str | None = None,
    safe_error: str | None = None,
    cursor_after: str | None = None,
) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(f"非法运行状态: {status}")
    with conn:
        conn.execute(
            "update radar_runs set status=?, ended_at=?, counts_json=?, error_code=?, safe_error=?, cursor_after=? where id=?",
            (
                status, now_iso(), dumps(counts or {}), error_code,
                (safe_error or "")[:300] or None, cursor_after, run_id,
            ),
        )


def cancel_run(conn, run_id: int) -> bool:
    """把仍在 RUNNING 的运行标记为 CANCELLED；已结束的运行不改写。"""
    with conn:
        cursor = conn.execute(
            "update radar_runs set status='CANCELLED', ended_at=?, "
            "safe_error=coalesce(safe_error,'用户取消') where id=? and status='RUNNING'",
            (now_iso(), run_id),
        )
    return bool(cursor.rowcount)


def list_runs(conn, *, limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for row in conn.execute("select * from radar_runs order by id desc limit ?", (max(1, min(limit, 200)),)):
        data = dict(row)
        data["counts"] = loads(data.pop("counts_json", "{}"), {})
        out.append(data)
    return out


def recover_interrupted_runs(conn) -> int:
    """把遗留的 RUNNING 运行标记为 UNKNOWN（进程中断，不能假装成功）。"""
    with conn:
        cursor = conn.execute(
            "update radar_runs set status='UNKNOWN', ended_at=?, safe_error=coalesce(safe_error,'进程中断') "
            "where status='RUNNING'",
            (now_iso(),),
        )
    return cursor.rowcount or 0


# --------------------------------------------------------------------------
# 主题
# --------------------------------------------------------------------------
def create_topic(
    conn,
    *,
    name: str,
    include_terms: list[str],
    exclude_terms: list[str] | None = None,
    source_filter: list[str] | None = None,
    enabled: bool = True,
) -> int:
    clean_include = [term.strip() for term in include_terms if term and term.strip()]
    if not name.strip() or not clean_include:
        raise ValueError("主题需要名称和至少一个包含词")
    with conn:
        cursor = conn.execute(
            "insert into radar_topics(name, include_terms_json, exclude_terms_json, source_filter_json, enabled, created_at) "
            "values(?,?,?,?,?,?)",
            (
                name.strip()[:100], dumps(clean_include), dumps(exclude_terms or []),
                dumps(source_filter or []), 1 if enabled else 0, now_iso(),
            ),
        )
    return int(cursor.lastrowid)


def list_topics(conn) -> list[dict[str, Any]]:
    out = []
    for row in conn.execute("select * from radar_topics order by id"):
        data = dict(row)
        data["include_terms"] = loads(data.pop("include_terms_json"), [])
        data["exclude_terms"] = loads(data.pop("exclude_terms_json"), [])
        data["source_filter"] = loads(data.pop("source_filter_json"), [])
        data["match_count"] = int(
            conn.execute("select count(*) as n from radar_topic_matches where topic_id=?", (data["id"],)).fetchone()["n"]
        )
        out.append(data)
    return out


def get_topic(conn, topic_id: int) -> dict[str, Any] | None:
    for topic in list_topics(conn):
        if topic["id"] == topic_id:
            return topic
    return None


def update_topic(conn, topic_id: int, **fields: Any) -> None:
    mapping = {
        "name": "name",
        "include_terms": "include_terms_json",
        "exclude_terms": "exclude_terms_json",
        "source_filter": "source_filter_json",
        "enabled": "enabled",
    }
    updates: dict[str, Any] = {}
    for key, column in mapping.items():
        if key in fields:
            value = fields[key]
            if key.endswith("terms") or key == "source_filter":
                updates[column] = dumps(value or [])
            elif key == "enabled":
                updates[column] = 1 if value else 0
            else:
                updates[column] = value
    if not updates:
        return
    with conn:
        conn.execute(
            "update radar_topics set " + ", ".join(f"{column}=?" for column in updates) + " where id=?",
            (*updates.values(), topic_id),
        )
    evaluate_topic(conn, topic_id)


def delete_topic(conn, topic_id: int) -> None:
    with conn:
        conn.execute("delete from radar_topics where id=?", (topic_id,))


def _matches(topic: dict[str, Any], title: str, summary: str) -> bool:
    haystack = f"{title} {summary}".lower()
    includes = [term.lower() for term in topic.get("include_terms") or []]
    excludes = [term.lower() for term in topic.get("exclude_terms") or []]
    if not includes or not any(term in haystack for term in includes):
        return False
    return not any(term in haystack for term in excludes)


def evaluate_topic(conn, topic_id: int, *, rule_version: str = "v1") -> int:
    topic = get_topic(conn, topic_id)
    if not topic or not topic["enabled"]:
        return 0
    sources = topic.get("source_filter") or []
    sql = "select id, title, coalesce(summary,'') as summary, source_id from radar_items"
    args: list[Any] = []
    if sources:
        sql += " where source_id in (" + ",".join("?" for _ in sources) + ")"
        args.extend(sources)
    matched = 0
    with conn:
        for row in conn.execute(sql, args):
            if not _matches(topic, row["title"], row["summary"]):
                continue
            conn.execute(
                "insert or ignore into radar_topic_matches(topic_id, item_id, matched_at, rule_version) values(?,?,?,?)",
                (topic_id, row["id"], now_iso(), rule_version),
            )
            matched += 1
    return matched


def evaluate_all_topics(conn, *, rule_version: str = "v1") -> dict[str, int]:
    return {str(topic["id"]): evaluate_topic(conn, topic["id"], rule_version=rule_version) for topic in list_topics(conn)}


# --------------------------------------------------------------------------
# 分析结果读取（写入由 ai/jobs.py 负责）
# --------------------------------------------------------------------------
def get_latest_analysis(conn, item_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "select * from radar_analyses where item_id=? and stale=0 order by generated_at desc limit 1", (item_id,)
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["output"] = loads(data.pop("output_json", "{}"), {})
    data["usage"] = loads(data.pop("usage_json", "{}"), {})
    return data


def list_analyses(conn, *, limit: int = 50, task_type: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "select a.*, i.title, i.source_id from radar_analyses a join radar_items i on i.id=a.item_id "
        "where a.stale=0"
    )
    args: list[Any] = []
    if task_type:
        sql += " and a.task_type=?"
        args.append(task_type)
    sql += " order by a.generated_at desc limit ?"
    args.append(max(1, min(limit, 200)))
    out = []
    for row in conn.execute(sql, args):
        data = dict(row)
        data["output"] = loads(data.pop("output_json", "{}"), {})
        out.append(data)
    return out
