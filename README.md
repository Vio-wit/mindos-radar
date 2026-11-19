# MindOS Radar

个人研究与技术情报聚合工具（v0.1，开发中）。

> **状态：R1/C2/U3/A4 主体已实现；L5 真实 DeepSeek 调用为 NOT_RUN，候选来源仍待逐条核验。本仓库为用户授权公开可见的代码快照；LICENSE/权属未定，公开可见不授予复制、修改或分发许可。阶段状态见 `TASKS.md` 与 `docs/project/`。**

## 定位

- 多源信息雷达：公开 API / RSS 采集 → 本地 SQLite → 规则统计 → 可选 AI 分析。
- 免费优先：仅接入官方、公开、免费、无需登录的接口；来源分级与核验状态见 `registry/`。
- 默认保守：限流、单源条目上限、AI 默认关闭；不绕过验证码、登录、付费墙、robots 或反爬。
- 与外部旧通知看板解耦：独立数据库 `data/radar.sqlite3`；仅在用户显式配置 `RADAR_NOTICE_DB` 后，通过 `mode=ro` 只读桥接访问，不修改旧库与 `notices` 表。

## 快速开始（Windows / 无 Docker）

```powershell
cd <repository-root>
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 初始化数据库并同步注册表
.\.venv\Scripts\python.exe -m radar init

# 启动本地 Web（默认 127.0.0.1:8770）
.\.venv\Scripts\python.exe -m radar serve

# 运行全部离线测试
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Web 只绑定 `127.0.0.1`，带 Host 白名单、CSRF Token、Origin/Referer 校验与严格 CSP；所有页面输出做 HTML 转义。

## 常用命令

```powershell
python -m radar registry validate        # 校验注册表（含 JSON Schema）
python -m radar registry list --status integrated
python -m radar source probe hacker-news # 只探测前 3 条，不落库
python -m radar sub add pypi-requests --enable --interval 720
python -m radar collect --due            # 采集到期订阅
python -m radar collect --all --force    # 强制全部启用订阅
python -m radar runs --limit 20          # 运行记录
python -m radar runs --cancel 3          # 取消仍 RUNNING 的记录
python -m radar topics add --name AI --include "AI,模型"
python -m radar analyze                  # 本地规则分析（不联网）
python -m radar stats                    # 统计 + 质量
python -m radar export --format csv --out .\exports\radar.csv
python -m radar import preview --file .\items.csv
python -m radar import commit --file .\items.csv --source local-import
python -m radar ai preview --items 1,2   # 只预估 token 与费用
python -m radar ai jobs --items 1,2      # 创建待执行任务
python -m radar ai run 1 --fake          # 本地假 provider，不发外部请求
python -m radar bridge stats             # 旧通知库只读统计
python -m radar backup                   # 备份雷达库（含真实数据，勿上传）
```

## 目录

```text
radar/              核心包（配置、数据库、采集、注册表、统计、导出）
radar/collectors/   RSS / 声明式 JSON 映射 / API 适配器
radar/ai/           可恢复 AI 任务、预算、provider、输出 schema
radar/web/          stdlib ThreadingHTTPServer 本地 Web 应用（无第三方框架）
registry/           来源注册表：source.schema.json + 已接入 + 候选目录
tests/              离线单元测试（不联网；AI 用 ScriptedProvider）
examples/           课程案例（离线、虚构数据）
tools/              手动探测脚本
docs/               证据、API、运维、来源说明
data/               运行期数据（Git 忽略）
```

## 数据与秘密

- 运行期数据只写 `data/`（可用 `RADAR_DATA_DIR` / `RADAR_DB_PATH` 覆盖），该目录永不提交。
- API Key 放 `data/secrets.json` 或环境变量（`RADAR_DEEPSEEK_API_KEY`、`RADAR_GITHUB_TOKEN`）；代码与接口只回显“是否已配置”。
- 旧通知库默认不配置；需要桥接时显式设置 `RADAR_NOTICE_DB`，桥接只读并拒绝任何写操作。
- 导出、备份、课程输出可能含第三方元数据或备注，默认不要提交到 Git 或发送给外部服务。

## 文档

- `docs/API.md`：Web 路由与 JSON API。
- `docs/PRODUCT_SPEC.md`：产品目标、边界与当前能力。
- `docs/ARCHITECTURE.md`：模块、数据流和安全边界。
- `docs/ACCEPTANCE.md`：验收矩阵、证据和未执行项。
- `docs/project/RESEARCH_BILIBILI_ANALYSIS.md`：B 站 BV 作品分析需求调研与授权边界。
- `docs/OPERATIONS.md`：安装、配置、采集、备份与故障处理。
- `docs/SOURCES.md`：来源分级、核验状态与新增来源流程。
- `docs/COURSE_REPORT_TEMPLATE.md`：课程报告模板。
- `examples/course_case/README.md`：离线教学案例。

## 许可

**未定**。LICENSE 待权属确认后再选。仓库公开可见仅用于代码审阅，不代表授予复制、修改、再发布或商业使用许可；请勿在许可确定前将其作为已开源项目使用。
