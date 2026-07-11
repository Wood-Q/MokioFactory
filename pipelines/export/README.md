# LLaMA-Factory 导出

内部 `sft.v1` 是数据湖的 canonical schema，不直接绑定任何训练框架。训练前才通过本步骤导出为 LLaMA-Factory 的 ShareGPT JSONL，避免训练框架的字段限制污染 bronze、silver 和 gold。

## 字段映射

| `sft.v1` | ShareGPT |
|---|---|
| `system` message | 顶层 `system` |
| `user` message | `{"from": "human", "value": ...}` |
| `assistant` message | `{"from": "gpt", "value": ...}` |
| `assistant` + `name=function_call` | `{"from": "function_call", "value": ...}` |
| `tool` message | `{"from": "observation", "value": ...}` |
| `tools` | 顶层 JSON 字符串 `tools` |

导出器会在写训练文件前执行 LLaMA-Factory schema audit：

- `human / observation` 必须位于输入侧，`gpt / function_call` 必须位于模型学习侧。
- 对话必须从 `human` 开始，并以 `gpt` 或可直接监督的 `function_call` 结束。
- `observation` 前必须是 `function_call`；非末尾 `function_call` 后必须有 `observation`。
- role 和 message value 必须合法且非空，`tools` 必须能解析为对象数组。
- 对于 `human -> function_call -> observation` 后缺少 assistant 回复的尾部不完整轮次，只删除末尾不可学习的 `observation`，保留 `human -> function_call` 监督目标，不伪造模型回答。
- 不能确定性修复的记录会阻断整个导出，不会静默丢弃。

审核结果写入 `schema_audit.json`，并同步嵌入 export manifest，记录各 split 输入/输出数、修复数、修复原因和拒绝策略。

## 输出

```text
exports/format=llamafactory-sharegpt/mixture=agent-code-v1/run_id=stage1-20260710-001/
  train.jsonl
  validation.jsonl
  dataset_info.json
  schema_audit.json
  manifest.json
```

`dataset_info.json` 已经声明 ShareGPT 格式、`conversations`、`system` 和 `tools` 字段。训练机器上把整个前缀下载到本地 `dataset_dir` 后，即可在 LLaMA-Factory 中引用配置里的 train/validation dataset name。

## 运行

```bash
.venv/bin/python -m pipelines.export.export_llamafactory \
  --config configs/export/stage1_llamafactory_sharegpt.yaml \
  --overwrite
```

本次 4000 条 gold 数据实际审核修复 17 条尾部不完整 Agent 轨迹，其中 train 15 条、validation 2 条。修复后记录总数保持 `3800 train + 200 validation`，LLaMA-Factory 不再因为末尾 observation 跳过这些样本。

下载训练输入示例：

```bash
mc cp --recursive \
  mokio/mokio-lake/exports/format=llamafactory-sharegpt/mixture=agent-code-v1/run_id=stage1-20260710-001/ \
  data/llamafactory/stage1_agent_code_v1/
```

生产环境里通常由训练 Job 的 init container 或数据加载器从对象存储 materialize 到本地 NVMe / PVC；Stage 1 先用 `mc cp` 把这个动作显式化，便于学习和排错。
