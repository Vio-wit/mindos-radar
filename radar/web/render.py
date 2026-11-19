"""服务端渲染：所有页面均为本地静态 HTML + 少量内联样式，不依赖 CDN。

安全：
* 所有动态文本一律 ``html.escape``；
* 表单带 CSRF 隐藏字段；
* 不渲染任何原始 HTML 内容（正文只作为转义文本展示）。
"""
from __future__ import annotations

import html
from typing import Any, Iterable

NAV = [
    ("/", "今日"),
    ("/feed", "信息流"),
    ("/sources", "数据源"),
    ("/subscriptions", "订阅"),
    ("/runs", "运行"),
    ("/topics", "主题"),
    ("/stats", "统计"),
    ("/ai", "AI"),
    ("/import", "导入"),
    ("/notices", "通知"),
    ("/settings", "设置"),
]

STATUS_LABELS = {
    "integrated": "已接入",
    "live_test_pass": "实测通过",
    "docs_verified": "文档已核",
    "candidate": "候选",
    "blocked": "受阻",
    "deprecated": "已弃用",
    "rejected": "已拒绝",
    "SUCCEEDED": "成功",
    "FAILED": "失败",
    "NOT_MODIFIED": "未变化",
    "RUNNING": "运行中",
    "UNKNOWN": "未知",
    "CANCELLED": "已取消",
    "COMPLETED": "完成",
    "PARTIAL": "部分完成",
    "PAUSED": "已暂停",
    "SKIPPED": "跳过",
    "PENDING": "待处理",
    "CACHE_HIT": "缓存命中",
    "FAILED_RETRYABLE": "可重试失败",
    "FAILED_FINAL": "最终失败",
    "UNKNOWN_OUTCOME": "结果未知",
    "IN_FLIGHT": "请求中",
}

COST_LABELS = {"F0": "F0 免费", "F1": "F1 免费额度", "F2": "F2 低费", "F3": "F3 付费", "F4": "F4 高价", "FU": "费用未知"}


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def num(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def label(value: Any) -> str:
    text = str(value or "")
    return h(STATUS_LABELS.get(text, text or "未知"))


def day(value: Any) -> str:
    text = str(value or "")
    return h(text[:10] if len(text) >= 10 else "未知")


def csrf_field(token: str) -> str:
    return f'<input type="hidden" name="_csrf" value="{h(token)}">'


def layout(title: str, body: str, *, active: str = "/", csrf: str = "", notice: str = "") -> str:
    links = "".join(
        f'<a class="nav-link{" active" if path == active else ""}" href="{path}">{h(name)}</a>'
        for path, name in NAV
    )
    banner = f'<div class="notice" role="status">{h(notice)}</div>' if notice else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)} · MindOS Radar</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body data-csrf="{h(csrf)}">
<header class="topbar">
  <div class="brand"><a href="/">MindOS <span>Radar</span></a></div>
  <nav class="nav">{links}</nav>
</header>
<main class="wrap">
{banner}
{body}
</main>
<footer class="foot">
  <span>本地运行 · 仅绑定 127.0.0.1 · 数据在 data/radar.sqlite3</span>
  <span>v0.1 · G0 冻结中</span>
