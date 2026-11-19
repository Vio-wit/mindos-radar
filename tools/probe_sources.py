"""最小连通性探测：只取最小样本，不写入正文，不绕过任何限制。

用法: python tools/probe_sources.py [out.json]
仅用于 SRC-2 证据；结果写入 docs/evidence/，样本正文只留在 data/samples/（git 忽略）。
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "MindOS-Radar/0.1 (+local personal research tool)"

TARGETS = [
    ("hacker-news", "https://hacker-news.firebaseio.com/v0/topstories.json"),
    ("arxiv-cs-ai", "https://export.arxiv.org/rss/cs.AI"),
    ("openalex", "https://api.openalex.org/works?per-page=1"),
    ("gutendex", "https://gutendex.com/books?page=1"),
    ("open-meteo", "https://api.open-meteo.com/v1/forecast?latitude=31.30&longitude=120.62&hourly=temperature_2m&forecast_days=1&timezone=UTC"),
    ("github-releases", "https://api.github.com/repos/python/cpython/releases?per_page=1"),
    ("nvidia-newsroom", "https://nvidianews.nvidia.com/rss"),
    ("sspai", "https://sspai.com/feed"),
    ("guardian-world", "https://www.theguardian.com/world/rss"),
    ("gdelt", "https://api.gdeltproject.org/api/v2/doc/doc?query=NVIDIA&mode=artlist&maxrecords=1&format=json"),
    ("bangumi", "https://api.bgm.tv/v0/calendar"),
    ("crossref", "https://api.crossref.org/works?rows=1"),
    ("tvmaze", "https://api.tvmaze.com/shows/1"),
    ("worldbank", "https://api.worldbank.org/v2/country/CHN/indicator/NY.GDP.MKTP.CD?format=json&per_page=1"),
    ("wikidata", "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q182477&format=json"),
    ("pypi", "https://pypi.org/pypi/requests/json"),
    ("npm", "https://registry.npmjs.org/react/latest"),
    ("osv", "https://api.osv.dev/v1/vulns/GHSA-9c47-m6qq-7p4h"),
    ("nvd", "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=1"),
    ("openlibrary", "https://openlibrary.org/search.json?q=linux&limit=1"),
]


def probe(name: str, url: str) -> dict:
    started = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as resp:
            body = resp.read(200_000)
            sample_dir = Path(__file__).resolve().parent.parent / "data" / "samples"
            sample_dir.mkdir(parents=True, exist_ok=True)
            (sample_dir / f"{name}.sample").write_bytes(body[:20_000])
            return {
                "id": name,
                "url": url,
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "bytes_read": len(body),
                "elapsed_ms": int((time.time() - started) * 1000),
                "etag": bool(resp.headers.get("ETag")),
                "last_modified": bool(resp.headers.get("Last-Modified")),
            }
    except urllib.error.HTTPError as exc:
        return {"id": name, "url": url, "status": exc.code, "error": f"HTTP {exc.code}", "elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"id": name, "url": url, "status": 0, "error": f"{type(exc).__name__}: {exc}", "elapsed_ms": int((time.time() - started) * 1000)}


def main() -> int:
    extra = []
    for arg in sys.argv[1:]:
        if "=" in arg:
            name, url = arg.split("=", 1)
            extra.append((name, url))
    results = [probe(name, url) for name, url in (extra or TARGETS)]
    out = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "SRC-probe-2026-11-16.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in results:
        flag = "OK " if row.get("status") == 200 else "ERR"
        print(f"{flag} {row['id']:<18} status={row.get('status')} type={row.get('content_type','')[:28]} bytes={row.get('bytes_read',0)} err={row.get('error','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
