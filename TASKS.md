# TASKS.md — 唯一任务状态表

状态：`PASS` / `PARTIAL` / `TODO` / `NOT_RUN` / `BLOCKED`。最近更新：2026-11-19。

## G0 风险与冻结

| ID | 任务 | 状态 | 证据/说明 |
|---|---|---|---|
| G0-01 | 环境与路径审计 | PASS | 管理区 `docs/audit/ENVIRONMENT.md` |
| G0-02 | 基线功能/DB/路由审计 | PASS | 管理区 `docs/audit/BASELINE.md`；旧库 49/49 测试 |
| G0-03 | 开源/隐私/凭据审计 | PASS | 管理区 `docs/audit/OPEN_SOURCE_REVIEW.md`；秘密扫描 0 |
| G0-04 | 旧库在线备份 + 独立恢复演练 | PASS | 本地审计证据：quick_check=ok、条数/摘要一致；备份文件未随仓库发布 |
| G0-05 | 新项目选址与初始化 | PASS | 独立 Git 仓库 |
| G0-06 | 文档拆分与进度初始化 | PASS | `docs/PRODUCT_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/ACCEPTANCE.md` 已建立；README 与项目状态同步 |

## R1 数据层

| ID | 任务 | 状态 | 说明 |
|---|---|---|---|
| R1-01 | 独立 DB + 迁移 + FTS | PASS | 12 张 `radar_*` 表；WAL/外键；FTS5 自动回退 |
| R1-02 | 来源注册表 schema | PASS | `registry/schema/source.schema.json` + 手写校验器 |
| R1-03 | 注册表数据 | PASS | 108 条，0 校验错误；13 integrated / 95 candidate |
| R1-04 | 条目去重、状态、运行记录 | PASS | 内容变化标记分析 stale |
| R1-05 | 主题规则匹配 | PASS | 包含词/排除词/来源过滤 |
| R1-06 | CSV/JSON 导入器 | PASS | 预览/提交两段式；`local-import` |
| R1-07 | 旧库只读桥接 | PASS | `mode=ro`，写操作被拒绝 |

## C2 采集

| ID | 任务 | 状态 | 说明 |
|---|---|---|---|
| C2-01 | SSRF 安全 HTTP 客户端 | PASS | 私网/回环拦截、重定向逐跳校验、限速/限大小/重试 |
| C2-02 | RSS/Atom 适配器 | PASS | 条件请求 304、实体声明拒绝 |
| C2-03 | 声明式 JSON 映射 | PASS | 路径/列表/日期部件/URL 模板 |
| C2-04 | 专用适配器 | PASS | hackernews / github_releases / openmeteo |
| C2-05 | 真实来源 10–15 个 | PASS | 13 个 integrated，均有连通性证据 |
| C2-06 | 候选目录 100–150 | PARTIAL | 108 条（95 候选）；尚未逐条验证 |
| C2-07 | 连续两轮增量采集 | PASS | `docs/evidence/C2-two-round-collect-*.json` |
| C2-08 | 调度器 | PASS | 默认关闭，手动 `collect --due` |

## U3 / A4 / L5 / Q6

| ID | 任务 | 状态 | 说明 |
|---|---|---|---|
| U3-01 | 六个主页面 | PASS | 今日/信息流/主题/分析/来源/设置 |
| U3-02 | JSON API | PASS | `/api/radar/*` |
| U3-03 | 本地安全 | PASS | 仅 127.0.0.1、Host 白名单、CSRF、CSP、HTML 转义 |
| A4-01 | 统计/质量指标 | PASS | `radar/stats.py` |
| A4-02 | 规则分析 | PASS | `radar/analysis.py`（不联网） |
| A4-03 | CSV/JSON/Markdown 导出 | PASS | 公式注入防护 |
| A4-04 | 课程案例 | PASS | `examples/course_case/` 离线可复现 |
| L5-01 | AI 任务状态机 | PASS（假 provider） | 暂停/继续/取消、UNKNOWN_OUTCOME、失败恢复 |
| L5-02 | 预算与费用估算 | PASS（估算） | 每日请求/金额上限；价格可配置 |
| L5-03 | 真实 DeepSeek 调用 | NOT_RUN | 无 API Key；不得用样例冒充实测 |
| L5-04 | 无 Key 降级 | PASS | 页面与 API 正常，仅 AI 功能禁用 |
| Q6-01 | 离线回归测试 | PASS | 108 tests OK |
| Q6-02 | 文档 | PASS | 产品/架构/验收/API/运维/来源/课程文档齐备；README 已复核 |
| Q6-03 | 开源许可与权属决策 | BLOCKED | LICENSE/权属未定；仓库公开可见不构成代码复用许可，暂不授予复制、修改或分发权 |

## F7 后续功能需求

| ID | 需求 | 状态 | 证据/说明 |
|---|---|---|---|
| F7-01 | B 站 BV 链接影视/视频分析（授权优先） | BLOCKED | 先确认开放平台获批范围；任意 BV 自动抓取评论/弹幕未获书面许可前不开发。调研见 `docs/project/RESEARCH_BILIBILI_ANALYSIS.md` |

## 下一步（按顺序）

1. 按批次逐条核验 95 个候选源；保留失败证据，只升级确实通过的来源。
2. 真实 Key 可用且用户确认真实付费调用后，做一次最小 DeepSeek 冒烟测试并核对费用。
3. 设计并执行长期调度/并发压力验收，使用隔离数据环境。
4. 完成 LICENSE/权属决策后再确定可授予的复用权利；当前公开可见但无 LICENSE。
5. F7-01 先取得平台书面授权/接口范围答复；未获授权时只评估用户有权取得的数据手工导入方案。
