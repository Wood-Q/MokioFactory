# schemas/

数据契约。核心 Schema 会先以 JSON Schema 固化，后续再补 Pydantic 模型：

- `pretrain.schema.json` — 预训练记录 `{id, text, source, ...}`
- `sft.schema.json` — SFT 记录 `{id, messages:[{role,content}], ...}`
- `preference.schema.json` — 偏好记录 `{id, prompt, chosen, rejected, ...}`

当前已落地 `sft.schema.json`，用于 Stage 1 · Phase 1 的 raw -> bronze/silver 转换，并由 `pipelines/audit/audit_silver.py` 通过 JSON Schema 做全量验证。

每条 SFT 数据强制含：

```text
id / schema_version / source_dataset / task_family / domain / messages / tools / quality_score / meta
```

工具语义约定：`messages[].name=function_call` 代表 assistant 发起工具调用；`role=tool` 代表工具 observation；顶层 `tools` 只保存工具定义。没有工具定义的 Fable 调用实例保存在 `meta.tool_calls`，避免把“定义”和“执行记录”混为一谈。

后续会补 `models.py`，用于校验 JSONL/Parquet。
