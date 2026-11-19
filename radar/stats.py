"""统计与质量指标（A4）：概览、分类分布、来源贡献、趋势、关键词、成本。"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from . import store

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "has", "have",
    "will", "into", "about", "after", "over", "their", "they", "you", "your", "our", "its",
    "his", "her", "not", "but", "can", "new", "how", "why", "what", "when", "who", "all",
}


def _scalar(conn, sql: str, args: tuple = ()) -> int:
    row = conn.execute(sql, args).fetchone()
    return int(row[0] or 0) if row else 0


def overview(conn) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")
    totals = {
        "items": _scalar(conn, "select count(*) from radar_items"),
        "items_today": _scalar(conn, "select count(*) from radar_items where substr(coalesce(published_at, collected_at),1,10)=?", (today,)),
        "items_week": _scalar(conn, "select count(*) from radar_items where coalesce(published_at, collected_at) >= ?", (week_ago,)),
        "unread": _scalar(conn, "select count(*) from radar_items i left join radar_item_state s on s.item_id=i.id where s.read_at is null"),
        "favorites": _scalar(conn, "select count(*) from radar_item_state where favorite=1"),
        "notes": _scalar(conn, "select count(*) from radar_item_state where note != ''"),
        "sources": _scalar(conn, "select count(*) from radar_sources"),
        "sources_integrated": _scalar(conn, "select count(*) from radar_sources where verification_status='integrated'"),
        "subscriptions": _scalar(conn, "select count(*) from radar_subscriptions"),
        "subscriptions_enabled": _scalar(conn, "select count(*) from radar_subscriptions where enabled=1"),
        "topics": _scalar(conn, "select count(*) from radar_topics"),
        "observations": _scalar(conn, "select count(*) from radar_observations"),
        "analyses": _scalar(conn, "select count(*) from radar_analyses where stale=0"),
        "runs": _scalar(conn, "select count(*) from radar_runs"),
        "runs_failed": _scalar(conn, "select count(*) from radar_runs where status='FAILED'"),
    }
    by_category = [
        {"category": row["category"] or "unknown", "items": row["n"]}
        for row in conn.execute(
            "select coalesce(r.category,'unknown') as category, count(*) as n from radar_items i "
            "left join radar_sources r on r.id=i.source_id group by 1 order by n desc"
        )
    ]
    top_sources = [
        {"source_id": row["source_id"], "name": row["name"], "items": row["n"]}
        for row in conn.execute(
            "select i.source_id, coalesce(r.name, i.source_id) as name, count(*) as n from radar_items i "
            "left join radar_sources r on r.id=i.source_id group by i.source_id order by n desc limit 10"
        )
    ]
    days: list[dict[str, Any]] = []
    for offset in range(13, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
        days.append({
            "date": day,
            "items": _scalar(
                conn,
                "select count(*) from radar_items where substr(coalesce(published_at, collected_at),1,10)=?",
                (day,),
            ),
        })
    missing_published = _scalar(conn, "select count(*) from radar_items where published_at is null")
    duplicate_urls = _scalar(
        conn,
        "select count(*) from (select canonical_url from radar_items where canonical_url is not null "
        "group by canonical_url having count(*) > 1)",
    )
    usage = usage_summary(conn)
    return {
        "totals": totals,
        "by_category": by_category,
        "top_sources": top_sources,
        "daily": days,
        "quality": {
            "missing_published_at": missing_published,
            "items_without_published_pct": round(missing_published * 100.0 / totals["items"], 1) if totals["items"] else 0.0,
            "duplicate_canonical_urls": duplicate_urls,
        },
        "ai": usage,
    }


def usage_summary(conn) -> dict[str, Any]:
    jobs = {
        row["status"]: row["n"]
        for row in conn.execute("select status, count(*) as n from radar_ai_jobs group by status")
    }
    total_cost = Decimal("0")
    for row in conn.execute("select cost_estimate from radar_ai_usage where cost_estimate is not null"):
        try:
            total_cost += Decimal(str(row["cost_estimate"]))
        except Exception:  # noqa: BLE001
            continue
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_cost = Decimal("0")
    today_requests = 0
    for row in conn.execute(
        "select cost_estimate from radar_ai_usage where substr(created_at,1,10)=?", (today,)
    ):
        today_requests += 1
        try:
            today_cost += Decimal(str(row["cost_estimate"] or "0"))
        except Exception:  # noqa: BLE001
            continue
    return {
        "jobs_by_status": jobs,
        "requests_today": today_requests,
        "cost_today_cny": str(today_cost.quantize(Decimal("0.0001"))),
        "cost_total_cny": str(total_cost.quantize(Decimal("0.0001"))),
        "unknown_outcome": _scalar(conn, "select count(*) from radar_ai_job_items where status='UNKNOWN_OUTCOME'"),
    }


def keyword_frequency(conn, *, limit: int = 25, days: int = 14) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    counter: Counter[str] = Counter()
    rows = conn.execute(
        "select title, coalesce(summary,'') as summary from radar_items "
        "where coalesce(published_at, collected_at) >= ? limit 3000",
        (since,),
    )
    for row in rows:
        text = f"{row['title']} {row['summary']}"
        for word in WORD_RE.findall(text):
            lowered = word.lower()
            if lowered not in STOPWORDS:
                counter[lowered] += 1
        for block in CJK_RE.findall(text):
            for size, weight in ((2, 1), (3, 1)):
                if len(block) < size:
                    continue
                for index in range(len(block) - size + 1):
                    counter[block[index:index + size]] += weight
    return [{"term": term, "count": count} for term, count in counter.most_common(limit)]


def numeric_series(
    conn,
    *,
    source_id: str | None = None,
    metric: str | None = None,
    days: int = 7,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """真实观测序列；缺失日期不补零，避免把“没采到”当成“值为 0”。"""
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat(timespec="seconds")
    sql = [
        "select source_id, series_key, metric, observed_at, value, unit from radar_observations",
        "where value is not null and observed_at >= ?",
    ]
    params: list[Any] = [since]
    if source_id:
        sql.append("and source_id = ?")
        params.append(source_id)
    if metric:
        sql.append("and metric = ?")
        params.append(metric)
    sql.append("order by observed_at asc limit ?")
    params.append(max(1, min(limit, 5000)))
    return [dict(row) for row in conn.execute(" ".join(sql), params)]


def source_health(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select r.id, r.name, r.verification_status, "
        "(select count(*) from radar_items i where i.source_id=r.id) as items, "
        "(select max(i.collected_at) from radar_items i where i.source_id=r.id) as last_item_at, "
        "(select max(started_at) from radar_runs u where u.source_id=r.id) as last_run_at, "
        "(select status from radar_runs u where u.source_id=r.id order by id desc limit 1) as last_run_status "
        "from radar_sources r order by items desc, r.id"
    )
    return [dict(row) for row in rows]


def topic_digest(conn, topic_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
    return store.list_items(conn, topic_id=topic_id, limit=limit)
