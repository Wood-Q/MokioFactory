# LLaMA-Factory Qwen3-4B QLoRA Smoke

本阶段使用官方 Docker 镜像，不 clone LLaMA-Factory 源码。当前配置参考官方 Qwen3 LoRA/QLoRA 示例，基座为 `Qwen/Qwen3-4B-Instruct-2507`，模板为 `qwen3_nothink`。

## LoRA 与 QLoRA

- LoRA 冻结基座权重，只训练低秩 adapter，显存主要用于基座权重、激活和优化器状态。
- QLoRA 在 LoRA 基础上把冻结的基座权重量化为 4-bit，进一步降低显存；adapter 仍以较高精度训练。
- 当前配置使用 bitsandbytes 4-bit、LoRA rank 8、单卡 batch size 1 和梯度累积 8，面向 RTX 3090 / 4090 24GB 或租用的同级 NVIDIA GPU。

QLoRA smoke 产生的是 adapter checkpoint，不是完整模型权重。后续验证效果后再做完整训练、adapter 合并和模型发布。

## 为什么先跑 Smoke

20 step smoke 主要验证：

```text
ShareGPT 数据 -> tokenizer/template -> 4-bit Qwen3 -> LoRA backward
    -> eval_loss -> adapter checkpoint -> 本地 output 目录
```

验收标准：

- 训练集和验证集能被 LLaMA-Factory 识别。
- GPU 正常分配，没有 CPU fallback。
- loss / eval_loss 是有限数值，没有 NaN。
- `/workspace/output/qwen3-4b-qlora-sft-smoke` 生成 adapter 和 trainer state。

## 运行环境

预构建 CUDA 镜像面向 x86_64 NVIDIA Linux。macOS Docker 无法把 Apple GPU 暴露给这个容器，因此 Mac 只负责数据准备，训练需要 NVIDIA Linux 主机。

GPU 主机需要：

- NVIDIA Driver。
- Docker Engine。
- NVIDIA Container Toolkit。

先验证 Docker 能看到 GPU：

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.4.1-base-ubuntu22.04 \
  nvidia-smi
```

## 1. Materialize 训练数据

如果 GPU 主机可以访问 MinIO / OSS：

```bash
.venv/bin/python -m pipelines.train.llamafactory.materialize_dataset \
  --config configs/training/llamafactory/stage1_dataset_materialization.yaml
```

脚本会下载并校验：

```text
data/llamafactory/stage1_agent_code_v1/
  train.jsonl                 3800 records
  validation.jsonl             200 records
  dataset_info.json
  schema_audit.json
  materialization_manifest.json
