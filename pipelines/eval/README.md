# 独立 Holdout 测试集

训练使用的 3800 条 train 和 200 条 validation 都属于 gold mixture。validation 已参与 checkpoint 选择，不能作为最终无偏测试集。

本步骤从 silver 中排除全部 4000 个 gold ID，再按固定 seed 无放回抽取：

| 来源 | Test 数量 |
|---|---:|
| xLAM Function Calling | 200 |
| APIGen-MT | 200 |
| OpenThoughts Agent | 100 |
| 合计 | 500 |

Fable 和 OpenCodeInstruct 已被 gold 全量使用，没有剩余样本可构建内部 holdout；它们需要在 Phase 3 使用外部 Code/Agent benchmark 评估。

```bash
.venv/bin/python -m pipelines.eval.build_holdout \
  --config configs/eval/stage1_agent_code_holdout.yaml \
  --overwrite
```

脚本生成 LLaMA-Factory ShareGPT JSONL、`dataset_info.json` 和 `test_manifest.json`，并强制验证 test 与 train/validation ID 零交叉。

## Base / Adapter 对比

评估配置：

```text
configs/eval/llamafactory/qwen3_4b_base_holdout.yaml
configs/eval/llamafactory/qwen3_4b_adapter_holdout.yaml
```

两组评估使用同一个 Qwen3-4B、4-bit bitsandbytes、`qwen3_nothink` template、2048 cutoff 和 test set。唯一差异是第二组加载训练后的 LoRA adapter。

本阶段先比较 teacher-forced `eval_loss` 和 `perplexity = exp(eval_loss)`。它能衡量模型对目标回答的拟合程度，但不能替代 Agent 工具执行成功率、代码单测通过率等生成式能力指标。
