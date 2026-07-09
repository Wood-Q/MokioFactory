# schemas/

数据契约。核心 Schema 会先以 JSON Schema 固化，后续再补 Pydantic 模型：

- `pretrain.schema.json` — 预训练记录 `{id, text, source, ...}`
- `sft.schema.json` — SFT 记录 `{id, messages:[{role,content}], ...}`
- `preference.schema.json` — 偏好记录 `{id, prompt, chosen, rejected, ...}`

当前已落地 `sft.schema.json`，用于 Stage 1 · Phase 1 的 raw -> bronze/silver 转换。

每条 SFT 数据强制含：

```text
id / schema_version / source_dataset / task_family / domain / messages / tools / quality_score / meta
```

后续会补 `models.py` 和 `pipelines/validate_schema.py`，用于校验 JSONL/Parquet。
