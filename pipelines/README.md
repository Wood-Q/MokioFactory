# pipelines/

各阶段流水线（Python CLI 入口），演进路径：CLI → Docker → K8s Job/Argo。

- `ingest/` — 数据采集与下载，落 raw 层
- `clean/` — 清洗算子 pipeline，raw → silver
- `audit/` — schema/结构门禁、数据画像和人工审核队列，silver → audit report
- `mixture/` — 确定性采样、来源配比、train/validation 切分，silver + audit → gold
- `export/` — 训练框架适配，gold → LLaMA-Factory ShareGPT JSONL
- `tokenize/` — Tokenize，silver → tokenized shards
- `train/` — 训练入口（预训练 / SFT / LoRA / RL）
- `eval/` — 测评入口，产出 eval_report

数据层的原则是：canonical schema 只在 `clean` 产生，`audit` 决定准入，`mixture` 负责发布训练数据版本，`export` 才处理某个训练框架的字段格式。
