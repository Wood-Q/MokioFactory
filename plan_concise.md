# MokioFactory — Qwen3 小参数模型工业流水线学习计划

## 0. 项目定位

MokioFactory 的目标是让个人开发者在游戏本或低成本租卡环境里，围绕 **Qwen3 小参数开源基座模型** 跑通一套接近工业界的 LLM 训练流水线。项目不以从零发明模型架构为目标，而是复用成熟开源模型架构和权重，把精力放在数据治理、训练配置、实验追踪、评测和迭代闭环上。

核心变化：

- **基座模型**：使用 Qwen3 小参数版，不再维护仓库内自研模型实现。
- **实现方式**：复用工业组件，包括 LLaMA-Factory、TRL、PEFT、DeepSpeed、Data-Juicer、lm-eval。
- **数据治理**：走完整流程：采集 → 落湖 → 版本 → 清洗 → 审核 → 配比。
- **详细测评**：补齐 Smoke / Standard / Business 三层测评和 eval 闭环。
- **架构**：复用 Qwen3 dense decoder-only causal LM 架构和官方 tokenizer。
- **延伸 agent**：SFT 之后用 LoRA/QLoRA 增强工具调用 / function-calling，再用 DPO/GRPO 做偏好或行为强化。

主线：

```text
数据治理 -> Qwen3小参数基座 -> SFT(指令对齐) -> LoRA/QLoRA(agent/领域能力) -> DPO/GRPO(偏好/强化) -> 测评闭环
```

核心原则：复用开源组件不自研模型框架；配置与代码分离；数据不可变（raw→bronze→silver→gold）；完整 lineage 记录；任务可重跑。

## 1. 与从零小模型路线的对照

| 维度 | 从零小模型路线 | MokioFactory |
| --- | --- | --- |
| 实现方式 | 手写模型/Trainer 或轻量框架 | 工业组件（LLaMA-Factory / TRL / PEFT / DeepSpeed） |
| 架构 | 自定义 GPT/Qwen-like 小模型 | Qwen3 官方架构 |
| 权重 | 随机初始化 | Qwen3 官方小参数权重 |
| tokenizer | 自己训练 | 复用 Qwen3 官方 tokenizer |
| 数据 | 常用成品数据快速训练 | 完整治理：采集→落湖→版本→清洗→审核→配比 |
| 测评 | 简单 loss/生成检查 | 三层测评（Smoke/Standard/Business）+ eval 闭环 |
| 阶段 | 预训练→SFT→DPO | SFT→LoRA/QLoRA→DPO/GRPO→评测闭环 |
| 目标 | 理解预训练原理 | 学工业界围绕基座模型做数据与训练迭代 |

## 2. 三阶段路线（基础设施演进）

| 阶段 | 目标 | 技术栈 |
| --- | --- | --- |
| Phase 1 单机闭环 | 游戏本跑通 Qwen3 小模型 SFT/LoRA | 本地 FS + MinIO + PostgreSQL + MLflow；HF Datasets + Polars/DuckDB + Data-Juicer；LLaMA-Factory/TRL；lm-eval |
| Phase 2 单机 K8s | 脚本变 Job/Workflow | K3s/Kind + Argo Workflows + MinIO + Docker |
| Phase 3 租卡分布式 | 体验分布式、控成本 | DeepSpeed + torchrun；Kubeflow Training Operator；vLLM + lm-eval |

基础设施三阶段服务于同一条训练主线，不是平行关系。

## 3. 目录架构

```text
MokioFactory/
  configs/{datasets,cleaning,mixtures,training,eval}
  schemas/        # *.schema.json
  pipelines/{ingest,clean,tokenize,train,eval}
  operators/{filters,normalizers,dedup,quality}
  k8s/{argo,jobs,training}
  notebooks/{audit,analysis}
  reports/  docs/  models/
```

## 4. 数据：围绕 Qwen3 微调补齐治理流程

Qwen3 已经具备通用语言能力，本项目第一阶段不做从零预训练，而是围绕 SFT、agent 数据、偏好数据构建完整治理链路。

