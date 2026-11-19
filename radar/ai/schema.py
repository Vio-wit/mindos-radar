"""AI 结构化输出的 JSON Schema 校验（L5-07）。

约束（需求第 8.4 节）：
* 必须返回 JSON 对象，含非空 summary；
* tags / key_points / limitations 为字符串数组且长度受限；
* entities 每项必须有 name/type，evidence 若给出必须能在输入文本中找到；
* 不合法结果一律判失败，不做"猜你想要"式的自动修补。
"""
from __future__ import annotations

from typing import Any

MAX_SUMMARY = 2000
MAX_LIST = 12
MAX_ENTITY = 20
ENTITY_TYPES = {"person", "org", "product", "technology", "location", "event", "other"}


def _is_str_list(value: Any, *, limit: int) -> bool:
    if value is None:
        return True
    return isinstance(value, list) and len(value) <= limit and all(isinstance(item, str) and item.strip() for item in value)


def validate_output(data: Any, *, input_text: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["输出不是 JSON 对象"]
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("缺少非空 summary")
    elif len(summary) > MAX_SUMMARY:
        errors.append(f"summary 超过 {MAX_SUMMARY} 字符")
    for key in ("tags", "key_points", "limitations"):
        if not _is_str_list(data.get(key), limit=MAX_LIST):
            errors.append(f"{key} 必须是字符串数组且不超过 {MAX_LIST} 项")
    entities = data.get("entities")
    if entities is not None:
        if not isinstance(entities, list) or len(entities) > MAX_ENTITY:
            errors.append(f"entities 必须是数组且不超过 {MAX_ENTITY} 项")
        else:
            for index, entity in enumerate(entities):
                if not isinstance(entity, dict):
                    errors.append(f"entities[{index}] 必须是对象")
                    continue
                name = entity.get("name")
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"entities[{index}].name 不能为空")
                kind = entity.get("type")
                if kind is not None and kind not in ENTITY_TYPES:
                    errors.append(f"entities[{index}].type 非法：{kind!r}")
                evidence = entity.get("evidence")
                if evidence is not None:
                    if not isinstance(evidence, str) or not evidence.strip():
                        errors.append(f"entities[{index}].evidence 必须是非空字符串")
                    elif input_text is not None and evidence.strip() not in input_text:
                        errors.append(f"entities[{index}].evidence 无法在输入中找到")
    relevance = data.get("relevance")
    if relevance is not None and not (isinstance(relevance, (int, float)) and 0 <= float(relevance) <= 5):
        errors.append("relevance 必须是 0-5 之间的数字")
    source_ids = data.get("source_item_ids")
    if source_ids is not None and not (
        isinstance(source_ids, list) and all(isinstance(item, int) for item in source_ids)
    ):
        errors.append("source_item_ids 必须是整数数组")
    return errors


def normalize(data: dict[str, Any], *, input_text: str | None = None) -> dict[str, Any]:
    """裁剪超长字段并补齐缺省数组；调用前必须先通过 validate_output。"""
    payload = {
        "summary": str(data.get("summary") or "").strip()[:MAX_SUMMARY],
        "tags": [str(item).strip()[:40] for item in (data.get("tags") or [])][:MAX_LIST],
        "key_points": [str(item).strip()[:300] for item in (data.get("key_points") or [])][:MAX_LIST],
        "limitations": [str(item).strip()[:300] for item in (data.get("limitations") or [])][:MAX_LIST],
        "entities": [],
        "relevance": data.get("relevance"),
        "method": "llm",
    }
    for entity in data.get("entities") or []:
        entry = {
            "name": str(entity.get("name") or "").strip()[:120],
            "type": entity.get("type") or "other",
        }
        evidence = entity.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            entry["evidence"] = evidence.strip()[:300]
        payload["entities"].append(entry)
    return payload


def check_and_normalize(text: str, *, input_text: str | None = None) -> dict[str, Any]:
    """供 jobs 使用：解析由调用方完成，这里只做校验 + 规范化。"""
    import json

    data = json.loads(text)
    errors = validate_output(data, input_text=input_text)
    if errors:
        raise ValueError("; ".join(errors[:5]))
    return normalize(data, input_text=input_text)
