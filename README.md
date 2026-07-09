# MokioFactory

个人开发者在游戏本 / 低成本租卡环境里，完整跑通工业界 LLM 训练流水线的练手项目。完整计划见 [plan_concise.md](plan_concise.md)。

---

## 本次 Commit 概述：Phase 1 · 数据部分（HF 数据集调研 / 下载 / 存储）

### 目标

完成 Phase 1 数据链路的第一步：从 HuggingFace 选定并下载小而真实的数据集，按工业落湖结构存入对象存储，并生成可追溯的 manifest。本 commit **只做数据采集与落盘**，不做清洗 / Schema / 训练。

### 范围

1. **数据集调研与选型**
   - 按预训练 / SFT / Code 三类，列出候选数据集及其规模、语言、license、用途。
   - 第一版优先小切片，能在游戏本本地跑通即可，不追求量。
2. **下载**
   - 用 `huggingface_datasets` 拉取选定数据集的小切片。
   - 下载脚本可重跑、可指定切片大小，支持断点续传 / 缓存。
3. **存储落湖**
   - 数据写入 raw 层，Hive 风格分区路径：
     `s3://mokio-lake/raw/source=hf/dataset=<name>/date=<YYYY-MM-DD>/`
   - 存储后端：MinIO（S3 兼容）；本 commit 搭起 MinIO 并写入。
   - raw 数据不可变，后续只生成 bronze / silver / gold 新版本。
4. **Manifest 与元数据**
   - 每个数据集生成 `manifest.json`：来源、版本、切片、样本数、字节数、sha256。
   - 结构化元数据（数据集版本、文件清单）登记到 PostgreSQL。

### 候选数据集（当前选型）

本阶段先聚焦 **Agent / Tool Calling / Code**，目标是围绕 `Qwen3-4B` 做小规模高质量 SFT，优先选择新近、质量较高、Hugging Face 上近期下载量较高的数据集。

> 元数据查询时间：2026-07-09；`downloads` 取 Hugging Face API 返回值，可近似理解为近期下载热度。

| 优先级 | 数据集 | 方向 | downloads | license | 最近更新 | 第一版切片建议 | 用途 |
|---|---|---:|---:|---|---|---:|---|
| P0 | `Salesforce/xlam-function-calling-60k` | Tool Calling | 16,280 | `cc-by-4.0` | 2025-01-24 | 5k-20k | 训练函数调用、参数生成、工具格式稳定性 |
| P0 | `Salesforce/APIGen-MT-5k` | Multi-turn Agent | 1,784 | `cc-by-nc-4.0` | 2025-10-10 | 3k-5k | 强化多轮 user-agent-tool 交互和任务执行 |
| P0 | `open-thoughts/OpenThoughts-Agent-v1-SFT` | Agent / Terminal / Code | 8,124 | `apache-2.0` | 2026-01-27 | 5k-20k | 强化终端、代码、软件工程类 Agent 轨迹 |
| P0 | `nvidia/OpenCodeInstruct` | Code SFT | 11,281 | `cc-by-4.0` | 2025-04-28 | 5k-30k | 强化代码生成、代码解释、代码指令跟随 |
| P1 | `Glint-Research/Fable-5-traces` | Code Project Agent | 64,153 | `agpl-3.0` | 2026-06-29 | 1k-5k | 强化项目级代码修改、轨迹式软件工程能力 |

当前判断：

- 这组数据集比原来的 General / Codeforces 方向更贴合项目目标：不是泛泛训练代码题，而是让模型更会 **调用工具、执行任务、读写代码项目**。
- `Salesforce/xlam-function-calling-60k` 和 `APIGen-MT-5k` 适合作为 Tool/Agent 基础盘，前者偏单轮函数调用，后者偏多轮任务轨迹。
- `OpenThoughts-Agent-v1-SFT` 和 `Fable-5-traces` 更接近真实 Agent / coding agent 行为，适合放在第二批或混合训练里。
- `OpenCodeInstruct` 数据量大，第一版不要全量下载，先抽 5k-30k 做 schema、清洗、tokenize、SFT 和 eval 闭环。
- `Salesforce/xlam-function-calling-60k` 是 gated dataset，需要先在 Hugging Face 页面申请访问并配置 `HF_TOKEN`，否则匿名下载会返回 401。
- 注意 license：`APIGen-MT-5k` 是 `cc-by-nc-4.0`，`Fable-5-traces` 是 `agpl-3.0`，适合学习研究；如果未来做商业用途，需要重新审查 license。

第一版建议配比：

```text
Tool Calling:        35%  Salesforce/xlam-function-calling-60k
Multi-turn Agent:    20%  Salesforce/APIGen-MT-5k
Agent/Terminal/Code: 20%  OpenThoughts-Agent-v1-SFT + Fable-5-traces
Code SFT:            25%  nvidia/OpenCodeInstruct
```

第一版不追求量，先用 20k-50k 条高质量样本跑通：

```text
HF download -> MinIO raw -> PostgreSQL metadata -> schema normalize -> clean -> mix -> tokenize -> Qwen3-4B QLoRA SFT -> eval
```

### 产物

- 数据集调研记录（候选 + 选型理由）
- 下载脚本 + MinIO 部署配置
- raw 层数据 + 每数据集 `manifest.json`
- PostgreSQL 中的数据集版本登记

### 验收

