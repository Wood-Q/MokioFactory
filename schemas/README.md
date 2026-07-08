# schemas/

数据契约。三类核心 Schema（JSON Schema + Pydantic 双写）：

- `pretrain.schema.json` — 预训练记录 `{id, text, source, ...}`
- `sft.schema.json` — SFT 记录 `{id, messages:[{role,content}], ...}`
- `preference.schema.json` — 偏好记录 `{id, prompt, chosen, rejected, ...}`

`models.py` 用 Pydantic 定义对应 Record；`pipelines/validate_schema.py` 校验 JSONL/Parquet。
每条数据强制含 `id / schema_version / source / domain / quality_score / meta`。
