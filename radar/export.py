"""导出（A4）：JSON / CSV / Markdown，CSV 防公式注入，导出文件不进入 Git。"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import store

CSV_COLUMNS = ["id", "source_id", "source_name", "title", "summary", "url", "published_at", "collected_at", "read", "favorite", "note"]
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(DANGEROUS_PREFIXES):
        return "'" + text
    return text.replace("\r\n", " ").replace("\n", " ")


def _rows(conn, **filters: Any) -> list[dict[str, Any]]:
    return store.list_items(conn, **filters)


def export_json(conn, **filters: Any) -> str:
    rows = _rows(conn, **filters)
    payload = {
        "exported_at": store.now_iso(),
        "count": len(rows),
        "items": [
            {
                "id": row["id"], "source_id": row["source_id"], "source_name": row.get("source_name"),
                "title": row["title"], "summary": row.get("summary"), "url": row.get("canonical_url"),
                "published_at": row.get("published_at"), "collected_at": row.get("collected_at"),
                "read": bool(row.get("read_at")), "favorite": bool(row.get("favorite")), "note": row.get("note", ""),
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_csv(conn, **filters: Any) -> str:
    rows = _rows(conn, **filters)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _csv_safe({
            "id": row["id"], "source_id": row["source_id"], "source_name": row.get("source_name"),
            "title": row["title"], "summary": row.get("summary"), "url": row.get("canonical_url"),
            "published_at": row.get("published_at"), "collected_at": row.get("collected_at"),
            "read": "yes" if row.get("read_at") else "no", "favorite": "yes" if row.get("favorite") else "no",
            "note": row.get("note", ""),
        }.get(column)) for column in CSV_COLUMNS})
    return buffer.getvalue()


def export_markdown(conn, *, title: str | None = None, **filters: Any) -> str:
    rows = _rows(conn, **filters)
    heading = title or f"MindOS Radar 摘要 · {datetime.now(timezone.utc):%Y-%m-%d}"
    lines = [f"# {heading}", "", f"共 {len(rows)} 条，导出时间 {store.now_iso()}", ""]
    for row in rows:
        stamp = (row.get("published_at") or row.get("collected_at") or "")[:10]
        link = row.get("canonical_url")
        head = f"[{row['title']}]({link})" if link else row["title"]
        lines.append(f"- **{stamp}** · {row.get('source_name') or row['source_id']} · {head}")
        if row.get("summary"):
            lines.append(f"  - {str(row['summary'])[:220]}")
        if row.get("note"):
            lines.append(f"  - 备注：{row['note']}")
    return "\n".join(lines) + "\n"


EXPORTERS = {"json": export_json, "csv": export_csv, "md": export_markdown, "markdown": export_markdown}


def write_export(conn, path: Path | str, fmt: str = "json", **filters: Any) -> Path:
    exporter = EXPORTERS.get(fmt)
    if exporter is None:
        raise ValueError(f"不支持的导出格式: {fmt}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = exporter(conn, **filters)
    target.write_text(content, encoding="utf-8")
    return target


def export_scope_warning() -> str:
    return "导出内容可能包含第三方元数据与你自己的备注，默认不要提交到 Git 或发送给外部服务。"