</footer>
<script src="/static/app.js"></script>
</body>
</html>"""


def card(title: str, value: Any, hint: str = "") -> str:
    hint_html = f'<div class="card-hint">{h(hint)}</div>' if hint else ""
    return f'<div class="card"><div class="card-title">{h(title)}</div><div class="card-value">{h(value)}</div>{hint_html}</div>'


def cards(items: Iterable[tuple[str, Any, str]]) -> str:
    return '<section class="cards">' + "".join(card(*item) for item in items) + "</section>"


def empty(text: str, hint: str = "") -> str:
    extra = f'<p class="muted">{h(hint)}</p>' if hint else ""
    return f'<div class="empty"><strong>{h(text)}</strong>{extra}</div>'


def item_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return empty("还没有内容", "先在「数据源」启用订阅并运行一次采集，或用「导入」加载本地合法样本。")
    rows = []
    for item in items:
        unread = not item.get("read_at")
        star = "★" if item.get("favorite") else ""
        rows.append(
            '<tr class="item-row">'
            f'<td class="dot">{"●" if unread else ""}</td>'
            f'<td class="fav">{h(star)}</td>'
            f'<td><a class="item-title" href="/feed/{int(item["id"])}">{h(item.get("title"))}</a>'
            f'<div class="muted small">{h(item.get("source_name") or item.get("source_id"))} · {day(item.get("published_at") or item.get("collected_at"))}</div></td>'
            f'<td class="summary muted">{h((item.get("summary") or "")[:140])}</td>'
            "</tr>"
        )
    return '<table class="table items"><tbody>' + "".join(rows) + "</tbody></table>"


def pager(path: str, page: int, has_next: bool, query: str = "") -> str:
    base = path + ("?" + query + "&" if query else "?")
    prev_link = f'<a class="btn" href="{h(base)}page={page - 1}">上一页</a>' if page > 1 else '<span class="btn disabled">上一页</span>'
    next_link = f'<a class="btn" href="{h(base)}page={page + 1}">下一页</a>' if has_next else '<span class="btn disabled">下一页</span>'
    return f'<div class="pager">{prev_link}<span class="muted">第 {page} 页</span>{next_link}</div>'


def dashboard(data: dict[str, Any], *, csrf: str, notice: str = "") -> str:
    totals = data["overview"]["totals"]
    health = data.get("health") or []
    events = data.get("events") or []
    today_items = data.get("today_items") or []
    body = "<h1>今日</h1>"
    body += cards(
        [
            ("今日新增", num(totals.get("items_today")), "按发布时间或采集时间的 UTC 日期"),
            ("近 7 天", num(totals.get("items_week")), ""),
            ("未读", num(totals.get("unread")), ""),
            ("收藏", num(totals.get("favorites")), ""),
            ("信息源", num(totals.get("sources")), f'已接入 {num(totals.get("sources_integrated"))}'),
            ("启用订阅", num(totals.get("subscriptions_enabled")), f'共 {num(totals.get("subscriptions"))}'),
        ]
    )
    body += '<div class="grid-2">'
    body += '<section class="panel"><div class="panel-head"><h2>今日新内容</h2><a class="btn" href="/feed?unread=1">看未读</a></div>'
    body += item_rows(today_items)
    body += "</section>"
    body += '<section class="panel"><div class="panel-head"><h2>源状态</h2><a class="btn" href="/sources">数据源</a></div>'
    if health:
        rows = "".join(
            f'<tr><td><a href="/feed?source={h(row["id"])}">{h(row["name"])}</a></td>'
            f'<td>{num(row["items"])}</td><td>{label(row["verification_status"])}</td>'
            f'<td>{label(row["last_run_status"])}</td></tr>'
            for row in health[:12]
        )
        body += f'<table class="table compact"><thead><tr><th>来源</th><th>条目</th><th>状态</th><th>最近运行</th></tr></thead><tbody>{rows}</tbody></table>'
    else:
        body += empty("尚未同步来源注册表", "运行 `python -m radar init` 或下方按钮。")
    body += '<form method="post" action="/init">' + csrf_field(csrf) + '<button class="btn primary" type="submit">同步注册表</button></form>'
    body += "</section></div>"
    body += '<section class="panel"><div class="panel-head"><h2>赛事 / 截止</h2><a class="btn" href="/notices">旧通知库</a></div>'
    if events:
        rows = "".join(
            f'<tr><td>{h(event.get("title"))}</td><td>{day(event.get("school_signup_deadline"))}</td>'
            f'<td>{day(event.get("work_submission_deadline"))}</td><td>{h(event.get("status"))}</td></tr>'
            for event in events
        )
        body += f'<table class="table compact"><thead><tr><th>赛事</th><th>校内报名</th><th>作品提交</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>'
    else:
        body += empty("没有可读的赛事数据", "旧通知库不可用时这里保持空状态，不显示假数据。")
    body += "</section>"
    return layout("今日", body, active="/", csrf=csrf, notice=notice)


def sources_page(sources: list[dict[str, Any]], filters: dict[str, Any], categories: list[str],
                 *, csrf: str, notice: str = "") -> str:
    body = "<h1>数据源</h1>"
    body += (
        '<form class="filters" method="get" action="/sources">'
        f'<input name="q" value="{h(filters.get("q"))}" placeholder="搜索 id / 名称 / 标签">'
        '<select name="category"><option value="">全部分类</option>'
        + "".join(f'<option value="{h(c)}"{" selected" if filters.get("category") == c else ""}>{h(c)}</option>' for c in categories)
        + "</select>"
        '<select name="status"><option value="">全部状态</option>'
        + "".join(
            f'<option value="{key}"{" selected" if filters.get("status") == key else ""}>{h(text)}</option>'
            for key, text in STATUS_LABELS.items() if key in {"integrated", "live_test_pass", "docs_verified", "candidate", "blocked"}
        )
        + "</select>"
        '<select name="cost"><option value="">全部费用</option>'
        + "".join(
            f'<option value="{key}"{" selected" if filters.get("cost") == key else ""}>{h(text)}</option>'
            for key, text in COST_LABELS.items()
        )
        + "</select>"
        '<button class="btn primary" type="submit">筛选</button></form>'
    )
    body += f'<p class="muted">共 {len(sources)} 个来源。候选源默认关闭，未通过审核的源不会发起请求。</p>'
    if not sources:
        body += empty("没有匹配的来源", "可以放宽筛选条件，或检查 registry/curated/*.yaml。")
        return layout("数据源", body, active="/sources", csrf=csrf, notice=notice)
    rows = []
    for source in sources:
        status = (source.get("verification") or {}).get("status")
        cost = (source.get("access") or {}).get("cost_tier") or "FU"
        adapter = (source.get("collector") or {}).get("adapter")
        allowed_probe = status in {"integrated", "live_test_pass"}
        probe = (
            f'<form method="post" action="/sources/{h(source["id"])}/probe" class="inline">{csrf_field(csrf)}'
            '<button class="btn small" type="submit">探测</button></form>'
            if allowed_probe
            else '<span class="muted small">需先审核</span>'
        )
        subscribe = (
            f'<form method="post" action="/subscriptions" class="inline">{csrf_field(csrf)}'
            f'<input type="hidden" name="source_id" value="{h(source["id"])}">'
            '<input type="hidden" name="enabled" value="0">'
            '<button class="btn small" type="submit">加订阅</button></form>'
        )
        rows.append(
            f'<tr><td><strong>{h(source.get("name"))}</strong><div class="muted small">{h(source["id"])}</div></td>'
            f'<td>{h(source.get("category"))}</td><td>{h(COST_LABELS.get(cost, cost))}</td>'
            f'<td>{label(status)}</td><td>{h(adapter)}</td>'
            f'<td class="actions">{probe}{subscribe}</td></tr>'
        )
    body += (
        '<table class="table"><thead><tr><th>来源</th><th>分类</th><th>费用</th><th>验证</th><th>适配器</th><th>操作</th></tr></thead>'
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )
    return layout("数据源", body, active="/sources", csrf=csrf, notice=notice)


def feed_page(items: list[dict[str, Any]], *, filters: dict[str, Any], page: int, has_next: bool,
              sources: list[dict[str, Any]], csrf: str, notice: str = "") -> str:
    body = "<h1>信息流</h1>"
    query_parts = []
    for key in ("q", "source", "unread", "favorites", "order", "topic"):
        if filters.get(key):
            query_parts.append(f"{key}={h(filters[key])}")
    body += (
        '<form class="filters" method="get" action="/feed">'
        f'<input name="q" value="{h(filters.get("q"))}" placeholder="关键词（FTS / LIKE）">'
        '<select name="source"><option value="">全部来源</option>'
        + "".join(
            f'<option value="{h(s["id"])}"{" selected" if filters.get("source") == s["id"] else ""}>{h(s["name"])}</option>'
            for s in sources
        )
        + "</select>"
        f'<label class="check"><input type="checkbox" name="unread" value="1"{" checked" if filters.get("unread") else ""}> 仅未读</label>'
        f'<label class="check"><input type="checkbox" name="favorites" value="1"{" checked" if filters.get("favorites") else ""}> 仅收藏</label>'
        '<select name="order"><option value="published"' + (' selected' if filters.get("order") != "collected" else "") + ">按发布</option>"
        '<option value="collected"' + (' selected' if filters.get("order") == "collected" else "") + ">按采集</option></select>"
        '<button class="btn primary" type="submit">查询</button>'
        '<a class="btn" href="/export?format=csv&' + "&".join(query_parts) + '">导出 CSV</a>'
        "</form>"
    )
    body += item_rows(items)
    body += pager("/feed", page, has_next, "&".join(query_parts))
    return layout("信息流", body, active="/feed", csrf=csrf, notice=notice)


def item_page(item: dict[str, Any], *, csrf: str, notice: str = "") -> str:
    analysis = item.get("analysis") or {}
    output = analysis.get("output") or {}
    body = f'<h1>{h(item.get("title"))}</h1>'
    body += (
        '<div class="meta">'
        f'<span>{h(item.get("source_name") or item.get("source_id"))}</span>'
        f'<span>{day(item.get("published_at") or item.get("collected_at"))}</span>'
        f'<span>{"已读" if item.get("read_at") else "未读"}</span>'
        f'<span>{"收藏" if item.get("favorite") else "未收藏"}</span>'
        f'<span>权利：{h(item.get("rights_state"))}</span>'
        "</div>"
    )
    if item.get("canonical_url"):
        body += f'<p><a class="btn" href="{h(item["canonical_url"])}" target="_blank" rel="noopener noreferrer">打开原文</a></p>'
    if item.get("summary"):
        body += f'<section class="panel"><h2>摘要</h2><p class="pre">{h(item["summary"])}</p></section>'
    if item.get("body"):
        body += f'<section class="panel"><h2>正文（本地保存）</h2><p class="pre">{h(item["body"][:6000])}</p></section>'
    body += '<section class="panel"><h2>规则 / AI 分析</h2>'
    if output:
        body += f'<p><strong>方法：</strong>{h(output.get("method", "rule"))} · <strong>分类：</strong>{h(output.get("primary_category") or "")}</p>'
        if output.get("summary"):
            body += f'<p class="pre">{h(output["summary"])}</p>'
        if output.get("tags"):
            body += '<p>' + " ".join(f'<span class="chip">{h(tag)}</span>' for tag in output["tags"]) + "</p>"
        if output.get("entities"):
            body += "<ul>" + "".join(
                f'<li>{h(entity.get("name"))} · {h(entity.get("type"))}</li>' for entity in output["entities"][:10]
            ) + "</ul>"
    else:
        body += empty("还没有分析结果", "可在下方运行本地规则分析；AI 分析需要先配置 Key。")
        body += f'<form method="post" action="/feed/{int(item["id"])}/analyze">{csrf_field(csrf)}<button class="btn" type="submit">运行规则分析</button></form>'
    body += "</section>"
    body += (
        f'<section class="panel"><h2>我的状态</h2><form method="post" action="/feed/{int(item["id"])}/state">'
        + csrf_field(csrf)
        + f'<label class="check"><input type="checkbox" name="read" value="1"{" checked" if item.get("read_at") else ""}> 已读</label>'
        + f'<label class="check"><input type="checkbox" name="favorite" value="1"{" checked" if item.get("favorite") else ""}> 收藏</label>'
        + f'<textarea name="note" rows="3" placeholder="备注（本地保存）">{h(item.get("note") or "")}</textarea>'
        + '<button class="btn primary" type="submit">保存</button></form></section>'
    )
    body += (
        f'<section class="panel"><h2>AI 分析</h2><form method="post" action="/ai/preview">'
        + csrf_field(csrf)
        + f'<input type="hidden" name="item_ids" value="{int(item["id"])}">'
        + '<button class="btn" type="submit">预览（不调用 AI）</button></form>'
        + '<p class="muted small">预览只做本地估算；没有 Key 时 AI 按钮不会发起任何外部请求。</p></section>'
    )
    return layout("条目", body, active="/feed", csrf=csrf, notice=notice)


def subscriptions_page(subs: list[dict[str, Any]], sources: list[dict[str, Any]], *, csrf: str, notice: str = "") -> str:
    body = "<h1>订阅</h1>"
    body += '<section class="panel"><h2>新增订阅</h2><form method="post" action="/subscriptions" class="filters">'
    body += csrf_field(csrf)
    body += '<select name="source_id">' + "".join(
        f'<option value="{h(s["id"])}">{h(s["name"])}（{h((s.get("verification") or {}).get("status"))}）</option>'
        for s in sources
    ) + "</select>"
    body += '<input name="interval" type="number" min="15" value="360" title="采集间隔（分钟）">'
    body += '<label class="check"><input type="checkbox" name="enabled" value="1"> 立即启用</label>'
    body += '<button class="btn primary" type="submit">创建</button></form>'
    body += '<p class="muted small">候选源创建后默认不启用；启用后才会被采集入口取用。</p></section>'
    if not subs:
        body += empty("还没有订阅", "从上方选择一个来源创建订阅。")
        return layout("订阅", body, active="/subscriptions", csrf=csrf, notice=notice)
    rows = []
    for sub in subs:
        toggle = "暂停" if sub["enabled"] else "启用"
        rows.append(
            f'<tr><td>{h(sub.get("source_name") or sub["source_id"])}<div class="muted small">{h(sub["source_id"])}</div></td>'
            f'<td>{"启用" if sub["enabled"] else "停用"}</td>'
            f'<td>{int(sub["interval_minutes"])} 分钟</td>'
            f'<td>{h((sub.get("last_success_at") or "—")[:19])}</td>'
            f'<td class="muted small">{h((sub.get("last_error") or "")[:80])}</td>'
            f'<td class="actions">'
            f'<form method="post" action="/subscriptions/{int(sub["id"])}/toggle" class="inline">{csrf_field(csrf)}'
            f'<button class="btn small" type="submit">{toggle}</button></form>'
            f'<form method="post" action="/subscriptions/{int(sub["id"])}/collect" class="inline">{csrf_field(csrf)}'
            f'<button class="btn small" type="submit">采集</button></form>'
            f'<form method="post" action="/subscriptions/{int(sub["id"])}/delete" class="inline" data-confirm="确认删除该订阅？">{csrf_field(csrf)}'
            f'<button class="btn small danger" type="submit">删除</button></form>'
            "</td></tr>"
        )
    body += (
        '<table class="table"><thead><tr><th>来源</th><th>状态</th><th>间隔</th><th>最近成功</th><th>最近错误</th><th>操作</th></tr></thead>'
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )
    return layout("订阅", body, active="/subscriptions", csrf=csrf, notice=notice)


def runs_page(runs: list[dict[str, Any]], *, csrf: str, notice: str = "") -> str:
    body = "<h1>采集运行</h1>"
    body += (
        '<section class="panel"><h2>手动触发</h2>'
        f'<form method="post" action="/collect/due" class="inline">{csrf_field(csrf)}'
        '<button class="btn primary" type="submit">采集到期订阅</button></form> '
        '<form method="post" action="/collect/all" class="inline" data-confirm="将立刻采集所有已启用订阅，确认？">'
        + csrf_field(csrf)
        + '<button class="btn" type="submit">采集全部已启用</button></form>'
        '<p class="muted small">默认只处理启用且到期的订阅；采集失败会记录安全错误，不影响其它来源。</p></section>'
    )
    if not runs:
        body += empty("还没有运行记录", "触发一次采集后会在这里出现。")
        return layout("运行", body, active="/runs", csrf=csrf, notice=notice)
    rows = "".join(
        f'<tr><td>{int(run["id"])}</td><td>{h(run.get("source_id"))}</td><td>{label(run.get("status"))}</td>'
        f'<td>{day(run.get("started_at"))}</td><td><code>{h(str(run.get("counts") or {}))}</code></td>'
        f'<td class="muted small">{h((run.get("safe_error") or "")[:100])}</td>'
        + (
            f'<td><form method="post" action="/runs/{int(run["id"])}/cancel" class="inline">{csrf_field(csrf)}'
            '<button class="btn small danger" type="submit">取消</button></form></td>'
            if run.get("status") == "RUNNING" else "<td></td>"
        )
        + "</tr>"
        for run in runs
    )
    body += (
        '<table class="table"><thead><tr><th>ID</th><th>来源</th><th>状态</th><th>开始</th><th>计数</th><th>错误</th><th>操作</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return layout("运行", body, active="/runs", csrf=csrf, notice=notice)


def topics_page(topics: list[dict[str, Any]], sources: list[dict[str, Any]], *, csrf: str, notice: str = "") -> str:
    body = "<h1>主题追踪</h1>"
    body += '<section class="panel"><h2>新增主题</h2><form method="post" action="/topics" class="filters">'
    body += csrf_field(csrf)
    body += '<input name="name" placeholder="主题名，如 NVIDIA / Jensen Huang" required>'
    body += '<input name="include" placeholder="包含词，逗号分隔" required>'
    body += '<input name="exclude" placeholder="排除词（可选）">'
    body += '<input name="source_filter" placeholder="限定来源 id，逗号分隔（可选）">'
    body += '<button class="btn primary" type="submit">创建</button></form>'
    body += '<p class="muted small">匹配只针对本地已采集条目，不做全网搜索；规则可追溯。</p></section>'
    if not topics:
        body += empty("还没有主题", "先用 NVIDIA / 黄仁勋 等词建立一个样例。")
        return layout("主题", body, active="/topics", csrf=csrf, notice=notice)
    rows = []
    for topic in topics:
        rows.append(
            f'<tr><td><strong>{h(topic["name"])}</strong><div class="muted small">包含：{h(", ".join(topic["include_terms"]))}'
            f'{"；排除：" + h(", ".join(topic["exclude_terms"])) if topic["exclude_terms"] else ""}'
            f'{"；来源：" + h(", ".join(topic["source_filter"])) if topic["source_filter"] else ""}</div></td>'
            f'<td>{num(topic["match_count"])}</td>'
            f'<td>{"启用" if topic["enabled"] else "停用"}</td>'
            f'<td class="actions"><a class="btn small" href="/feed?topic={int(topic["id"])}">查看匹配</a>'
            f'<form method="post" action="/topics/{int(topic["id"])}/eval" class="inline">{csrf_field(csrf)}'
            '<button class="btn small" type="submit">重新匹配</button></form>'
            f'<form method="post" action="/topics/{int(topic["id"])}/delete" class="inline" data-confirm="删除该主题？">{csrf_field(csrf)}'
            '<button class="btn small danger" type="submit">删除</button></form></td></tr>'
        )
    body += (
        '<table class="table"><thead><tr><th>主题</th><th>匹配</th><th>状态</th><th>操作</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return layout("主题", body, active="/topics", csrf=csrf, notice=notice)


def _bars(rows: list[dict[str, Any]], *, value_key: str = "items") -> str:
    peak = max([int(row.get(value_key) or 0) for row in rows] + [1])
    parts = []
    for row in rows:
        value = int(row.get(value_key) or 0)
        height = max(2, round(value * 100 / peak))
        parts.append(
            f'<div class="bar" title="{h(row.get("date"))} · {value}"><span style="height:{height}%"></span>'
            f'<em>{h(str(row.get("date"))[5:])}</em></div>'
        )
    return '<div class="chart">' + "".join(parts) + "</div>"


def stats_page(data: dict[str, Any], *, csrf: str, notice: str = "") -> str:
    overview = data["overview"]
    totals = overview["totals"]
    body = "<h1>统计</h1>"
    body += cards(
        [
            ("条目总数", num(totals.get("items")), ""),
            ("缺失发布时间", num(overview["quality"].get("missing_published_at")), f'{overview["quality"].get("items_without_published_pct")}%'),
            ("重复链接", num(overview["quality"].get("duplicate_canonical_urls")), "按规范化 URL 统计"),
            ("运行失败率", f'{data["quality"].get("run_failure_pct")}%', f'{num(data["quality"].get("runs_failed"))} / {num(data["quality"].get("runs"))}'),
            ("采集窗口", f'{data["quality"].get("collection_window_days")} 天' if data["quality"].get("collection_window_days") is not None else "无数据", "按已入库条目的最早/最晚时间"),
        ]
    )
    body += '<section class="panel"><h2>每日新增（14 天）</h2>' + _bars(overview["daily"]) + "</section>"
    body += '<div class="grid-2"><section class="panel"><h2>来源分布</h2>'
    if overview["top_sources"]:
        body += '<table class="table compact"><thead><tr><th>来源</th><th>条目</th></tr></thead><tbody>' + "".join(
            f'<tr><td>{h(row["name"])}</td><td>{num(row["items"])}</td></tr>' for row in overview["top_sources"]
        ) + "</tbody></table>"
    else:
        body += empty("暂无数据")
    body += '</section><section class="panel"><h2>分类分布</h2>'
    if overview["by_category"]:
        body += "".join(
            f'<div class="row"><span>{h(row["category"])}</span><strong>{num(row["items"])}</strong></div>'
            for row in overview["by_category"]
        )
    else:
        body += empty("暂无数据")
    body += "</section></div>"
    body += '<section class="panel"><h2>关键词（14 天）</h2>'
    keywords = data.get("keywords") or []
    body += ("".join(f'<span class="chip">{h(item["term"])} · {num(item["count"])}</span>' for item in keywords)
             if keywords else empty("暂无关键词", "采集到中英文文本后自动统计。"))
    body += "</section>"
    body += '<section class="panel"><h2>导出</h2><p class="muted">导出只包含元数据与你自己的备注，默认排除无权全文。</p>'
    body += '<a class="btn" href="/export?format=json">JSON</a> <a class="btn" href="/export?format=csv">CSV</a> <a class="btn" href="/export?format=md">Markdown</a></section>'
    return layout("统计", body, active="/stats", csrf=csrf, notice=notice)


def settings_page(settings: dict[str, Any], *, secret_status: dict[str, bool], paths: dict[str, Any],
                  bridge_stats: dict[str, Any] | None, csrf: str, notice: str = "") -> str:
    collector = settings.get("collector") or {}
    ai = settings.get("ai") or {}
    schedule = settings.get("schedule") or {}
    body = "<h1>设置</h1>"
    body += '<section class="panel"><h2>界面与采集</h2><form method="post" action="/settings" class="form-grid">'
    body += csrf_field(csrf)
    body += f'<label>站点标题<input name="site_title" value="{h(settings.get("site_title"))}"></label>'
    body += f'<label>采集超时（秒）<input name="timeout_seconds" type="number" min="3" max="120" value="{h(collector.get("timeout_seconds", 20))}"></label>'
    body += f'<label>单次最大字节<input name="max_bytes" type="number" min="65536" value="{h(collector.get("max_bytes", 4194304))}"></label>'
    body += f'<label>单源条目上限<input name="max_items_per_run" type="number" min="1" max="200" value="{h(collector.get("max_items_per_run", 50))}"></label>'
    body += f'<label>同主机最小间隔（秒）<input name="host_min_interval_seconds" type="number" min="0" step="0.1" value="{h(collector.get("host_min_interval_seconds", 1.0))}"></label>'
    body += f'<label>重试次数<input name="retry_count" type="number" min="0" max="5" value="{h(collector.get("retry_count", 2))}"></label>'
    body += '<label class="check"><input type="checkbox" name="schedule_enabled" value="1"' + (" checked" if schedule.get("enabled") else "") + "> 启用本地调度（默认关闭）</label>"
    body += f'<label>调度间隔（分钟）<input name="schedule_interval" type="number" min="30" value="{h(schedule.get("interval_minutes", 360))}"></label>'
    body += '<button class="btn primary" type="submit">保存</button></form>'
    body += '<p class="muted small">调度需要服务以 <code>--schedule</code> 启动且未放置 scheduler.stop 急停文件。</p></section>'
    body += '<section class="panel"><h2>AI（非秘密配置）</h2><form method="post" action="/settings/ai" class="form-grid">'
    body += csrf_field(csrf)
    body += '<label class="check"><input type="checkbox" name="ai_enabled" value="1"' + (" checked" if ai.get("enabled") else "") + "> 允许 AI 任务（默认关闭）</label>"
    body += f'<label>模型<input name="model" value="{h(ai.get("model"))}"></label>'
    body += f'<label>Base URL<input name="base_url" value="{h(ai.get("base_url"))}"></label>'
    body += f'<label>单任务条目上限<input name="max_items_per_job" type="number" min="1" max="50" value="{h(ai.get("max_items_per_job", 10))}"></label>'
    body += f'<label>每日请求上限<input name="daily_request_limit" type="number" min="0" value="{h(ai.get("daily_request_limit", 50))}"></label>'
    body += f'<label>每日预算（CNY）<input name="daily_budget_cny" value="{h(ai.get("daily_budget_cny", "1.00"))}"></label>'
    body += '<button class="btn primary" type="submit">保存</button></form>'
    body += '<p class="muted small">价格是本地估算依据，不保证与账单一致；无 Key 时不会发起任何请求。</p></section>'
    body += '<section class="panel"><h2>密钥（只写不读）</h2>'
    body += f'<p>DeepSeek Key：<strong>{"已配置" if secret_status.get("deepseek") else "未配置"}</strong> · GitHub Token：<strong>{"已配置" if secret_status.get("github") else "未配置"}</strong></p>'
    body += '<form method="post" action="/settings/secret" class="filters">' + csrf_field(csrf)
    body += '<select name="name"><option value="deepseek_api_key">DeepSeek API Key</option><option value="github_token">GitHub Token</option></select>'
    body += '<input name="value" type="password" placeholder="粘贴后保存；接口与页面永不回显" autocomplete="off">'
    body += '<button class="btn primary" type="submit">保存</button>'
    body += '<button class="btn danger" type="submit" name="action" value="clear">清除</button></form>'
    body += '<p class="muted small">保存在 data/secrets.json（已 gitignore，权限 600 尽力而为）。</p></section>'
    body += '<section class="panel"><h2>路径与状态</h2><table class="table compact"><tbody>'
    for key, value in paths.items():
        body += f'<tr><th>{h(key)}</th><td><code>{h(value)}</code></td></tr>'
    body += "</tbody></table><p class=\"muted small\">旧通知库只读；雷达数据独立在 data/radar.sqlite3。</p></section>"
    if bridge_stats:
        body += '<section class="panel"><h2>旧通知库（只读）</h2><table class="table compact"><tbody>'
        for key, value in bridge_stats.items():
            body += f'<tr><th>{h(key)}</th><td>{h(value)}</td></tr>'
        body += "</tbody></table></section>"
    body += '<section class="panel"><h2>备份</h2><p class="muted">备份请在命令行执行，避免 Web 进程持有大文件：</p>'
    body += '<pre class="code">python -m radar backup --out .\\backups\\radar</pre>'
    body += '<p class="muted small">备份文件含真实数据，不要提交到 Git 或上传。</p></section>'
    return layout("设置", body, active="/settings", csrf=csrf, notice=notice)


def ai_page(jobs: list[dict[str, Any]], preview: dict[str, Any] | None,
            *, ai_enabled: bool, key_configured: bool, csrf: str, notice: str = "") -> str:
    body = "<h1>AI 分析</h1>"
    body += f'<p class="muted">状态：AI {"已启用" if ai_enabled else "未启用"} · Key {"已配置" if key_configured else "未配置"}。没有 Key 时预览仍可用，但不会发起任何请求。</p>'
    body += '<section class="panel"><h2>预览（不调用 AI）</h2><form method="post" action="/ai/preview" class="filters">'
    body += csrf_field(csrf)
    body += '<input name="item_ids" placeholder="条目 ID，逗号分隔，如 1,2,3" required>'
    body += '<select name="task_type"><option value="brief">摘要简报</option><option value="classify">分类</option><option value="entities">实体</option></select>'
    body += '<button class="btn" type="submit">预览</button></form></section>'
    if preview:
        body += '<section class="panel"><h2>预览结果</h2>'
        body += f'<p>条目 {preview["count"]} 条 · 估计输入 {num(preview["estimated_prompt_tokens"])} tokens · 估计费用 {h(preview["estimated_cost_cny"])} CNY</p>'
        body += '<p class="muted small">估算基于本地价格配置，可能与实际账单不同；缓存命中的条目不重复计费。</p>'
        body += '<table class="table compact"><thead><tr><th>ID</th><th>标题</th><th>输入字符</th><th>缓存</th></tr></thead><tbody>'
        for row in preview["items"]:
            body += (f'<tr><td>{h(row.get("item_id"))}</td><td>{h(row.get("title") or "（缺失）")}</td>'
                     f'<td>{h(row.get("input_chars", "—"))}</td><td>{"是" if row.get("cached") else "否"}</td></tr>')
        body += "</tbody></table>"
        if preview["count"]:
            body += ('<form method="post" action="/ai/jobs" class="inline">' + csrf_field(csrf)
                     + f'<input type="hidden" name="item_ids" value="{h(",".join(str(row["item_id"]) for row in preview["items"] if row.get("item_id")))}">'
                     + '<input type="hidden" name="task_type" value="' + h(preview["task_type"]) + '">'
                     + '<button class="btn primary" type="submit" data-confirm="确认创建 AI 任务？">确认创建任务</button></form>')
        body += "</section>"
    body += '<section class="panel"><h2>任务</h2>'
    if not jobs:
        body += empty("还没有 AI 任务", "先用上面的预览确认条目和预算。")
    else:
        rows = []
        for job in jobs:
            counts = job.get("counts") or {}
            actions = (
                f'<form method="post" action="/ai/jobs/{int(job["id"])}/run" class="inline">{csrf_field(csrf)}'
                '<button class="btn small" type="submit">运行</button></form>'
                f'<form method="post" action="/ai/jobs/{int(job["id"])}/pause" class="inline">{csrf_field(csrf)}'
                '<button class="btn small" type="submit">暂停</button></form>'
                f'<form method="post" action="/ai/jobs/{int(job["id"])}/resume" class="inline">{csrf_field(csrf)}'
                '<button class="btn small" type="submit">恢复</button></form>'
                f'<form method="post" action="/ai/jobs/{int(job["id"])}/cancel" class="inline" data-confirm="取消未执行条目？">{csrf_field(csrf)}'
                '<button class="btn small danger" type="submit">取消</button></form>'
            )
            rows.append(
                f'<tr><td>{int(job["id"])}</td><td>{h(job["provider"])} / {h(job["model"])}</td>'
                f'<td>{h(job["task_type"])}</td><td>{label(job["status"])}</td>'
                f'<td><code>{h(str(counts))}</code></td><td class="actions">{actions}</td></tr>'
            )
        body += ('<table class="table"><thead><tr><th>ID</th><th>Provider</th><th>任务</th><th>状态</th><th>条目</th><th>操作</th></tr></thead>'
                 "<tbody>" + "".join(rows) + "</tbody></table>")
    body += '<p class="muted small">真实 DeepSeek 调用标记为 NOT_RUN，除非你配置 Key 并明确执行；本页“运行”在无 Key 时会失败并保持任务可恢复。</p></section>'
    return layout("AI", body, active="/ai", csrf=csrf, notice=notice)


def import_page(preview_data: dict[str, Any] | None, *, sources: list[dict[str, Any]] | None = None,
                csrf: str, notice: str = "") -> str:
    body = "<h1>本地导入</h1>"
    body += '<section class="panel"><h2>预览</h2><form method="post" action="/import/preview" class="filters">'
    body += csrf_field(csrf)
    body += '<input name="path" placeholder="本地文件路径，如 .\\items.csv" required>'
    body += '<select name="source_id">'
    body += '<option value="">（预览不需要来源）</option>'
    body += "".join(f'<option value="{h(s["id"])}">{h(s["name"])}</option>' for s in (sources or []))
    body += '</select>'
    body += '<button class="btn" type="submit">预览</button></form>'
    body += '<p class="muted small">支持 CSV / TSV / JSON / JSONL；文件只在本地读取，不联网。</p></section>'
    if preview_data:
        body += '<section class="panel"><h2>预览结果</h2>'
        body += f'<p>{h(preview_data["format"])} · {num(preview_data["rows"])} 行 · 可导入 {num(preview_data["mapped_rows"])} 条 · 文件内重复 {num(preview_data["duplicate_external_ids_in_file"])}</p>'
        if preview_data["errors"]:
            body += "<ul class=\"errors\">" + "".join(f"<li>{h(error)}</li>" for error in preview_data["errors"]) + "</ul>"
        if preview_data["sample"]:
            body += '<table class="table compact"><thead><tr><th>external_id</th><th>标题</th><th>发布时间</th></tr></thead><tbody>'
            body += "".join(
                f'<tr><td>{h(row["external_id"])}</td><td>{h(row["title"])}</td><td>{day(row["published_at"])}</td></tr>'
                for row in preview_data["sample"][:10]
            )
            body += "</tbody></table>"
        body += f'<form method="post" action="/import/commit" class="filters">{csrf_field(csrf)}'
        body += f'<input type="hidden" name="path" value="{h(preview_data["path"])}">'
        body += f'<input type="hidden" name="snapshot" value="{h(preview_data["snapshot"])}">'
        body += '<input name="source_id" placeholder="目标来源 id，如 local-import" required>'
        body += '<button class="btn primary" type="submit" data-confirm="确认写入数据库？">确认导入</button></form>'
        body += "</section>"
    return layout("导入", body, active="/import", csrf=csrf, notice=notice)


def notices_page(items: list[dict[str, Any]], stats_data: dict[str, Any] | None, *, csrf: str, notice: str = "") -> str:
    body = "<h1>旧通知库（只读）</h1>"
    if stats_data is None:
        body += empty("旧通知库不可用", "设置 RADAR_NOTICE_DB 指向 notices.sqlite3；桥接失败不会建库或写库。")
        return layout("旧通知", body, active="/notices", csrf=csrf, notice=notice)
    body += '<section class="panel"><h2>状态</h2><table class="table compact"><tbody>'
    for key, value in stats_data.items():
        body += f'<tr><th>{h(key)}</th><td>{h(value)}</td></tr>'
    body += "</tbody></table><p class=\"muted small\">只读连接（mode=ro + query_only + authorizer），不会改动旧库。</p></section>"
    if items:
        rows = "".join(
            f'<tr><td><a href="/notices/{h(item["notice_key"])}">{h(item["title"])}</a>'
            f'<div class="muted small">{h(item.get("department") or "")}</div></td>'
            f'<td>{day(item.get("published_at"))}</td><td>{"未读" if item.get("is_unread") else "已读"}</td>'
            f'<td>{h((item.get("brief_summary") or "")[:80])}</td></tr>'
            for item in items
        )
        body += '<table class="table"><thead><tr><th>标题</th><th>发布</th><th>状态</th><th>摘要</th></tr></thead><tbody>' + rows + "</tbody></table>"
    else:
        body += empty("没有通知数据")
    return layout("旧通知", body, active="/notices", csrf=csrf, notice=notice)


def notice_page(item: dict[str, Any], *, csrf: str, notice: str = "") -> str:
    body = f'<h1>{h(item.get("title"))}</h1>'
    body += (f'<div class="meta"><span>{h(item.get("department") or "")}</span>'
             f'<span>{day(item.get("published_at"))}</span><span>{h(item.get("notice_type"))}</span></div>')
    if item.get("url"):
        body += f'<p><a class="btn" href="{h(item["url"])}" target="_blank" rel="noopener noreferrer">打开原文</a></p>'
    if item.get("brief_summary"):
        body += f'<section class="panel"><h2>既有摘要</h2><p class="pre">{h(item["brief_summary"])}</p></section>'
    body += f'<section class="panel"><h2>正文（只读）</h2><p class="pre">{h((item.get("content") or "")[:8000])}</p></section>'
    body += '<p class="muted small">该页面只读取旧库；修改仍请在原通知看板进行。</p>'
    return layout("通知详情", body, active="/notices", csrf=csrf, notice=notice)


def error_page(code: int, message: str, *, csrf: str = "") -> str:
    body = f'<h1>{code}</h1>' + empty(message, "返回上一页或回到今日首页。")
    body += '<p><a class="btn" href="/">回到今日</a></p>'
    return layout(f"错误 {code}", body, active="/", csrf=csrf)
