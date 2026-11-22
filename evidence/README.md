# 实验汇总数据

- `Baseline_E14_epochs.csv` 包含 Baseline 的 15 轮指标。冻结 E14 取第 14 轮 checkpoint。
- `AB_epochs.csv` 包含 A、B 各 4 轮指标，以及各自第 5 轮的 `NOT_RUN_EARLY_STOP` 状态。
- `conditions-summary.csv` 包含三模型、19 个条件的 57 条汇总。该表和训练 Dev 表的实验设置不同。

来源为历史固定提交 `45f11b75b6a6ede924b806ea9508a0629ddfa395` 中的逐轮日志、A/B 早停摘要与正式条件汇总。这里仅提供训练及评价层面的汇总数据，适用于报告实验设置和结果。
