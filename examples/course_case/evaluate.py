"""一致性评估：规则分类 vs 人工标注（A4-06）。

输入 CSV 需含 `rule_category` 与 `human_category`；空白行忽略。
输出准确率、Cohen's kappa、每类计数与混淆对，便于课程报告引用。
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def kappa(pairs: list[tuple[str, str]]) -> float:
    if not pairs:
        return 0.0
    total = len(pairs)
    observed = sum(1 for rule, human in pairs if rule == human) / total
    rule_counts = Counter(rule for rule, _ in pairs)
    human_counts = Counter(human for _, human in pairs)
    expected = sum((rule_counts[key] / total) * (human_counts[key] / total) for key in set(rule_counts) | set(human_counts))
    if expected >= 1:
        return 0.0
    return (observed - expected) / (1 - expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    path = Path(args.annotations)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    pairs = [
        ((row.get("rule_category") or "").strip(), (row.get("human_category") or "").strip())
        for row in rows
        if (row.get("rule_category") or "").strip() and (row.get("human_category") or "").strip()
    ]
    mismatches = [pair for pair in pairs if pair[0] != pair[1]]
    result = {
        "rows": len(rows),
        "comparable": len(pairs),
        "agreement": round(sum(1 for rule, human in pairs if rule == human) / len(pairs), 4) if pairs else None,
        "cohens_kappa": round(kappa(pairs), 4),
        "rule_counts": dict(Counter(rule for rule, _ in pairs)),
        "human_counts": dict(Counter(human for _, human in pairs)),
        "mismatch_pairs": [{"rule": rule, "human": human} for rule, human in mismatches[:20]],
        "note": "样本量很小时 kappa 不稳定，报告里要写明样本数与标注规则。",
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