```

### Train / Validation 如何切分

切分发生在 gold mixture 阶段，不是在训练机上临时随机切：

| 来源 | Train | Validation |
|---|---:|---:|
| xLAM Function Calling | 1330 | 70 |
| APIGen-MT | 760 | 40 |
| OpenThoughts Agent | 667 | 35 |
| Fable traces | 93 | 5 |
| OpenCodeInstruct | 950 | 50 |
| 合计 | 3800 | 200 |

每个来源独立使用固定 seed `20260710` 做无放回 95%/5% 分层切分，再分别稳定 shuffle。这样每次重跑得到相同 split，train/validation ID 不交叉，且小来源不会在 validation 中消失。

这里的 validation 用于观察训练期间的 `eval_loss`、选择 checkpoint 和发现过拟合。Phase 3 仍需要独立 benchmark/test set 测 tool calling、Agent 和 Code 能力，不能把这 200 条 validation 当作最终模型能力测评。

如果 MinIO 只运行在 Mac 本机，可以先在 Mac materialize，再用 `rsync/scp` 把整个 `data/llamafactory/stage1_agent_code_v1/` 目录传到 GPU 主机；生产环境通常让训练 Job 从云端 OSS 下载到本地 NVMe 或 PVC。

## 2. 拉取 LLaMA-Factory 镜像

```bash
docker pull hiyouga/llamafactory:latest
```

`latest` 适合第一次 smoke。跑通后应记录镜像 digest，并在正式训练中固定 digest，避免未来镜像更新导致配置不可复现。

中国大陆网络可按需设置：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

私有或 gated 模型才需要：

```bash
export HF_TOKEN=hf_xxx
```

不要把 token 写进 YAML 或提交到 Git。

## 3. 启动训练

在仓库根目录执行：

```bash
bash deploy/LLaMAFactory/run_smoke.sh
```

脚本挂载四类目录：

```text
dataset     -> /workspace/dataset:ro
config      -> /workspace/config:ro
output      -> /workspace/output
HF cache    -> /root/.cache/huggingface
```

数据和配置只读挂载，checkpoint 单独写入 output，模型缓存持久化，容器删除后结果仍然保留。

## 关键训练参数

| 参数 | Smoke 值 | 作用 |
|---|---:|---|
| `quantization_bit` | 4 | 4-bit 加载冻结基座 |
| `lora_rank` | 8 | adapter 容量 |
| `cutoff_len` | 2048 | 单样本最大 token 长度 |
| `max_samples` | 200 | 限制 smoke 数据规模 |
| `max_steps` | 20 | 限制训练时间 |
| `gradient_accumulation_steps` | 8 | 模拟更大的有效 batch |
| `bf16` | true | Ampere 及以后 GPU 的训练精度 |

如果 24GB 卡仍然 OOM，第一步把 `cutoff_len` 降到 1024；不要先删除数据或提高量化压缩。

## 输出检查

```bash
find outputs/llamafactory/qwen3-4b-qlora-sft-smoke -maxdepth 2 -type f | sort
```

本次单卡 RTX 3090 smoke 已跑通：20 steps、198 条有效 train/validation、`train_loss=0.8498`、`eval_loss=0.7171`。其中各 2 条被跳过的原因是尾部不完整 Agent 轮次；全量 schema audit 共修复 train 15 条、validation 2 条，修复后需要重跑 smoke，预期 train/validation 均完整加载 200 条 smoke 样本。

修复版 smoke 通过后，下一步才是移除 `max_samples` / `max_steps` 限制，配置正式 epoch、checkpoint 策略和 MLflow，再进入完整 SFT。

## 全量单卡基线

完整配置为：

```text
configs/training/llamafactory/qwen3_4b_qlora_sft_full.yaml
```

它使用全部 `3800 train + 200 validation`，单卡训练 1 epoch：

| 参数 | 值 |
|---|---:|
| 预计 optimizer steps | 475 |
| 有效 batch size | 8 |
| learning rate | 1e-4 |
| warmup steps | 48 |
| eval/save interval | 100 steps |
| checkpoint 保留数 | 2 |

Stage 1 先做 1 epoch 基线，观察 train/eval loss 后再决定是否增加到 2 epoch，避免直接在 3800 条小数据上重复训练导致过拟合。

Docker 运行：

```bash
bash deploy/LLaMAFactory/run_full.sh
```

### 实时查看 Loss

正式配置启用 TensorBoard，并每 5 steps 写一次日志。训练机启动：

```bash
tensorboard \
  --logdir outputs/llamafactory/qwen3-4b-qlora-sft-full-v1/tensorboard \
  --host 127.0.0.1 \
  --port 6006
```

Mac 另开终端建立 SSH 隧道：

```bash
ssh -N -L 6006:127.0.0.1:6006 shanghai-3090-34-qhk
```

浏览器打开 `http://127.0.0.1:6006`，重点看：

- `train/loss`：训练 loss 总体应下降，短期上下波动正常。
- `eval/loss`：用于判断泛化；train loss 继续下降而 eval loss 持续上升通常意味着过拟合。
- `train/learning_rate`：确认 warmup 和 cosine decay 按配置执行。
- `train/grad_norm`：持续突增或出现 NaN 时需要检查学习率、异常长样本和数值稳定性。

训练结束后还会在 output 目录生成：

```text
training_loss.png
training_eval_loss.png
trainer_log.jsonl
```
