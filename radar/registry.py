"""数据源注册表：加载、校验、筛选与导入。

安全原则（需求第 5 节）：
* 注册表只描述"如何访问"，绝不包含秘密值；
* 未通过审核（verification.status 不是 live_test_pass/integrated）的来源默认禁用；
* 导入外部目录只做索引与字段映射，不执行任何 URL、脚本或命令。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import config

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

CATEGORIES = {
    "news", "policy", "technology", "ai", "opensource", "academic", "campus",
    "jobs", "books", "anime", "media", "games", "audio", "social", "finance",
    "weather", "ecology", "geo", "culture", "maker", "security", "government",
    "opendata", "other",
}
ACCESS_TYPES = {"rss_atom", "rest_json", "open_csv", "school_bridge", "manual"}
COST_TIERS = {"F0", "F1", "F2", "F3", "F4", "FU"}
VERIFICATION = {
    "candidate", "docs_verified", "live_test_pass", "integrated",
    "blocked", "deprecated", "rejected",
}
AUTH_TYPES = {"none", "api_key", "oauth", "token", "login", "unknown"}
RIGHTS_VALUES = {
    "allowed", "review_required", "prohibited_by_default", "unknown", "restricted",
}

FORBIDDEN_SECRET_KEYS = {
    "api_key", "apikey", "password", "passwd", "secret", "token", "cookie",
    "authorization", "bearer", "session",
}

DEFAULTS: dict[str, Any] = {
    "tags": [],
    "access": {
        "type": "rest_json", "base_url": None, "auth": "none", "secret_ref": None,
        "cost_tier": "FU", "quota": None, "quota_verified_at": None,
    },
    "collector": {"adapter": "jsonmap", "default_interval_minutes": 720, "max_items_per_run": 30, "params": {}},
    "rights": {
        "metadata": "review_required", "fulltext": "review_required",
        "download": "prohibited_by_default", "redistribution": "review_required",
        "commercial_use": "review_required", "policy_url": None,
    },
    "verification": {"status": "candidate", "docs_checked_at": None, "live_checked_at": None, "evidence_ref": None},
    "origin": {"type": "curated", "upstream_url": None, "upstream_version": None, "imported_at": None},
    "enabled_by_default": False,
    "schema_version": 1,
}


@dataclass
class RegistryResult:
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _deep_default(base: Any, override: Any) -> Any:
    if isinstance(base, dict):
        out = {k: _deep_default(v, None) if isinstance(v, dict) else v for k, v in base.items()}
        if isinstance(override, dict):
            for key, value in override.items():
                out[key] = _deep_default(out.get(key), value) if isinstance(out.get(key), dict) else value
        return out
    return override if override is not None else base


def normalize(source: dict[str, Any]) -> dict[str, Any]:
    data = _deep_default(DEFAULTS, source)
    collector = data.setdefault("collector", {})
    if not isinstance(collector.get("params"), dict):
        collector["params"] = {}
    data["enabled_by_default"] = False  # 永不因配置默认启用
    return data


def _walk_for_secrets(node: Any, path: str, errors: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_l = str(key).lower()
            if key_l in FORBIDDEN_SECRET_KEYS and value not in (None, "", [], {}):
                errors.append(f"{path}.{key}: 注册表禁止包含秘密值（只允许 secret_ref 名称）")
            _walk_for_secrets(value, f"{path}.{key}", errors)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk_for_secrets(value, f"{path}[{index}]", errors)


def validate(source: dict[str, Any], *, where: str = "") -> list[str]:
    errors: list[str] = []
    prefix = f"{where}: " if where else ""
    data = normalize(source)

    sid = data.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        errors.append(f"{prefix}id 必须匹配 {ID_RE.pattern}")
    if not str(data.get("name") or "").strip():
        errors.append(f"{prefix}缺少 name")
    if data.get("category") not in CATEGORIES:
        errors.append(f"{prefix}category 非法: {data.get('category')!r}")
    if data.get("schema_version") != 1:
        errors.append(f"{prefix}schema_version 必须为 1")

    access = data.get("access") or {}
    if access.get("type") not in ACCESS_TYPES:
        errors.append(f"{prefix}access.type 非法: {access.get('type')!r}")
    if access.get("cost_tier") not in COST_TIERS | {None}:
        errors.append(f"{prefix}access.cost_tier 非法: {access.get('cost_tier')!r}")
    if access.get("auth") not in AUTH_TYPES:
        errors.append(f"{prefix}access.auth 非法: {access.get('auth')!r}")
    secret_ref = access.get("secret_ref")
    if secret_ref is not None and not isinstance(secret_ref, str):
        errors.append(f"{prefix}access.secret_ref 必须是字符串名称或 null")
    base_url = access.get("base_url")
    if base_url is not None and not str(base_url).startswith(("http://", "https://")):
        errors.append(f"{prefix}access.base_url 必须是 http(s) URL")

    collector = data.get("collector") or {}
    if not str(collector.get("adapter") or "").strip():
        errors.append(f"{prefix}collector.adapter 不能为空")
    for key in ("default_interval_minutes", "max_items_per_run"):
        value = collector.get(key)
        if not isinstance(value, int) or value <= 0:
            errors.append(f"{prefix}collector.{key} 必须是正整数")

    verification = data.get("verification") or {}
    if verification.get("status") not in VERIFICATION:
        errors.append(f"{prefix}verification.status 非法: {verification.get('status')!r}")

    rights = data.get("rights") or {}
    for key in ("metadata", "fulltext", "download", "redistribution", "commercial_use"):
        value = rights.get(key)
        if value not in RIGHTS_VALUES:
            errors.append(f"{prefix}rights.{key} 非法: {value!r}")

    if source.get("enabled_by_default", False) is not False:
        errors.append(f"{prefix}enabled_by_default 必须为 false（默认关闭）")

    for url_key in ("provider_url", "docs_url"):
        value = data.get(url_key)
        if value is not None and not str(value).startswith(("http://", "https://")):
            errors.append(f"{prefix}{url_key} 必须是 http(s) URL")

    _walk_for_secrets(source, prefix + "source", errors)
    return errors


def load_document(path: Path) -> list[dict[str, Any]]:
    """支持单源文档或 ``sources:`` 列表文档。"""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return []
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        return [item for item in data["sources"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"无法解析注册表文档: {path}")


def load_registry(root: Path | None = None) -> RegistryResult:
    base = Path(root) if root else config.ROOT / "registry"
    result = RegistryResult()
    for path in sorted(base.rglob("*.y*ml")):
        if "schema" in path.parts:
            continue
        try:
            documents = load_document(path)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            result.errors.append(f"{path.name}: 解析失败: {exc}")
            continue
        for raw in documents:
            data = normalize(raw)
            where = f"{path.name}:{data.get('id') or '?'}"
            problems = validate(raw, where=where)
            if problems:
                result.errors.extend(problems)
                continue
            sid = data["id"]
            if sid in result.sources:
                result.errors.append(f"{where}: 重复 id {sid}")
                continue
            if data["verification"]["status"] in {"candidate", "docs_verified"}:
                result.warnings.append(f"{where}: 尚未通过连通性验证")
            result.sources[sid] = data
    return result


def filter_sources(
    sources: Iterable[dict[str, Any]],
    *,
    category: str | None = None,
    cost_tier: str | None = None,
    status: str | None = None,
    query: str | None = None,
    adapter: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    needle = (query or "").strip().lower()
    for source in sources:
        if category and source.get("category") != category:
            continue
        if cost_tier and (source.get("access") or {}).get("cost_tier") != cost_tier:
            continue
        if status and (source.get("verification") or {}).get("status") != status:
            continue
        if adapter and (source.get("collector") or {}).get("adapter") != adapter:
            continue
        if needle:
            haystack = " ".join(
                str(source.get(key) or "") for key in ("id", "name", "category", "provider_url", "docs_url")
            ).lower() + " " + " ".join(str(t) for t in source.get("tags") or [])
            if needle not in haystack:
                continue
        out.append(source)
    return sorted(out, key=lambda item: (item.get("category", ""), item.get("id", "")))


def sync_to_db(conn, sources: dict[str, dict[str, Any]], *, now: str) -> dict[str, int]:
    """把注册表写入 radar_sources（幂等）。不创建订阅，不启用任何源。"""
    inserted = updated = 0
    for sid, source in sources.items():
        payload = json.dumps(
            {
                "tags": source.get("tags") or [],
                "access": source.get("access") or {},
                "collector": source.get("collector") or {},
                "rights": source.get("rights") or {},
                "verification": source.get("verification") or {},
                "origin": source.get("origin") or {},
                "provider_url": source.get("provider_url"),
                "docs_url": source.get("docs_url"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        row = conn.execute("select id from radar_sources where id=?", (sid,)).fetchone()
        if row:
            conn.execute(
                "update radar_sources set name=?, category=?, config_json=?, verification_status=?, cost_tier=?, origin=?, updated_at=? where id=?",
                (
                    source["name"], source["category"], payload,
                    source["verification"]["status"], source["access"].get("cost_tier"),
                    (source.get("origin") or {}).get("type", "curated"), now, sid,
                ),
            )
            updated += 1
        else:
            conn.execute(
                "insert into radar_sources(id,name,category,config_json,verification_status,cost_tier,origin,updated_at) "
                "values(?,?,?,?,?,?,?,?)",
                (
                    sid, source["name"], source["category"], payload,
                    source["verification"]["status"], source["access"].get("cost_tier"),
                    (source.get("origin") or {}).get("type", "curated"), now,
                ),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated, "total": len(sources)}


def import_candidates(conn, path: Path, *, now: str) -> dict[str, int]:
    """导入外部候选目录（JSON/YAML 列表）；只建索引，不执行任何地址。"""
    result = load_registry_documents(path)
    stats = {"imported": 0, "skipped_duplicate": 0, "rejected": 0, "errors": 0}
    seen = {row["id"] for row in conn.execute("select id from radar_sources")}
    for raw in result["documents"]:
        data = normalize({**raw, "origin": {**DEFAULTS["origin"], "type": "imported"}})
        problems = validate(raw, where=f"import:{data.get('id')}")
        if problems:
            stats["rejected"] += 1
            stats["errors"] += len(problems)
            continue
        if data["id"] in seen:
            stats["skipped_duplicate"] += 1
            continue
        source = {**data, "verification": {**data["verification"], "status": "candidate"}}
        sync_to_db(conn, {data["id"]: source}, now=now)
        seen.add(data["id"])
        stats["imported"] += 1
    return stats


def load_registry_documents(path: Path) -> dict[str, Any]:
    return {"documents": load_document(path)}
