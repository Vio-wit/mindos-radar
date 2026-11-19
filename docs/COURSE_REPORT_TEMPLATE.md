# 课程报告模板：MindOS Radar 实践

> 建议 8–15 页；所有结论必须能追溯到命令、文件或证据路径。不得把演示数据写成真实接口结果。

## 1. 选题与目标

- 要解决的问题：
- 目标用户与使用场景：
- 明确不做的范围（参考需求非目标）：

## 2. 系统设计

- 数据流：采集 → 去重 → 入库 → 规则分析 → 统计/导出 →（可选）AI
- 表结构与关键约束（去重键、运行状态、分析缓存）：
- 只读桥接与隐私边界：

## 3. 实现与演示

- 运行环境（Python 版本、依赖、启动命令）：
- 页面截图（桌面 + 390×844 移动端）：
- CLI 演示（至少 3 条命令与输出摘要）：

## 4. 数据与来源

- 已接入来源数量、候选数量、失败来源及原因：
- 许可/配额检查记录：
- 两轮采集幂等证据：

## 5. 统计与规则分析

- 每日趋势、分类分布、来源贡献：
- 关键词方法（CJK 二元组/英文词）及局限：
- 数值序列：缺失不补零的说明。

## 6. 人工评估

- 样本量、标注规则、是否双人标注：
- 一致率与 Cohen's kappa（引用 `docs/evidence/A4-evaluation-demo.json` 或自己的结果）：
- 典型错误案例与改进方向：

## 7. AI 使用（如选做）

- 是否启用、模型与价格来源、预算：
- 输入脱敏与正文边界：
- 状态机：失败/未知/暂停/取消/重试策略：
- 成本与实际用量：

## 8. 安全与合规

- CSRF/Host/Origin/XSS/导出注入的处理：
- 秘密管理：
- 未绕过任何登录/验证码/付费墙/robots 的声明：

## 9. 局限与后续

- 未完成项（NOT_RUN/BLOCKED）：
- 若继续迭代的三件事：

## 10. 复现清单

```powershell
python -m radar init
python -m radar registry validate
python -m unittest discover -s tests
python examples/course_case/run_case.py
python examples/course_case/evaluate.py --annotations examples/course_case/demo_annotations.csv
```

附：测试输出、证据文件路径、提交哈希。
