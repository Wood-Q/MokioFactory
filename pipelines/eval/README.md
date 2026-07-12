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

## Tool-call 生成评分

生成式评测使用 vLLM，与 LLaMA-Factory 的训练和 teacher-forced loss 评估解耦。base 与 adapter 必须使用相同输入、chat template、`temperature=0` 和 `max_new_tokens`，唯一变量是是否加载 LoRA。

```bash
VLLM_USE_V1=0 python -m pipelines.eval.generate_vllm \
  data/llamafactory/stage1_agent_code_holdout_v1/test_salesforce-xlam-function-calling-60k.jsonl \
  outputs/predict/qwen3-4b-base-xlam/generated_predictions.jsonl \
  --model /home/qhk/models/Qwen3-4B-Instruct-2507 \
  --enforce-eager

VLLM_USE_V1=0 python -m pipelines.eval.generate_vllm \
  data/llamafactory/stage1_agent_code_holdout_v1/test_salesforce-xlam-function-calling-60k.jsonl \
  outputs/predict/qwen3-4b-adapter-xlam/generated_predictions.jsonl \
  --model /home/qhk/models/Qwen3-4B-Instruct-2507 \
  --adapter outputs/llamafactory/qwen3-4b-qlora-sft-full-v1 \
  --enforce-eager
```

脚本读取 ShareGPT 记录，通过 Qwen tokenizer 自带的 chat template 注入 system、tools 和历史消息，并把最后一个 assistant/function_call 留作 label。vLLM 直接在同一个基座上动态挂载 LoRA，不需要先合并完整权重。

当前 RTX 3090 算力机驱动支持到 CUDA 12.9，已验证的推理环境为 `vllm==0.10.2`、`torch==2.8.0+cu128`、`transformers==4.57.1`。使用 `VLLM_USE_V1=0` 和 `--enforce-eager` 是为了避开该版本 V1 engine 的图编译不稳定；它更适合短期离线评测，不代表生产服务必须关闭 CUDA graph。

生成完成后评分：

```bash
python -m pipelines.eval.score_tool_calls \
  outputs/predict/qwen3-4b-base-xlam/generated_predictions.jsonl \
  --output outputs/predict/qwen3-4b-base-xlam/tool_call_metrics.json
```

指标包括 JSON 可解析率、`<tool_call>` 包装格式正确率、调用数量、工具名称、参数和完整调用严格匹配率。严格匹配会规范化 JSON 对象的键顺序，但不会忽略调用顺序或参数值差异。

## 本次 QLoRA 对比

独立 xLAM holdout 共 200 条，未进入 gold train/validation。两组均使用 Qwen3 官方 chat template、`temperature=0`、`max_new_tokens=256`：

| 指标 | Base | QLoRA adapter | 变化 |
| --- | ---: | ---: | ---: |
| JSON 可解析率 | 99.5% | 100.0% | +0.5pp |
| wrapper 正确率 | 99.5% | 100.0% | +0.5pp |
| 调用数量准确率 | 99.0% | 99.5% | +0.5pp |
| 工具名称准确率 | 99.0% | 99.5% | +0.5pp |
| 参数/完整调用严格准确率 | 75.5% | 80.0% | +4.5pp |

逐样本配对结果：144 条两者都正确，16 条 adapter 修正，7 条 adapter 回退，33 条两者都错误。当前 adapter 有净提升，但 200 条样本仍偏小；下一轮应扩大独立 tool-call benchmark，并重点补充默认参数、offset、单次/多次调用边界样本。

xLAM 的部分 label 会显式填充用户没有提供的默认值或占位凭证。严格准确率适合检查是否复现数据集标注，但不等同于真实工具执行成功率；后续还需要增加 schema 校验和可执行 mock tool 测试。

## 外部 Benchmark

内部 holdout 跑通后，Stage 1 · Phase 3 接入 [BFCL、EvalPlus 与 τ³-bench](benchmarks/README.md) 小切片。三者分别覆盖标准工具调用、可执行代码正确性和有状态多轮 Agent 任务，统一比较 base/adapter，但继续使用各项目官方 evaluator。
