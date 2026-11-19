# MindOS Radar 架构说明（v0.1）

更新日期：2026-11-19

## 1. 部署视图

MindOS Radar 当前是单机 Python 应用，无 Docker、无外部数据库服务。CLI 和 Web 共用同一应用包及运行期数据目录。Web 使用 Python 标准库 `ThreadingHTTPServer`，默认只绑定 `127.0.0.1:8770`。

```text
浏览器 ──本机 HTTP──> radar.web（服务端渲染 + JSON API）
                           │
CLI ──────────────────────┤
                           ├── registry / collectors / engine
                           ├── store / analysis / stats / export / importer
                           ├── ai jobs / provider（显式执行）
                           └── SQLite：data/radar.sqlite3

旧通知 SQLite ──只读 bridge（独立连接）
```

## 2. 代码模块

| 模块 | 职责 |
|---|---|
| `radar/config.py` | 数据路径、非敏感设置和秘密读取；环境变量覆盖文件配置 |
| `radar/db.py` | SQLite 连接、schema 迁移、WAL/外键、FTS5 初始化与回退 |
| `radar/registry.py`、`schema_check.py` | YAML 注册表加载、校验、筛选和同步 |
| `radar/httpclient.py`、`radar/collectors/`、`engine.py` | 安全 HTTP、RSS/Atom、JSON 映射及专用适配器、采集状态与运行记录 |
| `radar/store.py` | 来源、订阅、条目、状态、主题和运行记录的持久化操作 |
| `radar/analysis.py`、`stats.py`、`export.py` | 本地规则分析、统计质量指标和导出 |
| `radar/importer.py` | 本地文件两阶段预览与提交 |
| `radar/ai/` | 预算估算、任务状态机、provider、PII 基础脱敏和输出 schema 校验 |
| `radar/bridge.py` | 对旧通知数据库的只读访问 |
| `radar/web/` | 本地页面、JSON API、渲染、静态资源和请求安全校验 |
| `radar/cli.py` | 初始化、注册表、采集、分析、导入导出、AI、备份和服务命令 |

## 3. 数据存储

默认数据库为 `data/radar.sqlite3`，可通过 `RADAR_DATA_DIR` 或 `RADAR_DB_PATH` 覆盖。当前 schema 版本为 1；连接时执行幂等迁移。启用 WAL、外键和 busy timeout。支持 FTS5 时建立全文索引与触发器；不可用时搜索回退到 LIKE。

主要表按职责分组：

- 来源与采集：`radar_sources`、`radar_subscriptions`、`radar_runs`。
- 内容与用户整理：`radar_items`、`radar_item_subscriptions`、`radar_item_state`、`radar_topics`、`radar_topic_matches`、`radar_observations`。
- AI：`radar_ai_jobs`、`radar_ai_job_items`、`radar_analyses`、`radar_ai_usage`。

来源定义由 `registry/curated/` 下的 YAML 管理；数据库保存运行所需的同步快照。注册表 schema 在 `registry/schema/source.schema.json`。`data/` 是运行期边界，数据库、设置、秘密及真实数据不应提交。

## 4. 采集数据流

1. 注册表加载器校验 YAML 条目及 schema；未知来源默认 `candidate` 且不默认启用。
2. 用户为已集成来源创建或启用订阅。
3. 采集引擎选择适配器，经 HTTP 客户端执行请求；客户端约束协议、目标地址、重定向、超时、响应大小、重试及主机间隔。
4. 解析后的条目按来源与外部 ID 去重，更新内容摘要/哈希、时间戳和订阅关联；保存成功/失败/未修改等运行状态。
5. 内容变化时相关分析可标记为 stale。进程中断遗留运行在恢复流程中转为未知状态，不冒充成功。

来源的 `integrated` 状态表示仓库所记录的适配器、样本、权利审核与连通证据达到项目门槛；它不保证第三方服务未来持续可用。

## 5. Web 与安全边界

- 页面由服务端渲染；API 位于 `/api/radar/`。
- 写请求校验 Host、Origin/Referer 和 CSRF token；请求体按 JSON 处理。
- 页面动态值做 HTML 转义；响应设置 CSP、`nosniff`、`no-store` 等安全头。
- 默认仅回环监听；当前应用不提供认证，不应放到公网或由反向代理暴露。
- 旧通知库由独立只读桥接连接打开；文件缺失时报告不可用，不创建旧库。

详细路由见 `docs/API.md`；启动和恢复流程见 `docs/OPERATIONS.md`。

## 6. AI 执行路径

AI 默认关闭。用户先预览选择项、截断后的输入长度、token/费用估算和预算，再显式创建任务并手动执行。执行器按条目保存状态、attempt 和用量估算；输出必须通过 JSON schema 检查。超时或无法确认是否处理的请求记作 `UNKNOWN_OUTCOME`，不自动重试。FakeProvider 是本地测试/演示实现，结果会标为 fake。

真实 DeepSeek 调用目前没有被执行；provider 存在不等于该路径已经通过真实服务验收。费用记录目前是基于配置价格的估算/对账状态，不应当作账单。

## 7. 外部依赖与运行要求

- Python 3.14（项目记录的验证环境）；第三方 Python 运行依赖为 PyYAML。
- SQLite 由 Python 标准库提供；FTS5 为可选能力。
- 采集需访问第三方公开服务；网络封锁、限流或服务变化会导致单源失败。
- 真实 AI 需要用户自行配置 DeepSeek Key，并承担调用费用；没有 Key 时非 AI 功能仍可运行。

## 8. 已知约束与演进边界

- 无用户认证、角色权限、团队隔离或远程访问控制。
- 调度默认关闭；任务的后台服务可靠性和长期并发承载尚未验收。
- 95 条 candidate 尚未逐条验证，禁止批量升级状态。
- 许可证和来源权利需要按正式政策进一步确认；公开发布保持冻结。