| 数据集 | 用途 | MokioFactory 做法 |
| --- | --- | --- |
| SFT 指令 | 指令对齐 | alpaca-cleaned / Open-Orca 小切片 + 自合成指令 |
| Agent 数据 | 工具调用 / function-calling | 自合成工具调用数据 + 人审抽样 |
| 偏好数据 | DPO/GRPO | 自合成 chosen/rejected，或使用偏好子集 |
| 通用文本 | 可选继续预训练 | fineweb-edu / wiki 小切片，仅作为后续扩展 |

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

每 shard 128MB-1GB，文件名稳定，配 manifest（样本数/字节/hash/来源）。第一版 JSONL.zst，第二版 Parquet。并行：单机 `datasets.map(num_proc=N)` → Ray Data → K8s Argo fan-out。

## 8. 人工审核

三层：自动统计 → 自动质检（PII/毒性/低质） → 人工抽样。工具 Label Studio（通用）或 Argilla（SFT/偏好更顺手）。每版本出 `profile.json / samples.jsonl / audit.md`。流程：`LLM judge 打标 → 人工看高风险/低置信 → 回写 quality_score`。

## 9. 数据配比

YAML 管 mixture（源 token 数、domain 权重、阶段配比、变更原因、与 eval 关系）。第一版按任务分两条线：

- `sft_general_v1`：通用指令数据。
- `agent_tool_v1`：工具调用 / function-calling 数据。
- `preference_v1`：偏好对数据，用于 DPO/GRPO。

后续再做多组对照，例如 general:agent = 100:0 / 70:30 / 50:50。

## 10. Tokenizer

- Qwen3 小参数基座：复用官方 tokenizer，不重新训练。
- SFT/LoRA/DPO/GRPO：始终保持 tokenizer 不变。
- 只有将来增加“从零训练小模型”实验线时，才单独训练 BPE tokenizer。

seq_len：第一版 SFT/LoRA 建议 1024-2048；如果显存紧张，优先降低 `cutoff_len` 和 batch，再用 gradient accumulation 补有效 batch。

## 11. 模型架构与训练（核心）

### 架构

本项目主线使用 Qwen3 小参数 dense decoder-only causal LM。架构由 Hugging Face Transformers / LLaMA-Factory 直接加载，不在仓库内维护手写模型层。

推荐基座：

| 阶段 | 模型 | 用途 |
| --- | --- | --- |
| MVP | `Qwen/Qwen3-0.6B` | 3090 / 低成本租卡首选，跑通数据治理、SFT、LoRA、评测闭环 |
| 进阶 | `Qwen/Qwen3-1.7B` | 更好的效果体感，适合 LoRA/QLoRA 和小规模 DPO |
| 扩展 | `Qwen/Qwen3-4B` | 需要更高显存或租卡，作为后续实验 |

Qwen3 dense 主线可以理解为稳定的现代 decoder-only Transformer：RoPE / RMSNorm / GQA / SwiGLU 或 SiLU gated MLP / causal attention。项目不碰 Qwen3 MoE、Qwen3-Next、超长上下文和多模态，先把稳定小模型流水线做扎实。

### 四阶段训练

| 阶段 | 目的 | 工业组件 | 数据 |
| --- | --- | --- | --- |
| ① SFT | 指令对齐，会按格式回答 | LLaMA-Factory / TRL | SFT 指令数据 |
| ② LoRA/QLoRA | 低成本适配领域/agent 能力 | LLaMA-Factory + PEFT | 领域/agent 合成数据 |
| ③ DPO/GRPO | 偏好对齐或强化 agent 行为 | TRL / LLaMA-Factory | 偏好/agent 轨迹数据 |
| ④ 可选继续预训练 | 增强领域语料吸收 | TRL/Transformers + DeepSpeed | 通用/领域文本 mixture |

分布式：先单卡 → `torchrun --nproc_per_node=1` 保入口 → 租 2-4 卡开 DeepSpeed ZeRO-2/3 → K8s 用 Job/MPIJob。

## 12. Checkpoint 与模型注册

区分训练 ckpt（含 optimizer/scheduler/rng，续训用）vs 发布权重（safetensors）vs 模型注册。LoRA/QLoRA 阶段优先保存 adapter；需要独立部署时再 merge 到 base model。DeepSpeed ZeRO ckpt 用 `zero_to_fp32.py` 聚合 → HF 格式 → safetensors。模型版本必记：`model_version / base_model / tokenizer_version / data_mixture / cleaning_recipe / training_config / stage / git_commit / docker_image / checkpoint_path / eval_report_path`。

