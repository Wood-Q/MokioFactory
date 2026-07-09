# clean pipeline

本目录负责把 raw 层数据转换成统一 schema，并做第一轮基础清洗。

Stage 1 · Phase 1 当前新增的是：

```text
MinIO raw JSONL -> bronze/schema=sft.v1 -> silver/schema=sft.v1
```

## 分层

- `raw`：原始下载，不修改，不覆盖。
- `bronze`：已经转成统一 `sft.v1` schema，但只做轻量规范化。
- `silver`：在 bronze 基础上做基础过滤和去重，可作为后续 mix/tokenize 的输入。
- `gold`：后续按数据配比混合后生成，可直接训练。

## 统一 SFT Schema

见：

```text
schemas/sft.schema.json
```

核心字段：

```json
{
  "id": "...",
  "schema_version": "sft.v1",
  "source_dataset": "salesforce-xlam-function-calling-60k",
  "task_family": "agent_tool_calling",
  "domain": "agent",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "tools": [],
  "quality_score": 0.8,
  "meta": {}
}
```

## 运行

先确认 MinIO 可读：

```bash
mc ls mokio/mokio-lake/raw/source=hf/
```

执行转换和清洗：

```bash
.venv/bin/python pipelines/clean/normalize_sft.py \
  --config configs/cleaning/stage1_phase1_sft_cleaning.yaml
```

输出位置：

```text
s3://mokio-lake/bronze/schema=sft.v1/date=2026-07-09/part-000000.jsonl
s3://mokio-lake/bronze/schema=sft.v1/date=2026-07-09/manifest.json
s3://mokio-lake/silver/schema=sft.v1/date=2026-07-09/part-000000.jsonl
s3://mokio-lake/silver/schema=sft.v1/date=2026-07-09/manifest.json
```

## 当前清洗规则

- 统一转换为 `messages` 格式。
- 清理控制字符。
- 折叠多余空白。
- 删除空消息。
- 删除缺少 assistant 回复的数据。
- 删除超长数据。
- 按 messages 文本做基础去重。

## Fable trace 转换

`Glint-Research/Fable-5-traces` 不是普通的一行一条 SFT 数据，而是 coding-agent 的事件流。

当前 parser 会按 `session` 事件切分 trace，把同一段 trace 内的事件转换为一条 `sft.v1` 样本：

```text
session -> model_change -> thinking_level_change -> user message -> assistant message
```

转换规则：

- `message.role=user` 转成 `messages[].role=user`。
- `message.role=assistant` 转成 `messages[].role=assistant`。
- `content[].type=text` 保留为自然语言内容。
- `content[].type=thinking` 保留为 assistant 内容，用于学习 agent 的规划过程。
- `content[].type=toolCall` 会同时写入 assistant 内容和顶层 `tools` 字段。
- `session / model_change / thinking_level_change` 不作为训练对话，只写入 `meta`。

这样做的目的，是把 Fable 的项目级 agent trace 转成可治理、可过滤、可混合的统一 schema，而不是把整行 raw JSON 粗暴塞进 assistant 回复。

## 后续

下一步会继续补：

```text
silver -> gold mixture -> LLaMA-Factory / TRL training jsonl
```
