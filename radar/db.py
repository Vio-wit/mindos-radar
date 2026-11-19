"""SQLite 连接、schema 迁移与 FTS 可用性处理。

设计要点（对应需求第 6、7 节）：
* 独立数据库 ``data/radar.sqlite3``，不改动旧项目 ``notices.sqlite3``；
* 事务化迁移，``schema_meta.schema_version`` 记录版本，可重复执行；
* 外键开启、WAL、busy_timeout，保证 CLI 与 Web 并发时不互相踩踏；
* FTS5 仅在真实可用时启用，缺失时自动回退 LIKE 搜索。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import config

SCHEMA_VERSION = 1

MIGRATION_1 = """
create table if not exists schema_meta (
    key text primary key,
    value text not null
);

create table if not exists radar_sources (
    id text primary key,
    name text not null,
    category text not null,
    config_json text not null default '{}',
    verification_status text not null default 'candidate',
    cost_tier text,
    origin text not null default 'curated',
    updated_at text not null
);

create table if not exists radar_subscriptions (
    id integer primary key autoincrement,
    source_id text not null references radar_sources(id) on delete cascade,
    parameters_json text not null default '{}',
    enabled integer not null default 0,
    interval_minutes integer not null default 360,
    cursor text,
    last_success_at text,
    last_error text,
    next_run_at text,
    created_at text not null,
    unique(source_id, parameters_json)
);

create table if not exists radar_items (
    id integer primary key autoincrement,
    source_id text not null,
    external_id text not null,
    canonical_url text,
    kind text not null default 'article',
    title text not null,
    summary text,
    body text,
    author text,
    published_at text,
    collected_at text not null,
    last_seen_at text not null,
    content_hash text not null,
    metadata_json text not null default '{}',
    language text,
    rights_state text not null default 'unknown',
    quality text not null default 'ok',
    unique(source_id, external_id)
);
create index if not exists idx_radar_items_published on radar_items(published_at desc);
create index if not exists idx_radar_items_source on radar_items(source_id, published_at desc);

create table if not exists radar_item_subscriptions (
    item_id integer not null references radar_items(id) on delete cascade,
    subscription_id integer not null references radar_subscriptions(id) on delete cascade,
    primary key(item_id, subscription_id)
);

create table if not exists radar_item_state (
    item_id integer primary key references radar_items(id) on delete cascade,
    read_at text,
    favorite integer not null default 0,
    note text not null default ''
);

create table if not exists radar_runs (
    id integer primary key autoincrement,
    subscription_id integer,
    source_id text,
    status text not null,
    started_at text not null,
    ended_at text,
    counts_json text not null default '{}',
    error_code text,
    safe_error text,
    cursor_before text,
    cursor_after text
);
create index if not exists idx_radar_runs_started on radar_runs(started_at desc);

create table if not exists radar_topics (
    id integer primary key autoincrement,
    name text not null unique,
    include_terms_json text not null default '[]',
    exclude_terms_json text not null default '[]',
    source_filter_json text not null default '[]',
    enabled integer not null default 1,
    created_at text not null
);

create table if not exists radar_topic_matches (
    topic_id integer not null references radar_topics(id) on delete cascade,
    item_id integer not null references radar_items(id) on delete cascade,
    matched_at text not null,
    rule_version text not null default 'v1',
    primary key(topic_id, item_id)
);

create table if not exists radar_observations (
    id integer primary key autoincrement,
    source_id text not null,
    series_key text not null,
    observed_at text not null,
    metric text not null,
    value real,
    unit text,
    params_json text not null default '{}',
    unique(source_id, series_key, observed_at, metric)
);

create table if not exists radar_ai_jobs (
    id integer primary key autoincrement,
    status text not null default 'DRAFT',
    provider text not null,
    model text not null,
    task_type text not null,
    selection_snapshot_json text not null default '[]',
    limits_json text not null default '{}',
    prompt_version text not null default 'v1',
    created_at text not null,
    updated_at text not null,
    cancellation_flag integer not null default 0,
    note text not null default ''
);

