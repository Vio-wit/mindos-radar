"""离线课程案例：导入 → 规则分析 → 统计 → 导出（不访问网络）。

用法：
    python examples/course_case/run_case.py --out examples/course_case/output
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="MindOS Radar 离线课程案例")
    parser.add_argument("--out", default=str(Path(__file__).parent / "output"))
    parser.add_argument("--data", default=str(Path(__file__).parent / "seed_data.csv"))
    args = parser.parse_args()

    out_dir = Path(args.out)
    work_dir = out_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    os.environ["RADAR_DATA_DIR"] = str(work_dir)

    from radar import analysis, config, db, export, importer, registry, stats, store

    conn = db.connect()
    loaded = registry.load_registry()
    registry.sync_to_db(conn, loaded.sources, now=store.now_iso())
    if loaded.errors:
        print(json.dumps({"ok": False, "errors": loaded.errors[:5]}, ensure_ascii=False))
        return 1

    preview = importer.preview(args.data)
    committed = importer.commit(conn, "local-import", args.data, expected_snapshot=preview["snapshot"])
    analyzed = analysis.analyze_items(conn)

    report = {
        "ok": True,
        "imported": committed,
        "analyzed": analyzed,
        "totals": stats.overview(conn)["totals"],
        "quality": analysis.quality_metrics(conn),
        "trend": analysis.trend(conn, days=7),
        "keywords": stats.keyword_frequency(conn, limit=10),
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    export.write_export(conn, out_dir / "export.csv", fmt="csv")
    export.write_export(conn, out_dir / "export.json", fmt="json")

    with open(out_dir / "annotations.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "title", "rule_category", "human_category", "agree", "notes"])
        for item in store.list_items(conn, limit=100):
            latest = store.get_latest_analysis(conn, item["id"]) or {}
            category = (latest.get("output") or {}).get("primary_category", "")
            writer.writerow([item["id"], item["title"], category, "", "", ""])

    print(json.dumps({"ok": True, "out": str(out_dir), "items": report["totals"]["items"]}, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
