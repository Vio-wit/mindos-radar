"""规则分析（A4）：分类、标签、实体、趋势与质量指标。

全部为可解释的本地规则，不调用任何模型，结果以 ``provider='rule'`` 写入
``radar_analyses``，与 AI 结果（method=llm）分开保存，互不覆盖。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import store

RULE_VERSION = "rules-v1"

CATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "人工智能", "llm", "大模型", "gpt", "transformer", "agent", "机器学习", "深度学习", "模型"),
    "technology": ("芯片", "gpu", "cpu", "云计算", "数据库", "编程", "开源", "框架", "系统", "软件", "硬件"),
    "opensource": ("开源", "github", "license", "仓库", "release", "贡献者", "maintainer"),
    "academic": ("论文", "arxiv", "研究", "学术", "引用", "期刊", "会议", "preprint", "dataset"),
    "policy": ("政策", "法规", "条例", "监管", "合规", "标准", "征求意见", "通知", "办法"),
    "jobs": ("招聘", "岗位", "实习", "校招", "社招", "薪酬", "面试"),
    "books": ("图书", "出版", "小说", "作者", "isbn", "阅读"),
    "anime": ("动画", "番剧", "漫画", "动画化", "声优", "bangumi"),
    "security": ("漏洞", "cve", "安全", "攻击", "补丁", "勒索", "钓鱼", "0day"),
    "finance": ("股价", "财报", "营收", "融资", "市值", "投资者", "财报"),
    "weather": ("天气", "气温", "降水", "风速", "预报", "气候"),
    "games": ("游戏", "主机", "steam", "playstation", "xbox", "switch"),
    "media": ("影视", "电影", "剧集", "票房", "导演", "演员"),
    "campus": ("校内", "学院", "教务处", "学生", "选课", "奖学金", "学号"),
    "science": ("物理", "化学", "生物", "天文", "材料", "能源", "量子"),
    "other": (),
}

ENTITY_HINTS: dict[str, tuple[str, str]] = {
    "nvidia": ("NVIDIA", "org"), "英伟达": ("NVIDIA", "org"),
    "jensen huang": ("Jensen Huang", "person"), "黄仁勋": ("黄仁勋", "person"),
    "openai": ("OpenAI", "org"), "anthropic": ("Anthropic", "org"), "google": ("Google", "org"),
    "microsoft": ("Microsoft", "org"), "apple": ("Apple", "org"), "meta": ("Meta", "org"),
    "amazon": ("Amazon", "org"), "amd": ("AMD", "org"), "intel": ("Intel", "org"),
    "tsmc": ("TSMC", "org"), "台积电": ("台积电", "org"), "deepseek": ("DeepSeek", "org"),
    "gpt-5": ("GPT-5", "product"), "gpt-4": ("GPT-4", "product"), "claude": ("Claude", "product"),
    "gemini": ("Gemini", "product"), "llama": ("Llama", "product"),
    "cuda": ("CUDA", "technology"), "transformer": ("Transformer", "technology"),
    "arxiv": ("arXiv", "org"), "github": ("GitHub", "org"), "hugging face": ("Hugging Face", "org"),
    "blackwell": ("Blackwell", "product"), "hopper": ("Hopper", "product"),
    "agent": ("AI Agent", "technology"), "机器人": ("机器人", "technology"),
    "humanoid": ("人形机器人", "technology"), "开源": ("开源", "other"),
}

CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "has", "have",
    "will", "into", "about", "after", "over", "their", "they", "you", "your", "our", "its",
    "his", "her", "not", "but", "can", "new", "how", "why", "what", "when", "who", "all",
}


def text_of(title: str, summary: str | None = None) -> str:
    return f"{title or ''} {summary or ''}".strip()


def classify(title: str, summary: str | None = None) -> tuple[str, float]:
    haystack = text_of(title, summary).lower()
    if not haystack.strip():
        return "other", 0.0
    scores: Counter[str] = Counter()
    for category, terms in CATEGORY_RULES.items():
        if not terms:
            continue
        for term in terms:
            if term.lower() in haystack:
                scores[category] += 1
    if not scores:
        return "other", 0.0
    category, hit = scores.most_common(1)[0]
    total = sum(scores.values()) or 1
    return category, round(min(0.95, 0.4 + 0.55 * hit / total), 3)


def keywords(title: str, summary: str | None = None, *, limit: int = 8) -> list[str]:
    text = text_of(title, summary)
    counter: Counter[str] = Counter()
    for word in WORD_RE.findall(text):
        lowered = word.lower()
        if lowered not in STOPWORDS:
            counter[lowered] += 1
    for block in CJK_RE.findall(text):
        for size in (2, 3):
            for index in range(max(0, len(block) - size + 1)):
                counter[block[index:index + size]] += 1
    return [term for term, _ in counter.most_common(limit)]


def entities(title: str, summary: str | None = None, *, limit: int = 8) -> list[dict[str, Any]]:
    text = text_of(title, summary)
    lowered = text.lower()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for needle, (name, kind) in ENTITY_HINTS.items():
        if needle in lowered and name not in seen:
            seen.add(name)
            found.append({"name": name, "type": kind, "evidence": needle})
            if len(found) >= limit:
                return found
    # 兜底：连续大写英文缩写（≥2 个字母）作为候选组织/产品
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", text):
        if token.lower() in STOPWORDS or token in seen:
            continue
        if token in {"AI", "GPU", "CPU", "API", "RSS", "JSON", "CSV", "HTML", "HTTP", "SQL"}:
            continue
        seen.add(token)
        found.append({"name": token, "type": "other", "evidence": token})
        if len(found) >= limit:
            break
    return found


def analyze_text(title: str, summary: str | None = None) -> dict[str, Any]:
    category, confidence = classify(title, summary)
    return {
        "method": "rule",
        "rule_version": RULE_VERSION,
        "summary": (summary or "")[:400] or title[:400],
        "primary_category": category,
        "confidence": confidence,
        "tags": keywords(title, summary, limit=6),
        "entities": entities(title, summary),
        "limitations": ["规则分类基于关键词，可能与人工判断不一致"],
    }


def store_rule_analysis(conn, item_id: int) -> dict[str, Any] | None:
    """把规则结果写入 radar_analyses（provider=rule），不覆盖 AI 结果。"""
    item = store.get_item(conn, item_id)
    if item is None:
        return None
    payload = analyze_text(item["title"], item.get("summary"))
    digest = item.get("content_hash") or ""
    with conn:
        conn.execute(
            "insert into radar_analyses(item_id, input_hash, task_type, provider, model, prompt_version, "
            "schema_version, output_json, quality_status, usage_json, generated_at, stale) "
            "values(?,?,?,?,?,?,?,?,?,?,?,0) "
            "on conflict(item_id, input_hash, task_type, provider, model, prompt_version, schema_version) "
            "do update set output_json=excluded.output_json, generated_at=excluded.generated_at, stale=0",
            (
                item_id, digest, "classify", "rule", RULE_VERSION, "v1", "v1",
                store.dumps(payload), "ok", store.dumps({"method": "rule"}), store.now_iso(),
            ),
        )
    return payload


def analyze_items(conn, *, limit: int | None = None, missing_only: bool = False) -> dict[str, int]:
    sql = "select i.id from radar_items i"
    if missing_only:
        sql += (
            " where not exists (select 1 from radar_analyses a where a.item_id=i.id and a.provider='rule' and a.stale=0)"
        )
    sql += " order by i.id"
    if limit:
        sql += f" limit {max(1, int(limit))}"
    ids = [row["id"] for row in conn.execute(sql)]
    for item_id in ids:
        store_rule_analysis(conn, item_id)
    return {"analyzed": len(ids)}


def trend(conn, *, days: int = 14) -> list[dict[str, Any]]:
    days = max(1, min(int(days), 90))
    out: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "select coalesce(r.category,'unknown') as category, count(*) as n from radar_items i "
            "left join radar_sources r on r.id=i.source_id "
            "where substr(coalesce(i.published_at, i.collected_at),1,10)=? group by 1 order by n desc",
            (day,),
        ).fetchall()
        out.append({"date": day, "total": sum(row["n"] for row in rows),
                    "by_category": {row["category"]: row["n"] for row in rows}})
    return out


def quality_metrics(conn) -> dict[str, Any]:
    total = conn.execute("select count(*) from radar_items").fetchone()[0]
    missing_date = conn.execute("select count(*) from radar_items where published_at is null").fetchone()[0]
    duplicate = conn.execute(
        "select count(*) from (select canonical_url from radar_items where canonical_url is not null "
        "group by canonical_url having count(*)>1)"
    ).fetchone()[0]
    unpublished = conn.execute(
        "select count(*) from radar_items where published_at is null"
    ).fetchone()[0]
    runs = conn.execute("select count(*) from radar_runs").fetchone()[0]
    failed = conn.execute("select count(*) from radar_runs where status='FAILED'").fetchone()[0]
    return {
        "items": total,
        "missing_published_at": missing_date,
        "missing_published_pct": round(missing_date * 100.0 / total, 2) if total else 0.0,
        "duplicate_canonical_urls": duplicate,
        "published_missing_pct": round(unpublished * 100.0 / total, 2) if total else 0.0,
        "runs": runs,
        "runs_failed": failed,
        "run_failure_pct": round(failed * 100.0 / runs, 2) if runs else 0.0,
        "collection_window_days": collection_window_days(conn),
    }


def collection_window_days(conn) -> int | None:
    row = conn.execute(
        "select min(coalesce(published_at, collected_at)) as first, max(coalesce(published_at, collected_at)) as last "
        "from radar_items"
    ).fetchone()
    if not row or not row["first"] or not row["last"]:
        return None
    try:
        first = datetime.fromisoformat(row["first"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(row["last"].replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (last - first).days)
