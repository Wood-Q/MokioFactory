# Silver 数据审核

本阶段位于 `silver -> gold` 之间，目标不是再次修改 silver，而是回答三个问题：

1. 数据是否符合 `sft.v1` schema。
2. 哪些记录可以进入 gold，哪些需要阻断。
3. 人工应该优先检查哪些来源和异常样本。

工业数据治理通常同时存在自动门禁和人工审核。自动门禁适合处理确定性规则，例如 schema、空消息、轮次顺序和长度；人工审核负责判断回答质量、事实性、安全性、工具调用合理性和 license 风险。

## 产物

```text
silver shard
  -> report.json
  -> review_queue.jsonl
  -> manifest.json
  -> reports/data_quality/stage1_phase1_silver_audit.md
```

- `report.json`：全量统计、来源画像、异常计数和自动 gate 结果。
- `review_queue.jsonl`：按来源和异常类型做确定性分层抽样，预留 `pending / approved / rejected` 审核字段。
- `manifest.json`：记录输入 silver manifest 指纹、配置指纹和审核产物指纹。
- Markdown：便于在仓库内快速阅读本次审核结论。

## 自动门禁

当前阻断条件包括：

- JSON Schema 不合法。
- 空消息或缺少 assistant。
- user/tool 与 assistant/function_call 轮次不交替。
- 总字符数超过上限。
- 工具定义仍是未解析字符串。
- Tool Calling 数据缺少工具定义。

`long_sample` 和 `too_many_messages` 默认作为警告，不直接阻断，便于后续结合 tokenizer 的真实 token 数再决定截断策略。

## 运行

```bash
.venv/bin/python -m pipelines.audit.audit_silver \
  --config configs/audit/stage1_phase1_silver_audit.yaml
```

同一个 `run_id` 默认不可覆盖。开发时确认要重跑才使用：

```bash
.venv/bin/python -m pipelines.audit.audit_silver \
  --config configs/audit/stage1_phase1_silver_audit.yaml \
  --overwrite
```

生产环境应该把 `manual_review.status` 作为 gold 构建的强制审批条件。Stage 1 为了跑通闭环，允许自动门禁通过后继续，但报告会始终保留人工审核为 `pending`。
