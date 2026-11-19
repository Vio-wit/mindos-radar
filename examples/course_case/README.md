# 课程案例：从 0 到 1 搭建本地信息雷达

本目录是一份**可复现、不联网、无真实隐私数据**的教学案例，用于 A4-05/A4-06。
所有示例条目均为虚构链接与示例文本。

## 1. 目标

- 用 6 条示例数据走通：初始化 → 导入 → 规则分析 → 统计 → 导出。
- 产出可人工标注的 `annotations.csv`，再用 `evaluate.py` 计算规则分类准确率与一致性。
- 全程离线；不会请求任何外部网站，不会写入 `data/` 以外的位置。

## 2. 运行

```powershell
cd <repository-root>
.\.venv\Scripts\python.exe examples\course_case\run_case.py --out .\examples\course_case\output
```

命令会：

1. 在 `output/work/` 建独立 SQLite（不碰正式库）；
2. 同步注册表并导入 `seed_data.csv`；
3. 对每条数据做规则分类/关键词/实体；
4. 生成 `output/report.json`、`output/export.csv`、`output/annotations.csv`。

## 3. 人工标注

打开 `output/annotations.csv`，填写 `human_category`（例如 ai / technology / campus / policy …），
每行 `agree` 列可按自己的判断写 `yes` / `no`。然后：

```powershell
.\.venv\Scripts\python.exe examples\course_case\evaluate.py --annotations output\annotations.csv
```

输出：样本数、可比较数、一致率、Cohen's kappa、每类计数。

## 4. 教学要点

- **只读桥接**：旧通知库只以 `mode=ro` 打开，任何写操作会被 SQLite 拒绝。
- **不把 Mock 当真实接口**：示例是本地 CSV；真实来源需完成 SRC 核验并保留证据。
- **缺失不补零**：统计里的观测序列只返回真实采集值。
- **导出会防公式注入**：CSV 中 `=`、`+`、`-`、`@` 开头的内容会加前缀 `'`。
- **AI 可选**：规则分析不需要 Key；AI 分析必须手动触发、可预览成本、默认不自动调用。

## 5. 文件

| 文件 | 用途 |
|---|---|
| `seed_data.csv` | 6 条虚构示例数据 |
| `run_case.py` | 一键离线案例脚本 |
| `evaluate.py` | 人工标注与规则结果一致性评估 |
| `annotation_template.csv` | 标注表头示例 |
