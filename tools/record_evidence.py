"""生成可复核的本地证据摘要（不联网、不写业务库）。

用法：
    python tools/record_evidence.py --label Q6
输出：
    docs/evidence/<label>-<UTC时间>.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="Q6")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    from radar import registry, schema_check

    result = registry.load_registry()
    schema = schema_check.load_schema()
    schema_errors = []
    for source_id, source in result.sources.items():
        for error in schema_check.validate_json_schema(source, schema):
            schema_errors.append(f"{source_id}: {error}")

    tests = {"skipped": True}
    if not args.skip_tests:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        tail = (completed.stderr or "").strip().splitlines()[-6:]
        tests = {
            "skipped": False,
            "returncode": completed.returncode,
            "summary": tail,
            "stderr_tail": (completed.stderr or "").strip().splitlines()[-80:],
            "ok": completed.returncode == 0,
        }

    course_files = [
        ROOT / "examples" / "course_case" / "seed_data.csv",
        ROOT / "examples" / "course_case" / "run_case.py",
        ROOT / "examples" / "course_case" / "evaluate.py",
        ROOT / "examples" / "course_case" / "demo_annotations.csv",
    ]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "registry": {
            "total": len(result.sources),
            "integrated": sum(1 for s in result.sources.values() if s["verification"]["status"] == "integrated"),
            "candidate": sum(1 for s in result.sources.values() if s["verification"]["status"] == "candidate"),
            "load_errors": result.errors,
            "schema_errors": schema_errors[:20],
        },
        "tests": tests,
        "course_case": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in course_files if path.is_file()
        ],
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = ROOT / "docs" / "evidence" / f"{args.label}-{stamp}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": tests.get("ok") if not tests.get("skipped") else True, "evidence": str(target)},
                     ensure_ascii=False))
    return 0 if (tests.get("skipped") or tests.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
