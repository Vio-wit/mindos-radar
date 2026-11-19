# 来源目录与核验（v0.1）

## 1. 分级

| 层 | 目标 | 本仓库现状 |
|---|---:|---|
| L0 候选索引 | 2000+（上游可合法导入时） | 未导入；仅保留 curated 候选 |
| L1 精选目录 | 100–150 | 108 条（2026-11-16） |
| L2 真实接入 | 10–15 | 13 条（其中 12 条公开接口 + 1 条本地导入） |

“L1 条数”不等于“已验证可用”。每条来源的 `verification.status` 才是运行期依据：

| 状态 | 含义 | 是否允许采集 |
|---|---|---|
| `candidate` | 仅有官方链接与初步费用/权限标注 | 否 |
| `docs_verified` | 官方文档已核对 | 否（需再做连通性） |
| `live_test_pass` | 单次连通探测通过 | 否（仍需样本与幂等验收） |
| `integrated` | 适配器、样本、许可审核、证据齐全 | 是 |
| `blocked` / `deprecated` / `rejected` | 不可用/停用/拒绝 | 否 |

## 2. 新增来源流程

1. 在 `registry/curated/candidates.yaml` 添加条目，字段参照 `registry/schema/source.schema.json`；缺失信息写 `null` 或 `未知`，不冒充已验证。
2. 运行 `python -m radar registry validate`，必须 0 error。
3. 核对官方文档与许可，把 `verification.docs_checked_at` 与 `docs_verified` 写入；证据放 `docs/evidence/`。
4. 用 `python -m radar source probe <id>` 做单次探测（不落库）；记录状态、内容类型、字节数。
5. 完成适配器与样本后，移入 `integrated.yaml`；填 `live_checked_at`、`evidence_ref`，状态改 `integrated`。
6. 运行离线测试 + 至少两轮真实采集，确认幂等（第二轮不重复写入）。

## 3. 当前真实接入（2026-11-16）

| 来源 | 类型 | 最近结论 | 证据 |
|---|---|---|---|
| arXiv cs.AI | RSS/Atom | PASS | `docs/evidence/C2-live-probe-*.json` |
| Crossref | REST JSON | PASS | 同上 |
| OpenAlex | REST JSON | PASS | 同上 |
| 少数派 | RSS | PASS（两轮幂等） | `docs/evidence/C2-two-round-collect-*.json` |
| GitHub Releases | REST JSON | PASS | `docs/evidence/C2-live-probe-*.json` |
| npm react | REST JSON | PASS | 同上 |
| PyPI requests | REST JSON | PASS | 同上 |
| NVD 最近 CVE | REST JSON | PASS（重试后） | 同上 |
| Open-Meteo 苏州 | 观测 JSON | PASS（重试后） | 同上 |
| Hacker News | REST JSON | PASS | 同上 |
| NVIDIA Developer Blog | RSS/Atom | PASS（两轮幂等） | `docs/evidence/C2-two-round-collect-*.json` |
| 本地导入 | manual | PASS（离线） | `examples/course_case/` |

失败/受限（保持候选或 blocked，不伪装成功）：

- Gutendex：本机网络超时（2026-11-16）。
- GDELT DOC：HTTP 429。
- Bangumi：本机网络不可达。

## 4. 许可与权限

- 所有条目都带 `access.auth`、`access.cost_tier`、`rights.*` 与 `policy_url`；未知项必须写 `unknown`/`review_required`。
- 不把第三方目录的“免费/可商用”标注直接当官方结论；以官方条款为准。
- 正文抓取默认 `review_required` 或 `prohibited_by_default`，仅元数据入库。

## 5. 回退

某个来源连续失败时：先看 `runs` 的错误码；确认网络或接口变更后，把状态改回 `candidate`/`blocked` 并保留证据，不删除历史条目。
