# reports/

每次数据清洗版本、训练 run、测评产出的报告（JSON / JSONL / Markdown）。

数据质检示例：`data_quality/<dataset>.<recipe>.{profile.json, samples.jsonl, audit.md}`。

当前 Stage 1 的实际审核结论见 [stage1_phase1_silver_audit.md](data_quality/stage1_phase1_silver_audit.md)。完整 `report.json`、人工 `review_queue.jsonl` 和 manifest 存放在 MinIO 的 `audit/schema=sft.v1/run_id=stage1-20260710-001/`。
