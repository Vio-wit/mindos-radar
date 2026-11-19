# CHANGELOG

## [0.1.0-wip] - 2026-11-16

### Added
- 独立 SQLite 数据层（12 张 `radar_*` 表、迁移、FTS5 回退、WAL/外键）。
- 来源注册表：JSON Schema + 校验器 + 108 条来源（13 integrated / 95 candidate）。
- 采集引擎：RSS/Atom、声明式 JSON、Hacker News、GitHub Releases、Open-Meteo；SSRF 防护、限速、条件请求、重试与安全错误日志。
- 旧通知库只读桥接（`mode=ro`）。
- Web 六页面 + JSON API，本地 Host 白名单、CSRF、CSP、HTML 转义。
- 统计、规则分析、CSV/JSON/Markdown 导出（公式注入防护）。
- AI 任务状态机、预算估算、假 provider、输出 schema；真实 DeepSeek 未接入。
- CSV/JSON 导入器（预览 + 提交）。
- 课程案例（离线、可复现）。
- 108 项离线测试；API/操作/来源/接力文档。

### Security
- 原通知项目远程仓库转为 PRIVATE；旧库完成备份与恢复演练。
- 注册表禁止秘密值；密钥仅存 `data/secrets.json` 或环境变量。

### Known limitations
- LICENSE 未定；仓库公开可见不构成复用许可。
- 真实 AI 调用未验证。
- 95 个候选源未逐条验证。
