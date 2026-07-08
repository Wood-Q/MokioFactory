# MokioFactory — minimind plus 版（工业组件复刻计划）

## 0. 项目定位

**minimind 的 plus 版**：[minimind](https://github.com/jingyaogong/minimind) 用从零 PyTorch 实现了一个单卡可跑的完整小 LLM（预训练 → SFT → DPO）。MokioFactory 做同一件事，但**每个阶段用工业组件和项目实现，而不是手写 torch**——学的是可迁移的工业流水线，不是造轮子。

与 minimind 的差异（补齐/改变的几块）：
- **实现方式**：工业组件（LLaMA-Factory / TRL / DeepSpeed / Data-Juicer / lm-eval），不手写 torch
- **数据治理**：minimind 用提前备好的成品数据；MokioFactory 走完整治理流程（采集→落湖→版本→清洗→审核→配比），并复刻其数据构建方法而非搬成品
- **详细测评**：minimind 训练后无详细测评；MokioFactory 补齐三层测评 + eval 闭环
- **架构**：沿用 Qwen2 风格（RoPE / RMSNorm / GQA / SwiGLU）
- **延伸 agent**：SFT 之后用 LoRA 增强 agent（工具调用 / function-calling），再用 RL 强化

四阶段主线（单一模型逐阶段演进）：

```text
复刻数据集 -> 预训练(Qwen2风格小模型) -> SFT(指令对齐) -> LoRA(agent能力) -> RL(强化agent)
```

核心原则：复用开源组件不自研框架；配置与代码分离；数据不可变（raw→bronze→silver→gold）；全 lineage 记录；任务可重跑。

## 1. 与 minimind 的对照

| 维度 | minimind | MokioFactory |
| --- | --- | --- |
| 实现方式 | 从零 PyTorch | 工业组件（LLaMA-Factory/TRL/DeepSpeed…） |
| 架构 | Qwen2 风格 | 同（Qwen2 风格） |
| 模型规模 | ~26M / ~104M，单卡可跑 | 同量级，~26M–100M+ |
| **数据** | **提前备好的成品，无治理流程** | **完整治理：采集→落湖→版本→清洗→审核→配比** |
| **测评** | **训练后无详细测评** | **三层测评（Smoke/Standard/Business）+ eval 闭环** |
| 阶段 | 预训练→SFT→DPO | 预训练→SFT→LoRA→RL |
| 目标 | 聊天小模型 | + agent 能力 |
| 训练栈 | 手写 Trainer | LLaMA-Factory/TRL + DeepSpeed |

## 2. 三阶段路线（基础设施演进）

基础设施三阶段服务于同一条训练主线，不是平行关系。

| 阶段 | 目标 | 技术栈 |
| --- | --- | --- |
| Phase 1 单机闭环 | 游戏本跑通四阶段 | 本地 FS + MinIO + PostgreSQL + MLflow；HF Datasets + Polars/DuckDB + Data-Juicer；LLaMA-Factory/TRL；lm-eval |
| Phase 2 单机 K8s | 脚本变 Job/Workflow | K3s/Kind + Argo Workflows + MinIO + Docker |
| Phase 3 租卡分布式 | 体验分布式、控成本 | DeepSpeed + torchrun；Kubeflow Training Operator；vLLM + lm-eval |

## 3. 目录架构

```text
MokioFactory/
  configs/{datasets,cleaning,mixtures,training,eval}
  schemas/        # *.schema.json
  pipelines/{ingest,clean,tokenize,train,eval}
  operators/{filters,normalizers,dedup,quality}
  k8s/{argo,jobs,training}
  notebooks/{audit,analysis}
  reports/  docs/
```

## 4. 数据：复刻 minimind 数据集 + 补齐治理流程

minimind 用提前备好的成品数据，**没有采集/版本/清洗/审核/配比的治理流程**。MokioFactory 补齐这块：复刻其数据**构建方法**（不搬运成品），并用工业组件走完整治理链路。本节及 §5–§9 共同构成这块短板的补齐。

| 数据集 | minimind 做法 | MokioFactory 复刻方式 |
| --- | --- | --- |
| 预训练语料 | 中英混合，百科/网页/书籍清洗 | HF 开源源（Wikipedia / Common Crawl 小切片 / fineweb-edu）+ Data-Juicer 清洗 |
| SFT 指令 | 单轮 + 多轮指令 | alpaca-cleaned / Open-Orca 小切片 + 自合成指令 |
| 偏好(DPO/RL) | 偏好对 | 自合成 chosen/rejected，或偏好子集 |
| Agent 数据（延伸） | minimind 无 | 工具调用 / function-calling 合成数据 |

落湖结构（Hive 分区，raw 不可变）：

```text
s3://mokio-lake/
  raw/source=hf/dataset=<name>/date=2026-07-08/
  bronze/schema=pretrain|sft|preference/v1/dataset=<name>/
  silver/recipe=<clean_recipe>/dataset=<name>/
  gold/mixture=<mixture_name>/
```

存储分工：MinIO 存文件/模型/ckpt；PostgreSQL 存元数据（版本/任务/manifest/eval 索引）；lakeFS 第二版再加。第一版 = MinIO + PostgreSQL。

## 5. Schema

三类核心 Schema（JSON Schema + Pydantic 双写）：PretrainRecord / SFTRecord / PreferenceRecord。每条强制含 `id / schema_version / source / domain / quality_score / meta`。落地 `schemas/*.schema.json` + `schemas/models.py` + `pipelines/validate_schema.py`。

## 6. 清洗与算子

清洗是 operator pipeline：`normalize -> language_filter -> length_filter -> dedup -> pii_filter -> quality_score -> schema_validate`。第一版用 Data-Juicer 跑基础清洗 + 自补少量算子，规则全写 YAML。算子接口 `__call__(record) -> dict | None`（None 即过滤）。

## 7. 分片与并行

每 shard 128MB–1GB，文件名稳定，配 manifest（样本数/字节/hash/来源）。第一版 JSONL.zst，第二版 Parquet。并行：单机 `datasets.map(num_proc=N)` → Ray Data → K8s Argo fan-out。

## 8. 人工审核

三层：自动统计 → 自动质检（PII/毒性/低质） → 人工抽样。工具 Label Studio（通用）或 Argilla（SFT/偏好更顺手）。每版本出 `profile.json / samples.jsonl / audit.md`。流程：`LLM judge 打标 → 人工看高风险/低置信 → 回写 quality_score`。

## 9. 数据配比

YAML 管 mixture（源 token 数、domain 权重、阶段配比、变更原因、与 eval 关系）。以复刻 minimind 的中英混合配比作基线，再做多组对照（如 general:code 100:0 / 70:30 / 50:50）。

## 10. Tokenizer

- 从零小模型：自训 BPE（与 minimind 一致），8k–32k 词表，100MB–1GB 语料，固化版本
- SFT/LoRA/RL：复用预训练阶段产出的 tokenizer，不换

特殊 token `<pad><bos><eos><unk>`。seq_len：预训练 512/1024，SFT 2048。

## 11. 模型架构与训练（核心）

### 架构

Qwen2 风格 decoder-only Transformer（与 minimind 一致）：RoPE 位置编码 / RMSNorm / SwiGLU / GQA / 无 bias。规模 ~26M–100M+，单卡（3090 24GB）可训。

参数量粗算：`params ≈ 12 × n_layer × n_embd² + vocab × n_embd²`（vocab embedding 占比不小，与 §10 tokenizer 绑定），`head_dim ≈ 64`。

| 目标 | n_layer | n_embd | n_head |
| --- | --- | --- | --- |
| ~26M | 8 | 512 | 8 |
| ~60M | 10 | 640 | 10 |
| ~100M | 12 | 768 | 12 |

**关键：架构用工业组件定义和训练，不手写。** 用 LLaMA-Factory 的模型 config（Qwen 架构）或 HF Transformers 的 `Qwen2Config` 定义结构，由 LLaMA-Factory/TRL + DeepSpeed 跑训练。

### 四阶段训练

| 阶段 | 目的 | 工业组件 | 数据 |
| --- | --- | --- | --- |
| ① 预训练 | 学语言建模，loss 下降、能生成 | LLaMA-Factory/TRL + DeepSpeed | 预训练语料（gold mixture） |
| ② SFT | 指令对齐，会对话 | LLaMA-Factory/TRL | SFT 指令数据 |
| ③ LoRA | 增强 agent 能力（工具调用/function-calling） | LLaMA-Factory + PEFT | agent 合成数据 |
| ④ RL | 强化 agent（DPO/GRPO） | TRL DPO/GRPO | 偏好/agent 轨迹数据 |

分布式：先单卡 → `torchrun --nproc_per_node=1` 保入口 → 租 2–4 卡开 DeepSpeed ZeRO-2/3 → K8s 用 Job/MPIJob。

## 12. Checkpoint 与模型注册

区分训练 ckpt（含 optimizer/scheduler/rng，续训用）vs 发布权重（safetensors）vs 模型注册。DeepSpeed ZeRO ckpt 用 `zero_to_fp32.py` 聚合 → HF 格式 → safetensors。模型版本必记：`model_version / base_model / tokenizer_version / data_mixture / cleaning_recipe / training_config / stage / git_commit / docker_image / checkpoint_path / eval_report_path`。

## 13. 测评与闭环

minimind **训练后无详细测评**——这是 MokioFactory 要补齐的第二块短板。补齐方式：三层测评 + eval 闭环，且每个训练阶段（预训练/SFT/LoRA/RL）都产出 eval_report，前后可对比。

三层：Smoke eval（几十题确认没坏）→ Standard（MMLU/C-Eval/CMMLU/GSM8K/HumanEval）→ Business（自有任务/badcase/agent 任务）。从零小模型先测 perplexity/生成质量/holdout loss，不上大 benchmark；agent 阶段测工具调用成功率。闭环：`eval badcase → 标原因 → 归 domain → 调 cleaning/mixture/agent 数据 → 重训 → 重 eval`。

## 14. 实验矩阵

| 组 | 目的 | 配置 |
| --- | --- | --- |
| 1 | 学预训练 | 复刻 minimind 预训练语料 → 26M Qwen2 → valid loss/生成 |
| 2 | 学 SFT | 26M base → SFT → 对话 eval |
| 3 | 学 agent LoRA | SFT model → LoRA(工具调用数据) → agent 任务成功率 |
| 4 | 学 RL | LoRA model → DPO/GRPO → agent 成功率提升 |
| 5 | 学数据配比 | general:code 100:0/70:30/50:50 同配置对比 eval |
| 6 | 学分布式 | 租 2–4 卡 → DeepSpeed ZeRO-2/3 → ckpt 聚合 |

## 15. MVP 里程碑

| # | 里程碑 | 验收 |
| --- | --- | --- |
| M1 | 数据湖+元数据（MinIO/PG/MLflow，下语料入 raw） | 能列版本、能读回 |
| M2 | Schema+清洗（三类 Schema，raw→bronze→silver） | 全过 schema 校验、有前后统计 |
| M3 | 人工审核（Label Studio/Argilla 抽样） | 看质量、能导 badcase |
| M4 | 配比+Tokenizer（mixture.yaml、BPE、tokenized shards） | 可复现 mixture、tokenizer 有版本+报告 |
| M5 | 预训练小模型（Qwen2 风格，LLaMA-Factory/TRL，ckpt+safetensors+MLflow） | loss 正常下降、能生成 |
| M6 | SFT（工业组件微调 base） | base vs SFT 对比、eval 有结论 |
| M7 | LoRA agent（PEFT + 工具调用数据） | agent 任务成功率 > base |
| M8 | RL agent（TRL DPO/GRPO） | agent 成功率进一步提升 |
| M9 | K8s Workflow（K3s+Argo，各阶段成 Job） | 一条 Workflow 端到端 |
| M10 | 分布式体验（租卡 DeepSpeed） | 能解释 ZeRO ckpt 与发布权重区别 |

## 16. 优先级与取舍

**优先级**
- P1：MinIO、PostgreSQL、HF Datasets、Pydantic/JSON Schema、Data-Juicer、MLflow、LLaMA-Factory/TRL、lm-eval
- P2：Argo Workflows、Label Studio/Argilla、lakeFS、Ray Data、DeepSpeed、PEFT
- P3：Kubeflow Training Operator、Kueue/Volcano、OpenCompass、vLLM

**取舍**
- 架构：固定 Qwen2 风格，不纠结、不比较——用工业组件定义
- 是否上 K8s：不急。路径 `Python CLI → Docker → Compose → K3s/Argo → Kubeflow`
- 是否训 tokenizer：是（复刻 minimind，自训 BPE），仅用于自训小模型主线
- agent vs 纯聊天：在 minimind 基础上延伸到 agent，LoRA + RL 是 agent 增强两步
- RL 形式：先 DPO（简单稳定），再尝试 GRPO（agent 轨迹强化）

## 17. 最终目标

不比别人模型强，但能系统回答：数据来自哪 → 经哪些清洗 → 符合哪个 schema → 哪些被过滤及为何 → 用了哪个配比 → tokenizer 哪版 → 处于哪个训练阶段(stage) → ckpt 能否恢复 → 权重如何导出 → eval 如何 → 下轮怎么改。

这些问题都能被系统回答时，项目就具备了工业训练流水线的骨架——一个用工业组件复刻、延伸到 agent 的 minimind plus 版。
