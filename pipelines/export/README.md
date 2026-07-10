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

导出器会验证 ShareGPT 的交替顺序：`human / observation` 在输入侧，`gpt / function_call` 在模型学习侧。错误的轮次会直接终止导出。

## 输出

```text
exports/format=llamafactory-sharegpt/mixture=agent-code-v1/run_id=stage1-20260710-001/
  train.jsonl
  validation.jsonl
  dataset_info.json
  manifest.json
```

`dataset_info.json` 已经声明 ShareGPT 格式、`conversations`、`system` 和 `tools` 字段。训练机器上把整个前缀下载到本地 `dataset_dir` 后，即可在 LLaMA-Factory 中引用配置里的 train/validation dataset name。

## 运行

```bash
.venv/bin/python -m pipelines.export.export_llamafactory \
  --config configs/export/stage1_llamafactory_sharegpt.yaml
```

下载训练输入示例：

```bash
mc cp --recursive \
  mokio/mokio-lake/exports/format=llamafactory-sharegpt/mixture=agent-code-v1/run_id=stage1-20260710-001/ \
  data/llamafactory/stage1_agent_code_v1/
```

生产环境里通常由训练 Job 的 init container 或数据加载器从对象存储 materialize 到本地 NVMe / PVC；Stage 1 先用 `mc cp` 把这个动作显式化，便于学习和排错。
