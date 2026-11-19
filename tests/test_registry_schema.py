"""注册表 JSON Schema（R1-02）一致性测试。"""
from __future__ import annotations

import copy
import unittest

from helpers import ROOT  # noqa: F401

from radar import registry, schema_check


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = schema_check.load_schema()
        self.result = registry.load_registry()

    def test_all_seed_sources_conform(self) -> None:
        self.assertEqual(len(self.result.errors), 0)
        failures = []
        for source_id, source in self.result.sources.items():
            errors = schema_check.validate_json_schema(source, self.schema)
            if errors:
                failures.append((source_id, errors[:3]))
        self.assertEqual(failures, [])
        self.assertGreaterEqual(len(self.result.sources), 100)

    def test_missing_required_field_detected(self) -> None:
        source = copy.deepcopy(next(iter(self.result.sources.values())))
        source.pop("access")
        errors = schema_check.validate_json_schema(source, self.schema)
        self.assertTrue(any("access" in error for error in errors))

    def test_illegal_enum_and_pattern_detected(self) -> None:
        source = copy.deepcopy(next(iter(self.result.sources.values())))
        source["category"] = "not-a-category"
        source["id"] = "Bad ID!"
        errors = schema_check.validate_json_schema(source, self.schema)
        self.assertTrue(any("category" in error for error in errors))
        self.assertTrue(any("pattern" in error for error in errors))

    def test_default_enabled_is_rejected(self) -> None:
        source = copy.deepcopy(next(iter(self.result.sources.values())))
        source["enabled_by_default"] = True
        errors = schema_check.validate_json_schema(source, self.schema)
        self.assertTrue(any("enabled_by_default" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
