# 运维说明（v0.1）

## 1. 环境与安装

- Python 3.14；仓库自带 `.venv`（无 Docker）。
- 依赖仅 `PyYAML==6.0.3`，Web 用标准库 `ThreadingHTTPServer`。

```powershell
cd <repository-root>
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. 路径与环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `RADAR_DATA_DIR` | 运行期数据目录 | `./data` |
| `RADAR_DB_PATH` | 雷达数据库文件 | `<data>/radar.sqlite3` |
| `RADAR_NOTICE_DB` | 旧通知库（只读） | 未配置；需要时由用户显式设置 |
| `RADAR_DEEPSEEK_API_KEY` | DeepSeek Key（可选） | 空 |
| `RADAR_GITHUB_TOKEN` | GitHub Token（可选，提升配额） | 空 |

`data/` 永不提交，包含数据库、设置、秘密、样本。

## 3. 初始化与验证

```powershell
.\.venv\Scripts\python.exe -m radar init
.\.venv\Scripts\python.exe -m radar registry validate
.\.venv\Scripts\python.exe -m radar stats
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

`registry validate` 会同时执行手工规则与 `registry/schema/source.schema.json` 的最小 JSON Schema 校验。

## 4. 启动 Web

```powershell
.\.venv\Scripts\python.exe -m radar serve
# 指定端口
.\.venv\Scripts\python.exe -m radar serve --port 8771
```

- 仅绑定 `127.0.0.1`；不要用反向代理暴露到公网。
- 调度器默认关闭；即使 `--schedule`，也要求 `settings.schedule.enabled=true` 且不存在急停文件 `data/scheduler.stop`。
- 急停：在 `data/` 下创建 `scheduler.stop`，调度循环停止；删除后需人工重启。

## 5. 采集

```powershell
python -m radar sub list
python -m radar sub add pypi-requests --enable --interval 720
python -m radar collect --due
python -m radar collect --subscription 3 --force
python -m radar runs --limit 50
```

- 只对 `integrated` 来源开放一键采集；候选来源仅可查看与手动探测。
- 采集失败记录错误码与安全错误文本，不写入第三方正文以外的多余数据。
- 进程中断的运行在下次启动时标记为 `UNKNOWN`，绝不假装成功。

## 6. 备份与恢复

```powershell
python -m radar backup --out .\backups\radar
```

- 使用 SQLite 在线备份 API，输出 SHA256、`quick_check` 与表计数。
- **备份含真实数据，禁止提交到 Git 或上传。**
- 恢复演练：停止服务 → 将备份复制为 `data/radar.sqlite3` → 运行 `python -m radar init`（幂等迁移）→ 检查 `stats`。

## 7. 旧通知库桥接

```powershell
python -m radar bridge stats
python -m radar bridge list --q 通知 --limit 20
```

- 只读打开（`mode=ro` + `query_only` + authorizer），写操作会被 SQLite 拒绝。
- 默认不配置旧库路径；仅在有权访问该数据库时设置 `RADAR_NOTICE_DB`。
- 文件不存在时不会创建空库，直接返回不可用原因。

## 8. AI（可选）

```powershell
python -m radar ai preview --items 1,2
python -m radar ai jobs --items 1,2
python -m radar ai run 1 --fake      # 本地假 provider
python -m radar ai run 1 --yes       # 真实付费调用，必须显式确认
```

- 默认关闭；无 Key、超预算、未确认时拒绝真实调用。
- 超时/连接中断 → `UNKNOWN_OUTCOME`，默认不自动重试。
- 每天预算、请求次数、模型价格写入设置；Key 只存本地秘密文件。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| 启动报端口占用 | 换 `--port`；检查 8080/8765/8770 是否被其他程序占用 |
| `registry validate` 报错 | 按错误中的来源 id 修正 YAML；禁止为通过而删除校验 |
| 某来源一直 FAILED | 查看 `runs` 的 `error_code`/`safe_error`；网络受限来源保持 `candidate`，不要改状态 |
| 页面 403 | 刷新页面重新取 CSRF；确认不是用 `file://` 或外部 Origin 提交 |
| 页面 Host 400 | 用 `http://127.0.0.1:<port>` 访问，不要用任意域名 |
| AI 409 | 未启用、未配置 Key 或预算不足；先看 `/settings` 与 `/ai` 提示 |

## 10. 安全与合规

- 不提交 `data/`、备份、导出、样本、真实通知内容。
- 不绕过验证码、登录、付费墙、robots；不采集未授权来源。
- 公开可见快照不包含本地数据库或秘密；LICENSE/权属仍待决策，当前不授予代码复用许可。
