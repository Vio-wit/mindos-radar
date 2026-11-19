# AGENTS.md — 接力开发硬约束

本仓库是 MindOS Radar v0.1 的唯一实现目录。任何模型接手前，先读完本文件、`TASKS.md`、`docs/project/HANDOFF.md`。

## 1. 不可违反的边界

1. 外部旧通知看板如需桥接，必须由用户通过 `RADAR_NOTICE_DB` 显式指定；不修改、不迁移、不写入旧库；桥接仅允许 `mode=ro`。
2. 新项目数据库独立：`data/radar.sqlite3`（或 `RADAR_DB_PATH`）。
3. 禁止把私人通知、日程、原始附件、API Key、`data/` 内容提交到 Git 或发送给外部模型。
4. 禁止 Git 历史重写和强制推送。仓库可见性及远程操作须有用户明确授权；本发布快照的公开可见性已获授权，但这不构成代码复用许可。
5. 禁止绕过登录、付费墙、robots、反爬或访问控制；无法合法获取的内容只保留元数据与原始链接。
6. AI 必须手动触发；禁止后台自动批量调用付费 API；无 Key 时系统必须可运行。
7. 未经验证的来源不得标记为 `integrated` / `live_test_pass`。

## 2. 已验证事实（2026-11-16）

- Python 3.14.2 / SQLite 3.50.4；虚拟环境 `.venv`；依赖仅 PyYAML。
- 测试：`108 tests OK`（离线，约 13 秒）。
- 注册表：108 个来源，13 个 `integrated`，95 个 `candidate`，校验 0 错误。
- 已接入：hacker-news、arxiv-cs-ai、openalex-works、gutendex-popular、open-meteo-suzhou、github-releases、sspai、crossref-works、nvd-recent、pypi-requests、npm-react、nvidia-devblog、local-import。
- 本网络不可达/受限：Guardian RSS、Bangumi API、GDELT(429)、NVIDIA newsroom RSS(返回 HTML)；已记录为 blocked，勿反复重试。
- 旧库备份与恢复演练：PASS；备份文件及其校验值仅保留在本地审计记录，不随公开仓库发布。
- 真实 DeepSeek 调用：**NOT_RUN**（无 Key，不得伪造结果）。

## 3. 命令

```powershell
cd <repository-root>
.\.venv\Scripts\python.exe -m unittest discover -s tests        # 全量离线测试
.\.venv\Scripts\python.exe -m radar init
.\.venv\Scripts\python.exe -m radar serve                        # 127.0.0.1:8770
.\.venv\Scripts\python.exe -m radar registry validate
.\.venv\Scripts\python.exe -m radar stats
```

## 4. 每个逻辑任务完成时必须做

1. 更新 `TASKS.md` 状态；2. 更新 `docs/project/PROGRESS.md`；3. 跑测试并把结果写入 `docs/project/TEST_REPORT.md`；4. 更新 `docs/project/HANDOFF.md`；5. `git status` 检查后按用户授权范围提交或推送。
