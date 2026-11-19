"""旧通知库只读桥接（R1-07）。

* 只以 ``mode=ro`` 打开旧库，并额外用 authorizer 拒绝所有写操作；
* 不复制、不迁移旧表；只读取展示所需的字段；
* 旧库路径必须通过 ``RADAR_NOTICE_DB`` 显式配置；
* 桥接失败必须显式报错，绝不悄悄建库或写入。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_DROP_TRIGGER,
}
if hasattr(sqlite3, "SQLITE_DROP_VIEW"):
    WRITE_ACTIONS.add(sqlite3.SQLITE_DROP_VIEW)
if hasattr(sqlite3, "SQLITE_CREATE_VIEW"):
    WRITE_ACTIONS.add(sqlite3.SQLITE_CREATE_VIEW)


class BridgeUnavailable(RuntimeError):
    """旧库不存在或无法只读打开。"""


def resolve_path(path: Path | str | None = None) -> Path | None:
    if path:
        return Path(path)
    override = os.environ.get("RADAR_NOTICE_DB")
    return Path(override) if override else None


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = resolve_path(path)
    if target is None:
        raise BridgeUnavailable("旧通知库未配置；请显式设置 RADAR_NOTICE_DB。")
    if not target.is_file():
        raise BridgeUnavailable(f"旧通知库不存在：{target}")
    conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma query_only = 1")
    conn.set_authorizer(_deny_writes)
    return conn


def _deny_writes(action: int, arg1: str | None, arg2: str | None, dbname: str | None, trigger: str | None) -> int:
    if action in WRITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK



def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("notices", "attachments", "attachment_extractions", "notice_briefs", "notice_analysis",
                 "tracked_events", "runs"):
        try:
            out[name] = int(conn.execute(f"select count(*) from {name}").fetchone()[0])
        except sqlite3.Error:
            out[name] = -1
    try:
        out["schema_version"] = conn.execute("select value from schema_meta where key='schema_version'").fetchone()[0]
    except (sqlite3.Error, TypeError):
        out["schema_version"] = None
    return out


def list_notices(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    unread: bool | None = None,
    favorites: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    sql = (
        "select n.notice_key, n.title, n.department, n.published_at, n.published_text, n.url, n.notice_type, "
        "n.is_unread, n.is_favorite, n.note, b.summary as brief_summary, a.primary_category "
        "from notices n left join notice_briefs b on b.notice_key=n.notice_key "
        "left join notice_analysis a on a.notice_key=n.notice_key where 1=1"
    )
    args: list[Any] = []
    if query:
        sql += " and (n.title like ? or coalesce(n.department,'') like ?)"
        like = f"%{query}%"
        args.extend([like, like])
    if unread:
        sql += " and n.is_unread=1"
    if favorites:
        sql += " and n.is_favorite=1"
    sql += " order by coalesce(n.published_at, n.first_seen_at) desc limit ? offset ?"
    args.extend([limit, offset])
    return [dict(row) for row in conn.execute(sql, args)]


def get_notice(conn: sqlite3.Connection, notice_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select n.*, b.summary as brief_summary, b.key_points as brief_key_points, b.deadlines as brief_deadlines, "
        "a.primary_category, a.categories, a.keywords "
        "from notices n left join notice_briefs b on b.notice_key=n.notice_key "
        "left join notice_analysis a on a.notice_key=n.notice_key where n.notice_key=?",
        (notice_key,),
    ).fetchone()
    return dict(row) if row else None


def list_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "select event_key, title, national_signup_deadline, school_signup_deadline, "
            "work_submission_deadline, competition_date, status, last_verified_at "
            "from tracked_events order by school_signup_deadline is null, school_signup_deadline"
        )
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]
