# MindOS Radar 验收基线（v0.1）

更新日期：2026-11-19

本文区分代码/离线验收、真实外部服务证据和未执行项目。通过离线测试不代表公网部署、长期稳定性、第三方服务持续可用或法律许可已经验收。

## 1. 总体结论

- 主体实现：R1、C2、U3、A4 已完成；L5 状态机/预算以假 provider 完成离线覆盖。
- 最新离线结果：公开发布快照中 `109 tests OK`；详细命令与时间见 `docs/project/TEST_REPORT.md`。
- 来源注册表：交接基线为 108 条、13 integrated、95 candidate、0 校验错误；若本轮校验结果不同，以本轮实测输出为准。
- 真实 DeepSeek：`NOT_RUN`（当前未提供 Key/未发起真实调用）。
- 许可决策：`BLOCKED`（仓库可公开查看；LICENSE/权属未定，不授予复用许可）。

## 2. 验收矩阵

| 范围 | 验收条件 | 基线状态 | 证据/限制 |
|---|---|---|---|
| 独立数据层 | 初始化、迁移幂等、去重、状态、运行恢复、搜索可用；不写旧库 | PASS（离线覆盖） | `tests/test_db_store.py`、`tests/test_bridge.py`；当前实际结果见测试报告 |
| 注册表 | 手工规则和 JSON Schema 校验通过；未验证来源保持 candidate/关闭 | PASS（基线） | `registry validate`；本轮输出记录在本次验收结果 |
| 采集安全 | 拒绝危险目标/重定向，限制响应并执行节流；失败有明确状态 | PASS（离线覆盖） | `tests/test_http_engine.py`；不能据此证明目标网络当前可达 |
| 真实来源 | integrated 有连通和样本证据；第二轮采集验证幂等 | PASS（13 个基线来源） | `docs/evidence/` 历史证据；不代表持续可用 |
| Web/API | 页面与 API 功能可用；Host、CSRF、转义等边界受测 | PASS（离线覆盖） | `tests/test_web.py`；不等于公网安全审计 |
| 分析与导出 | 规则分析、统计、导出可运行；CSV 公式注入被防护 | PASS（离线覆盖） | `tests/test_analysis.py`、`tests/test_export_stats.py` |
| AI 状态机 | 假 provider 下状态转换、预算、失败恢复、UNKNOWN_OUTCOME 和无 Key 降级正确 | PASS（假 provider） | `tests/test_ai_jobs.py`；假 provider 不证明真实 API 互通 |
| 真实 AI | 使用真实 Key 完成一次小样本调用、记录请求/用量/结果并核对费用 | NOT_RUN | 未发生真实 API 请求；不可标为通过 |
| 候选来源 | 逐条核对官方文档、访问条件、权利、连通性、样本与幂等性 | PARTIAL | 95 个 candidate 待逐条验证；不得批量改成 integrated |
| 长期/并发运行 | 覆盖长时间调度、恢复、压力和资源边界 | NOT_RUN | 目前没有此类验收证据 |
| 复用许可 | 权属和 LICENSE 决策完成，敏感/个人数据扫描通过 | BLOCKED | LICENSE 未定；仓库虽公开可见，尚未授予复用许可 |

## 3. 本轮可复现检查

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m radar registry validate
```

只有实际执行后，才能在 `docs/project/TEST_REPORT.md` 追加本次时间、命令、退出码、结果和失败信息。历史证据需保留其原日期，不可改写为本轮实测。

## 4. 验收边界

- Mock、FakeProvider、虚构课程数据和测试替身只能支持离线逻辑验收。
- 单次 probe 不等于适配器集成验收；需另有真实采集样本和增量幂等证据。
- `integrated` 是项目注册表状态，不是对第三方 SLA、内容完整性或法律许可的保证。
- 当前只有本机单用户安全设计；没有身份认证或公网部署验收。
- 费用估算不等于供应商账单；真实调用费用需由用户账户侧核对。
- 任何测试都不得使用旧通知库写连接、提交 `data/`、伪造真实 AI 结果或改变许可证状态。

## 5. 未完成项目与关闭条件

1. **真实 DeepSeek 冒烟测试**：用户配置 Key 后，显式确认真实付费调用；选 1 条无敏感测试条目，核对任务状态、结构化输出、用量与账单；证据脱敏后记录。未执行前保持 `NOT_RUN`。
2. **候选源验证**：分批人工核对官方资料并留证；每条独立决定 candidate、blocked 或 integrated，禁止批量升级。
3. **长期调度/压力**：定义资源和时长目标，使用隔离数据环境测试，记录边界与失败。
4. **许可与发布**：代码快照公开可见已获用户授权；LICENSE/权属决策仍待完成，公开可见不授予复用许可。
