"""最小 JSON Schema 校验器（R1-02）。

只实现注册表 schema 用到的关键字，避免为运行时引入第三方依赖：
``type`` / ``required`` / ``properties`` / ``enum`` / ``const`` / ``pattern`` /
``minLength`` / ``maxLength`` / ``minimum``。未知关键字忽略。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "registry" / "schema" / "source.schema.json"

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def load_schema(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else SCHEMA_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _type_ok(value: Any, expected: Any) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        python_type = _TYPE_MAP.get(name)
        if python_type is None:
            continue
        if name == "integer" and isinstance(value, bool):
            continue
        if isinstance(value, python_type):
            return True
    return False


def validate_json_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        errors.append(f"{path}: 类型应为 {expected}")
        return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: 必须等于 {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        errors.append(f"{path}: {instance!r} 不在允许值 {enum}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: 长度小于 {schema['minLength']}")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(f"{path}: 长度大于 {schema['maxLength']}")
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, instance):
            errors.append(f"{path}: 不匹配 pattern {pattern}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: 小于最小值 {schema['minimum']}")

    if isinstance(instance, dict):
        for key in schema.get("required") or []:
            if key not in instance:
                errors.append(f"{path}: 缺少字段 {key}")
        for key, subschema in (schema.get("properties") or {}).items():
            if key in instance:
                errors.extend(validate_json_schema(instance[key], subschema, f"{path}.{key}"))
    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate_json_schema(item, item_schema, f"{path}[{index}]"))
    return errors