- 能列出已下载数据集及其版本。
- 能从 MinIO 读回完整数据，hash 与 manifest 一致。
- 下载脚本可重跑，不重复下载、不破坏已有 raw 数据。

---

## 需要学习的知识和组件

本节是做这个 commit 前需要建立的概念，每块给「理论 + 实践」。

### 数据集

- **理论**：数据集 = 一批结构化样本的集合。LLM 训练里三类形态：预训练 `{text}`、SFT `{messages:[{role,content}]}`、偏好 `{prompt,chosen,rejected}`。数据集是有版本的——来源、切片、清洗规则任一变化都应视为新版本，否则无法复现和对比实验。
- **实践**：第一版选**小而真实**的切片（能在游戏本跑通即可，不追求量）；每个数据集记录来源、license、规模、用途；raw 层只存原始下载，不在原地改。

### huggingface_datasets

- **理论**：HuggingFace Hub 是模型/数据集托管平台；`datasets` 库提供加载、流式读取、批处理、持久化能力。核心抽象是 `Dataset` / `DatasetDict`，底层 Apache Arrow 列式存储，支持零拷贝和惰性 `map`。它是本项目的数据入口——下载、切片、转格式都靠它。
- **实践**：`load_dataset(name, split, streaming=True)` 流式拉取大集；用 `split` / `select` / `shuffle(seed).select(range(n))` 取小切片；`dataset.map(fn, num_proc=N)` 并行处理；`save_to_disk` 存 Arrow，或 `to_json` / `to_parquet` 导出通用格式；用 `revision` 钉版本保证可复现；注意默认缓存目录的清理。

### 对象存储和 MinIO

- **理论**：对象存储把数据组织成「桶(bucket) + 键(key) → 对象(blob + 元数据)」的扁平命名空间，而非文件系统的树形目录；通过 HTTP/S3 API 访问，天然适合海量不可变文件，是数据湖的事实标准。S3 API 是业界通用接口，学一次到处可用。MinIO 是自托管、S3 兼容的对象存储服务，本地起一个等于拥有一个"私有 AWS S3"。
- **实践**：本地用 Docker 起一个 MinIO；建 bucket（如 `mokio-lake`）；用 `boto3` / `minio` 客户端做 `put_object` / `get_object` / `list_objects`；key 用 Hive 分区路径（见下）。选它的原因：便宜、本地可控、API 与真 S3 一致，将来迁云零改动。

### 数据湖分层与 Hive 分区

- **理论**：数据不可变原则下，按"质量阶段"分层落盘：`raw`（原始下载）→ `bronze`（符合 schema）→ `silver`（清洗后）→ `gold`（按配比混合、可直接训练）。每层只读上一层产物生成新版本，不在原地改。Hive 分区把分类维度编码进路径：`raw/source=hf/dataset=tinystories/date=2026-07-08/`，这样按来源/日期筛选就是按前缀列目录，无需额外索引。
- **实践**：本 commit 只建 raw 层；路径严格按 `source=/dataset=/date=` 分区；bronze/silver/gold 在后续 commit 各自加，不回改 raw。

### manifest 与数据可追溯

- **理论**：manifest = 每个数据集版本的索引文件，记录来源、schema 版本、切片、样本数、字节数、每文件 sha256。它是"数据指纹"——校验读回数据没坏、判断是否需重下、追溯某条样本来源。lineage（血缘）= 从数据到模型的可追溯链路，工业训练的命门。
- **实践**：本 commit 每次写 raw 同步生成 `manifest.json`；读回时算 sha256 与 manifest 比对；manifest 内容登记到 PostgreSQL。

### PostgreSQL（元数据存储）

- **理论**：对象存储擅长放"大而笨"的文件，但不擅长查询"有哪些数据集版本、各自状态、哪个 manifest 指向哪条 key"。结构化元数据（版本、任务状态、manifest 索引、eval 结果）放关系数据库，与对象存储互补，不二选一。
- **实践**：本地 Docker 起一个 PostgreSQL；建表登记数据集版本（name / version / source / manifest_path / status / created_at）；本 commit 只做"登记"，不做复杂查询。

### Docker（服务编排）

- **理论**：本 commit 要起的 MinIO 和 PostgreSQL 都是服务进程，直接装在本机不易复现、不易切换版本。Docker 把服务连同依赖打成镜像，`docker-compose.yml` 声明式描述"起哪些服务、挂哪些卷、开哪些端口"，一行命令拉起整套环境，且与队友/CI 环境一致。
- **实践**：写一个 `docker-compose.yml` 同时定义 MinIO + PostgreSQL，数据卷持久化；`docker compose up -d` 起服务；凭据走 `.env`，不入库不入镜像。

### 模型架构（Qwen3 小参数基座）

- **理论**：本项目不再维护仓库内手写 Transformer 架构，而是直接复用 Qwen3 小参数基座模型。Qwen3 dense 主线属于稳定的 decoder-only causal LM：RMSNorm / RoPE / GQA / SwiGLU/SiLU MLP / causal attention。项目重点从“造一个模型结构”调整为“围绕真实开源基座模型跑通工业数据治理、微调、评测和闭环”。
- **实践**：第一版推荐 `Qwen/Qwen3-0.6B`，第二阶段可升级到 `Qwen/Qwen3-1.7B`。通过 Hugging Face Transformers、TRL、PEFT 或 LLaMA-Factory 加载官方架构和 tokenizer，进行 SFT / LoRA / DPO 等训练；不重新训练 tokenizer，不在仓库内手写模型层。