## 13. 测评与闭环

补齐三层测评 + eval 闭环，且每个训练阶段（SFT/LoRA/DPO/GRPO）都产出 eval_report，前后可对比。

三层：Smoke eval（几十题确认没坏）→ Standard（MMLU/C-Eval/CMMLU/GSM8K/HumanEval 子集）→ Business（自有任务/badcase/agent 任务）。Qwen3 小模型先测指令遵循、格式稳定性、工具调用成功率和自定义业务集。闭环：`eval badcase → 标原因 → 归 domain → 调 cleaning/mixture/agent 数据 → 重训 → 重 eval`。

## 14. 实验矩阵

| 组 | 目的 | 配置 |
| --- | --- | --- |
| 1 | 学 SFT | `Qwen/Qwen3-0.6B` → 通用 SFT 小切片 → 指令 eval |
| 2 | 学 LoRA/QLoRA | `Qwen/Qwen3-0.6B` → agent/tool 数据 → 工具调用 eval |
| 3 | 学偏好对齐 | LoRA 结果 → DPO/GRPO 小数据 → 偏好/格式 eval |
| 4 | 学数据配比 | general:agent = 100:0 / 70:30 / 50:50 → 同配置对比 eval |

## 15. MVP 里程碑

| 里程碑 | 内容 | 验收 |
| --- | --- | --- |
| M1 | 数据湖 + 元数据（MinIO/PostgreSQL/manifest） | 能列版本、读回数据、hash 一致 |
| M2 | Schema + 清洗（Pydantic/JSON Schema/Data-Juicer） | 每条通过校验，出清洗前后报告 |
| M3 | 人工审核（Label Studio/Argilla） | 能导入抽样、标 pass/reject/reason、导出 badcase |
| M4 | 配比 + Tokenize（mixture.yaml、Qwen3 tokenizer、tokenized shards） | 可复现 mixture，token 统计稳定 |
| M5 | Qwen3 SFT（LLaMA-Factory/TRL，adapter + MLflow） | loss 正常下降，输出格式更稳 |
| M6 | Agent LoRA（工具调用/function-calling 数据） | 工具调用成功率可测 |
| M7 | K8s Workflow（clean/tokenize/eval Job） | Argo 一条链路跑通，产物写 MinIO |
| M8 | 租卡分布式（DeepSpeed ZeRO-2/3） | 可恢复训练、可导出 adapter/权重、可评测 |

## 16. 优先级与取舍

**优先级**

- P1：MinIO、PostgreSQL、HF Datasets、Pydantic/JSON Schema、Data-Juicer、MLflow、LLaMA-Factory/TRL、PEFT、lm-eval。
- P2：Argo Workflows、Label Studio/Argilla、lakeFS、Ray Data、DeepSpeed。
- P3：Kubeflow Training Operator、Kueue/Volcano、OpenCompass、vLLM。

**取舍**

- 架构：固定 Qwen3 小参数 dense 基座，不纠结、不魔改。
- 是否从零训练：第一阶段不做；后续可作为独立实验线。
- 是否训练 tokenizer：不训练，复用 Qwen3 官方 tokenizer。
- 是否上 K8s：不急。路径 `Python CLI → Docker → Compose → K3s/Argo → Kubeflow`。
- agent vs 纯聊天：先 SFT 通用指令，再 LoRA/QLoRA 做 agent。
- RL 形式：先 DPO（简单稳定），再尝试 GRPO（agent 轨迹强化）。

## 17. 最终目标

不比别人模型强，但能系统回答：数据来自哪 → 经哪些清洗 → 符合哪个 schema → 哪些被过滤及为何 → 用了哪个配比 → tokenizer 哪版 → base model 哪版 → 处于哪个训练阶段(stage) → ckpt/adapter 能否恢复 → 权重如何导出 → eval 如何 → 下轮怎么改。

这些问题都能被系统回答时，项目就具备了工业训练流水线的骨架：一个围绕 Qwen3 小参数基座、可由个人开发者低成本实操的模型训练平台。
