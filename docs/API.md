# Web 路由与 JSON API（v0.1）

服务默认 `http://127.0.0.1:8770`，只监听本机回环地址。所有写操作需要 CSRF Token；跨站 Origin/Referer 会被拒绝；Host 只接受 `127.0.0.1` / `localhost` / `[::1]`。

## 页面（GET，服务端渲染）

| 路径 | 内容 |
|---|---|
| `/` | 仪表盘：总量、今日、未读、来源健康、趋势 |
| `/feed` | 信息流，支持 `q`、`source`、`unread`、`favorites`、`order`、`topic`、`page` |
| `/feed/<id>` | 条目详情、规则/AI 分析、备注、收藏、已读 |
| `/sources` | 来源目录，支持 `q`、`category`、`status`、`cost` |
| `/subscriptions` | 订阅列表、新增/启停/删除 |
| `/runs` | 运行历史与取消按钮 |
| `/topics` | 主题（包含词/排除词/来源过滤）与匹配数 |
| `/stats` | 统计、质量指标、最近 AI 用量 |
| `/ai` | AI 成本预估、任务创建与状态、假运行 |
| `/import` | 本地 CSV/JSON/JSONL 预览与提交 |
| `/settings` | 站点、采集、AI、旧库桥接、备份说明 |
| `/notices` | 旧通知库只读浏览（不可用时显示原因） |
| `/export?format=csv\|json\|md` | 导出下载 |
| `/healthz` | 健康检查 JSON |

## JSON API

全部位于 `/api/radar/`。写请求（POST/PATCH/DELETE）需请求头 `X-CSRF-Token`（页面内 `<body data-csrf>` 提供）且 `Content-Type: application/json`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sources?q=&category=&status=&cost=&limit=&offset=` | 来源列表（分页） |
| GET | `/sources/<id>` | 来源详情（注册表结构） |
| GET | `/subscriptions` | 订阅列表 |
| POST | `/subscriptions` | 新增：`{source_id, interval_minutes?, enabled?}` |
| PATCH | `/subscriptions/<id>` | 修改：`{enabled?, interval_minutes?}` |
| DELETE | `/subscriptions/<id>` | 删除 |
| POST | `/subscriptions/<id>/collect` | 立即采集（来源须为 `integrated`） |
| GET | `/runs?limit=` | 运行记录 |
| POST | `/runs/<id>/cancel` | 取消仍处于 RUNNING 的运行 |
| GET | `/items?q=&source=&unread=&favorites=&order=&topic=&limit=&offset=` | 条目列表（`has_next`） |
| GET | `/items/<id>` | 条目详情（含最新分析） |
| PATCH | `/items/<id>/state` | `{read?, favorite?, note?}` |
| POST | `/items/<id>/analyze` | 规则分析（本地） |
| GET | `/topics` | 主题列表 |
| POST | `/topics` | `{name, include_terms, exclude_terms?, source_filter?}` |
| GET | `/topics/<id>/matches` | 主题匹配条目 |
| GET | `/stats` | 概览、质量、用量 |
| GET | `/settings/ai` | AI 设置（不返回 Key） |
| POST | `/settings/ai` | 修改 AI/采集/站点设置 |
| POST | `/sources/<id>/probe` | 连通性探测；非 integrated 来源返回 403 |
| POST | `/ai/preview` | 成本预估：`{item_ids, task_type}` |
| POST | `/ai/jobs` | 创建任务：`{item_ids, task_type, include_cached?}` |
| GET | `/ai/jobs` | 任务列表 |
| GET | `/ai/jobs/<id>` | 任务详情与逐条状态 |
| POST | `/ai/jobs/<id>/run` | 执行；`{fake:true}` 走本地假 provider |
| POST | `/ai/jobs/<id>/pause\|resume\|cancel\|retry` | 状态控制 |

## 错误约定

- `400`：参数非法（含整数解析失败）。
- `403`：CSRF/Origin/Host 校验失败，或来源未完成真实验证。
- `404`：资源不存在。
- `409`：AI 未启用、未配置 Key 或预算/确认条件不满足。
- `429`：触发本地限流。
- `500`：未预期异常（返回错误页，不泄露堆栈与秘密）。

## 安全基线

- 绑定 `127.0.0.1`，不做公网部署。
- `Content-Security-Policy: default-src 'self'; ...`；无内联脚本，静态 JS/CSS 同源加载。
- 所有动态文本经 `html.escape`；JSON 响应设置 `nosniff` 与 `no-store`。
- CSRF Token 进程级随机生成，Cookie 仅 `HttpOnly; SameSite=Strict`。
- 秘密只存 `data/secrets.json`，接口仅返回“是否已配置”。
