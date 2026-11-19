# TEST_REPORT — 测试记录

## 2026-11-19 公开发布快照回归

在隔离的公开发布副本中运行；导入路径确认指向该副本。

- `python -m unittest discover -s tests`：`Ran 109 tests in 13.753s` → `OK`（退出码 0）。
- `python -m radar registry validate`：退出码 0，count=108，errors=0，schema_errors=0；candidate 未验证 warnings 属于预期状态提示。
- 安全扫描：未发现常见 GitHub/AWS 凭据格式或私钥标记；本机路径复扫无命中。
- 本轮未运行真实 API 调用、候选来源联网验收或浏览器验收。

## 2026-11-19 F7-01 需求记录回归

本轮仅新增/更新需求文档，没有代码、数据库 schema 或来源状态变更；未连接 B 站、未执行 API 探测或抓取。

- `python -m unittest discover -s tests`：`Ran 108 tests in 13.628s` → `OK`（退出码 0）。
- `python -m radar registry validate`：退出码 0，count=108，errors=0，schema_errors=0；candidate 未验证 warnings 属于预期状态提示。
- `git diff --check`：通过。

## 2026-11-19 文档收尾回归

### 离线单元测试

命令：

```powershell
cd <repository-root>
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

结果：`Ran 108 tests in 13.246s` → `OK`（退出码 0）。

### 来源注册表校验

命令：

```powershell
.\.venv\Scripts\python.exe -m radar registry validate
```

结果：退出码 0，`ok=true`，count=108，手工校验 errors=0，schema_errors=0。输出包含 candidate 尚未通过连通验证的 warnings；这是状态提示，不是校验失败。

### 本轮未执行

- 真实 DeepSeek API 调用、候选来源联网逐条验收、长时间调度/并发压力测试。
- 本轮只修改文档；未执行 Web 浏览器验收或生产部署。

## 2026-11-16 全量离线回归

命令：

```powershell
cd <repository-root>
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

结果：`Ran 108 tests in 13.220s` → `OK`（退出码 0）

覆盖：

- `test_db_store.py`：schema/迁移幂等、去重、内容变化标记分析 stale、状态、运行恢复、FTS/LIKE 搜索、主题匹配、观测值 upsert
- `test_registry.py` / `test_registry_schema.py`：必填/枚举/秘密值拒绝、默认关闭、重复 id、导入不升级状态、JSON Schema 一致性
- `test_collectors.py`：RSS/Atom、实体声明拒绝、JSON 映射路径、HN/GitHub/Open-Meteo
- `test_http_engine.py`：SSRF（私网/回环/保留地址）、协议与内嵌凭据、采集成功/失败/304/未启用
- `test_ai_jobs.py`：暂停/继续/取消、失败恢复、UNKNOWN_OUTCOME、预算上限、缓存复用、无 Key 降级
- `test_analysis.py` / `test_export_stats.py`：规则分析、统计指标、导出公式注入防护
- `test_bridge.py`：旧库只读、写入被拒绝
- `test_importer.py` / `test_cli.py` / `test_web.py`：导入两段式、CLI 冒烟、页面与 API 安全（Host/CSRF/转义）

## 真实网络证据（非测试套件）

- `docs/evidence/C2-live-probe-20261116T092725Z.json`
- `docs/evidence/C2-two-round-collect-20261116T092934Z.json`
- 结论：13 个来源连通并完成两轮增量采集；未通过来源记录为 blocked，不伪造。

## NOT_RUN

- 真实 DeepSeek API 调用（无 Key）
- 大规模候选源（95 条）逐条验收
- 长时间调度稳定性 / 并发压力测试
