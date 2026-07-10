# Gold 数据配比

`gold` 是训练输入层：它不是简单把所有 silver 文件拼起来，而是一个带版本、配比、切分、license 和输入血缘的训练数据发布物。

当前 `agent-code-v1` 的目标是 4000 条小规模 smoke 数据：

| 来源 | 数量 | 训练重点 |
|---|---:|---|
| xLAM Function Calling | 1400 | 单轮工具选择与参数生成 |
| APIGen-MT | 800 | 多轮 function call / observation |
| OpenThoughts Agent | 702 | 终端与 coding-agent 轨迹 |
| Fable traces | 98 | 项目级 coding-agent 轨迹 |
| OpenCodeInstruct | 1000 | 代码生成与指令跟随 |

配比使用无放回的确定性抽样，避免为了达到比例而复制同一条样本。抽样前会重新应用 audit 的阻断规则；本次 30 条不合格记录不会进入 gold。

## 分层切分

每个来源独立按固定 seed 切分：95% `train`、5% `validation`。这样验证集保留各来源的覆盖，避免 xLAM 这类大来源淹没较小的 agent trace 来源。

输出路径：

```text
gold/schema=sft.v1/mixture=agent-code-v1/run_id=stage1-20260710-001/
  split=train/part-000000.jsonl
  split=validation/part-000000.jsonl
  manifest.json
```

每个 shard 最多 1000 条记录。分片是后续多 Pod 清洗、tokenize 和训练 dataloader 并行读取的基础。

## 运行

```bash
.venv/bin/python -m pipelines.mixture.build_gold_mixture \
  --config configs/mixtures/stage1_agent_code_v1.yaml
```

输出 run_id 默认不可覆盖。确认要重跑时：

```bash
.venv/bin/python -m pipelines.mixture.build_gold_mixture \
  --config configs/mixtures/stage1_agent_code_v1.yaml \
  --overwrite
```

## 生产约束

此 mixture 包含 `CC-BY-NC-4.0` 的 APIGen 和 `AGPL-3.0` 的 Fable，因此 manifest 明确标记为研究学习用途，不能直接视为商业可用训练集。

Stage 1 为了跑通训练闭环，允许 audit 自动 gate 通过但人工审核仍是 `pending` 的版本继续构建 gold。生产环境必须把 `allow_pending_manual_review_for_stage1_smoke` 设为 `false`，并在人工审核批准后再发布 gold。
