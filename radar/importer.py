"""本地文件导入（C2-05）：CSV / JSON / JSONL 的预览与幂等入库。

规则：
* 只读本地文件，预览阶段不写库、不访问网络；
* 确认写入时按 ``(source_id, external_id)`` 去重，重复预览→提交幂等；
* 限制文件大小、行数与字段长度，错误逐行报告，不污染数据库；
* 文件内容视为不可信数据：不执行其中任何 URL、公式或脚本。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Iterable

from . import store
from .collectors.base import canonicalize_url, clean_text, content_hash, to_utc_iso
from .collectors.jsonmap import get_path

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 5000
FIELDS = ("external_id", "title", "summary", "url", "published_at", "author", "kind", "language")
ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": ("external_id", "id", "guid", "uuid", "url", "link"),
    "title": ("title", "name", "headline"),
    "summary": ("summary", "description", "content", "abstract"),
    "url": ("url", "link", "href"),
    "published_at": ("published_at", "published", "date", "pubDate", "created_at", "updated"),
    "author": ("author", "creator", "by"),
    "kind": ("kind", "type", "category"),
    "language": ("language", "lang"),
}
MAX_ERRORS = 20


class ImportProblem(ValueError):
    """文件不可导入（格式、大小、行数或映射问题）。"""


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    if suffix == ".json":
        return "json"
    head = path.read_text(encoding="utf-8", errors="replace")[:200].lstrip()
    if head.startswith(("{", "[")):
        return "jsonl" if head.startswith("{") and "\n" in head else "json"
    return "csv"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path, fmt: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if fmt == "csv":
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        rows = [dict(row) for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]
        return rows, warnings
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if fmt == "jsonl":
        rows = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                warnings.append(f"第 {index} 行不是合法 JSON：{exc.msg}")
                continue
            if isinstance(value, dict):
                rows.append(value)
            else:
                warnings.append(f"第 {index} 行不是对象，已跳过")
        return rows, warnings
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportProblem(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）") from exc
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)], warnings
        return [payload], warnings
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], warnings
    raise ImportProblem("JSON 顶层必须是对象或数组")


def _pick(row: dict[str, Any], spec: Any, field: str) -> str:
    if spec is None:
        value: Any = None
        for alias in ALIASES.get(field, (field,)):
            value = row.get(alias)
            if value not in (None, ""):
                break
    elif isinstance(spec, (list, tuple)):
        value = None
        for candidate in spec:
            value = _pick(row, candidate, field)
            if value:
                break
        return value
    else:
        text = str(spec)
        value = get_path(row, text)
        if value in (None, ""):
            value = row.get(text)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return clean_text("; ".join(clean_text(item) for item in value if item not in (None, "")), limit=1200)
    if isinstance(value, dict):
        for key in ("name", "title", "value", "text"):
            if key in value:
                return clean_text(value[key], limit=1200)
        return ""
    return clean_text(value, limit=1200)


def map_row(row: dict[str, Any], mapping: dict[str, Any] | None) -> dict[str, Any]:
    mapping = mapping or {}
    item: dict[str, Any] = {}
    for field in FIELDS:
        item[field] = _pick(row, mapping.get(field), field)
    item["external_id"] = item["external_id"].strip()[:500]
    item["title"] = (item["title"].strip() or "(无标题)")[:500]
    item["summary"] = item["summary"][:2000] or None
    item["url"] = canonicalize_url(item["url"] or None)
    item["published_at"] = to_utc_iso(item["published_at"]) if item["published_at"] else None
    item["author"] = item["author"][:200] or None
    item["kind"] = (item["kind"].strip() or "article")[:40]
    item["language"] = (item["language"].strip() or None) and item["language"].strip()[:20]
    item["content_hash"] = content_hash(item["title"], item["summary"])
    return item


def preview(
    path: Path | str,
    *,
    mapping: dict[str, Any] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """只读预览：返回格式、总行数、映射后样例、错误与快照哈希。"""
    target = Path(path)
    if not target.is_file():
        raise ImportProblem(f"文件不存在：{target}")
    size = target.stat().st_size
    if size > MAX_BYTES:
        raise ImportProblem(f"文件过大：{size} 字节（上限 {MAX_BYTES}）")
    fmt = detect_format(target)
    rows, warnings = _read(target, fmt)
    if len(rows) > MAX_ROWS:
        raise ImportProblem(f"行数过多：{len(rows)}（上限 {MAX_ROWS}）")
    mapped: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for index, row in enumerate(rows, start=1):
        item = map_row(row, mapping)
        if not item["external_id"]:
            if len(errors) < MAX_ERRORS:
                errors.append(f"第 {index} 行缺少 external_id，已在提交时跳过")
            continue
        if item["external_id"] in seen:
            duplicates += 1
        seen.add(item["external_id"])
        if len(mapped) < max(1, min(int(limit), 200)):
            mapped.append(item)
    return {
        "path": str(target),
        "format": fmt,
        "bytes": size,
        "rows": len(rows),
        "mapped_rows": len(seen),
        "duplicate_external_ids_in_file": duplicates,
        "errors": errors,
        "warnings": warnings[:MAX_ERRORS],
        "sample": mapped,
        "snapshot": file_digest(target),
        "fields": list(FIELDS),
    }


def commit(
    conn,
    source_id: str,
    path: Path | str,
    *,
    mapping: dict[str, Any] | None = None,
    expected_snapshot: str | None = None,
    rights_state: str = "metadata_only",
    subscription_id: int | None = None,
) -> dict[str, Any]:
    """确认导入：校验快照未变，再逐条幂等写入。"""
    target = Path(path)
    if expected_snapshot and file_digest(target) != expected_snapshot:
        raise ImportProblem("文件在预览后已变化，请重新预览")
    if store.get_source(conn, source_id) is None:
        raise ImportProblem(f"来源未注册：{source_id}")
    fmt = detect_format(target)
    rows, warnings = _read(target, fmt)
    if len(rows) > MAX_ROWS:
        raise ImportProblem(f"行数过多：{len(rows)}（上限 {MAX_ROWS}）")
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0, "adapter": "file_import"}
    for row in rows:
        item = map_row(row, mapping)
        if not item["external_id"]:
            counts["skipped"] += 1
            continue
        try:
            outcome, _ = store.upsert_item(
                conn,
                source_id=source_id,
                item=item,
                subscription_id=subscription_id,
                rights_state=rights_state,
            )
        except ValueError:
            counts["skipped"] += 1
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
    return {"source_id": source_id, "format": fmt, "rows": len(rows), "warnings": warnings[:MAX_ERRORS], **counts}


def commit_mapped(
    conn,
    source_id: str,
    items: Iterable[dict[str, Any]],
    *,
    rights_state: str = "metadata_only",
) -> dict[str, int]:
    """从已映射的行写入（供 API 使用预览快照）。"""
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    for item in items:
        if not item.get("external_id"):
            counts["skipped"] += 1
            continue
        try:
            outcome, _ = store.upsert_item(conn, source_id=source_id, item=item, rights_state=rights_state)
        except ValueError:
            counts["skipped"] += 1
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
