# PROGRESS — 阶段进度

## 2026-11-19

- G0-06：PASS。新增 `docs/PRODUCT_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/ACCEPTANCE.md`；README 与任务状态复核完成。
- Q6-02：PASS。项目文档索引和当前产品状态已更新。
- 本轮验证：`python -m unittest discover -s tests` → 108 tests OK（13.246 秒）；`python -m radar registry validate` → exit 0、108 条、0 errors、0 schema errors；候选来源未验证警告为预期提示。
- 当前 Git 实况：文档记录时为 `main`；公开发布快照的状态以实际仓库记录为准。
- 未改变：真实 DeepSeek 调用 NOT_RUN、95 个 candidate 待逐条核验、LICENSE/权属未定。
- B 站 BV 作品分析需求调研：新增 F7-01（BLOCKED）。开放平台官方资料描述的是审核开发者和授权关联账号数据；用户协议限制未经事先明确书面许可的自动化采集。任意 BV 评论/弹幕获取范围未确认；未探测接口、未抓取/下载媒体。详见 `docs/project/RESEARCH_BILIBILI_ANALYSIS.md`。
- F7-01 文档回归：108 tests OK（13.628 秒）；注册表 108 条且 0 errors/schema errors；`git diff --check` 通过。仅文档变更，没有 B 站网络请求或代码/schema 改动。

## 2026-11-16

- G0-04 旧库在线备份与独立恢复演练：PASS（quick_check=ok，9 张表条数一致，样本摘要一致）。
- G0-05 新项目初始化：独立 Git 仓库；历史远程可见性已调整为 PRIVATE。
- R1 数据层：12 张 `radar_*` 表、迁移、FTS5、去重、状态、运行记录、主题、观测值、导入器、只读桥接。
- C2 采集：SSRF 安全客户端、RSS/Atom、声明式 JSON、HN/GitHub/Open-Meteo 适配器；13 个真实来源 live PASS；连续两轮采集证据。
- U3 Web：六个页面 + JSON API + 本地安全（Host/CSRF/Origin/CSP/转义）。
- A4：统计、规则分析、CSV/JSON/Markdown 导出（公式注入防护）、离线课程案例。
- L5：AI 任务状态机（暂停/继续/取消/失败恢复/UNKNOWN_OUTCOME）、预算估算、假 provider；真实 DeepSeek NOT_RUN。
- Q6：108 项离线测试通过；API/操作/来源文档；证据归档。
- 待决：LICENSE/权属未定且无复用许可、无 DeepSeek Key、95 个候选源待逐条验证。

## 待办

1. 分批逐条验证 95 个候选来源，按证据单条更新状态。
2. 用户提供真实 Key 并明确确认后，执行最小 DeepSeek 冒烟测试和费用核对。
3. 补充长期调度/并发压力验收，使用隔离数据。
4. LICENSE/权属仍待确定；仓库公开可见不构成代码复用许可。