create table if not exists radar_ai_job_items (
    job_id integer not null references radar_ai_jobs(id) on delete cascade,
    item_id integer not null references radar_items(id) on delete cascade,
    input_hash text not null,
    status text not null default 'PENDING',
    attempt_id text,
    attempt_count integer not null default 0,
    usage_json text not null default '{}',
    error_code text,
    safe_error text,
    lease_until text,
    updated_at text not null,
    primary key(job_id, item_id, input_hash)
);

create table if not exists radar_analyses (
    item_id integer not null references radar_items(id) on delete cascade,
    input_hash text not null,
    task_type text not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    schema_version text not null default 'v1',
    output_json text not null,
    quality_status text not null default 'ok',
    usage_json text not null default '{}',
    generated_at text not null,
    stale integer not null default 0,
    primary key(item_id, input_hash, task_type, provider, model, prompt_version, schema_version)
);

create table if not exists radar_ai_usage (
    id integer primary key autoincrement,
    request_id text not null,
    attempt_id text,
    created_at text not null,
    provider text not null,
    model text not null,
    prompt_tokens integer,
    completion_tokens integer,
    cost_estimate text,
    currency text not null default 'CNY',
    rate_version text,
    reconciliation_status text not null default 'estimated'
);
"""


def connect(path: Path | str | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    target = Path(path) if path else config.db_path()
    if read_only:
        conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 5000")
    conn.execute("pragma foreign_keys = ON")
    if not read_only:
        conn.execute("pragma journal_mode = WAL")
        conn.execute("pragma synchronous = NORMAL")
        migrate(conn)
        try:
            ensure_fts(conn)
        except sqlite3.Error:
            pass
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("select value from schema_meta where key='schema_version'").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["value"]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute(
        "create table if not exists schema_meta (key text primary key, value text not null)"
    )
    version = _schema_version(conn)
    if version < 1:
        with conn:
            conn.executescript(MIGRATION_1)
            conn.execute(
                "insert into schema_meta(key,value) values('schema_version','1') "
                "on conflict(key) do update set value=excluded.value"
            )
        version = 1
    return version


def fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("create virtual table if not exists temp.__fts_probe using fts5(x)")
        conn.execute("drop table if exists temp.__fts_probe")
        return True
    except sqlite3.Error:
        return False


def ensure_fts(conn: sqlite3.Connection) -> bool:
    """幂等地建立 FTS 索引与触发器；不支持时返回 False。"""
    if not fts_available(conn):
        return False
    row = conn.execute(
        "select value from schema_meta where key='fts_enabled'"
    ).fetchone()
    if row and row["value"] == "1":
        return True
    with conn:
        conn.executescript(
            """
            create virtual table if not exists radar_items_fts using fts5(
                title, summary, body, content='radar_items', content_rowid='id'
            );
            create trigger if not exists radar_items_ai after insert on radar_items begin
                insert into radar_items_fts(rowid, title, summary, body)
                values (new.id, new.title, coalesce(new.summary,''), coalesce(new.body,''));
            end;
            create trigger if not exists radar_items_ad after delete on radar_items begin
                insert into radar_items_fts(radar_items_fts, rowid, title, summary, body)
                values ('delete', old.id, old.title, coalesce(old.summary,''), coalesce(old.body,''));
            end;
            create trigger if not exists radar_items_au after update on radar_items begin
                insert into radar_items_fts(radar_items_fts, rowid, title, summary, body)
                values ('delete', old.id, old.title, coalesce(old.summary,''), coalesce(old.body,''));
                insert into radar_items_fts(rowid, title, summary, body)
                values (new.id, new.title, coalesce(new.summary,''), coalesce(new.body,''));
            end;
            """
        )
        # backfill existing rows
        conn.execute(
            "insert into radar_items_fts(rowid, title, summary, body) "
            "select id, title, coalesce(summary,''), coalesce(body,'') from radar_items "
            "where id not in (select rowid from radar_items_fts)"
        )
        conn.execute(
            "insert into schema_meta(key,value) values('fts_enabled','1') "
            "on conflict(key) do update set value='1'"
        )
    return True


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
        )
    ]


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}
