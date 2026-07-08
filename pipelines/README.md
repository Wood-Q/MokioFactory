# pipelines/

各阶段流水线（Python CLI 入口），演进路径：CLI → Docker → K8s Job/Argo。

- `ingest/` — 数据采集与下载，落 raw 层
- `clean/` — 清洗算子 pipeline，raw → silver
- `tokenize/` — Tokenize，silver → tokenized shards
- `train/` — 训练入口（预训练 / SFT / LoRA / RL）
- `eval/` — 测评入口，产出 eval_report
