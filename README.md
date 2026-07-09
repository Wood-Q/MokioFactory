# MokioFactory

个人开发者在游戏本 / 低成本租卡环境里，完整跑通工业界 LLM 训练流水线的练手项目。完整计划见 [plan_concise.md](plan_concise.md)。

---

## 本次 Commit 概述：Stage 1 · Phase 1 · 数据部分（Schema 转换 / 基础清洗）

### 目标

在已经完成 Hugging Face 数据下载、MinIO raw 落湖、PostgreSQL 元数据登记之后，继续推进 Stage 1 · Phase 1 的下一步：把 raw 层不同来源、不同字段结构的数据统一转换成 `sft.v1` schema，并生成 bronze / silver 两层数据。本 commit **只做 schema 转换与基础清洗**，不进入训练。

### 范围

1. **统一 SFT Schema**
   - 新增 `schemas/sft.schema.json`。
   - 统一字段：`id / schema_version / source_dataset / task_family / domain / messages / tools / quality_score / meta`。
   - 第一版以 `messages` 为核心格式，兼容 tool calling、agent trace、code SFT 数据。
2. **清洗配置**
   - 新增 `configs/cleaning/stage1_phase1_sft_cleaning.yaml`。
   - 声明 raw 输入前缀、bronze/silver 输出前缀、过滤阈值、文本规范化规则。
3. **raw -> bronze**
   - 从 MinIO raw 层读取 JSONL shard。
   - 按来源数据集适配字段结构，转成 `sft.v1`。
   - 生成 `bronze/schema=sft.v1/date=2026-07-09/`。
4. **bronze -> silver**
   - 删除空消息、缺少 assistant 回复、超长样本。
   - 清理控制字符、折叠多余空白。
   - 按 messages 文本做基础去重。
   - 生成 `silver/schema=sft.v1/date=2026-07-09/`。
5. **Manifest**
   - bronze / silver 各自生成 `manifest.json`。
   - 记录样本数、字节数、sha256、清洗统计、输入 raw 文件列表。

### 已下载数据集

Stage 1 · Phase 1 已完成以下 Agent / Tool Calling / Code raw 数据下载，本 commit 继续把它们统一转换为 `sft.v1`。

> 元数据查询时间：2026-07-09；`downloads` 取 Hugging Face API 返回值，可近似理解为近期下载热度。

| 优先级 | 数据集 | 方向 | downloads | license | 最近更新 | 已下载切片 | 用途 |
|---|---|---:|---:|---|---|---:|---|
| P0 | `Salesforce/xlam-function-calling-60k` | Tool Calling | 16,280 | `cc-by-4.0` | 2025-01-24 | 5k | 训练函数调用、参数生成、工具格式稳定性 |
| P0 | `Salesforce/APIGen-MT-5k` | Multi-turn Agent | 1,784 | `cc-by-nc-4.0` | 2025-10-10 | 3k | 强化多轮 user-agent-tool 交互和任务执行 |
| P0 | `open-thoughts/OpenThoughts-Agent-v1-SFT` | Agent / Terminal / Code | 8,124 | `apache-2.0` | 2026-01-27 | 1k | 强化终端、代码、软件工程类 Agent 轨迹 |
| P0 | `nvidia/OpenCodeInstruct` | Code SFT | 11,281 | `cc-by-4.0` | 2025-04-28 | 1k | 强化代码生成、代码解释、代码指令跟随 |
| P1 | `Glint-Research/Fable-5-traces` | Code Project Agent | 64,153 | `agpl-3.0` | 2026-06-29 | 500 | 强化项目级代码修改、轨迹式软件工程能力 |

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

当前数据链路：

```text
HF download -> MinIO raw -> PostgreSQL metadata -> schema normalize -> bronze -> clean/dedup -> silver
```

后续继续进入：

```text
silver -> gold mixture -> tokenize -> Qwen3-4B QLoRA SFT -> eval
```

### 产物

- `schemas/sft.schema.json`
- `configs/cleaning/stage1_phase1_sft_cleaning.yaml`
- `pipelines/clean/normalize_sft.py`
- `pipelines/clean/README.md`
- bronze / silver 层 JSONL shard 与 `manifest.json`

### 验收

- 本地能通过脚本语法检查。
- `sft.schema.json` 和 cleaning YAML 能被正确解析。
- 典型 raw 样本能转成 `sft.v1` record。
- 在 MinIO 可访问环境下，能运行：
  ```bash
  .venv/bin/python pipelines/clean/normalize_sft.py \
    --config configs/cleaning/stage1_phase1_sft_cleaning.yaml
  ```
- 能在 MinIO 中看到：
  ```text
  s3://mokio-lake/bronze/schema=sft.v1/date=2026-07-09/
  s3://mokio-lake/silver/schema=sft.v1/date=2026-07-09/
  ```

---

## 需要学习的知识和组件

本节是做这个 commit 前需要建立的概念，每块给「理论 + 实践」。

### 数据集

- **理论**：数据集 = 一批结构化样本的集合。LLM 训练里三类形态：预训练 `{text}`、SFT `{messages:[{role,content}]}`、偏好 `{prompt,chosen,rejected}`。数据集是有版本的——来源、切片、清洗规则任一变化都应视为新版本，否则无法复现和对比实验。
- **实践**：第一版选**小而真实**的切片（能在游戏本跑通即可，不追求量）；每个数据集记录来源、license、规模、用途；raw 层只存原始下载，不在原地改；后续所有加工都写入 bronze / silver / gold。

### huggingface_datasets

- **理论**：HuggingFace Hub 是模型/数据集托管平台；`datasets` 库提供加载、流式读取、批处理、持久化能力。核心抽象是 `Dataset` / `DatasetDict`，底层 Apache Arrow 列式存储，支持零拷贝和惰性 `map`。它是本项目的数据入口——下载、切片、转格式都靠它。
- **实践**：`load_dataset(name, split, streaming=True)` 流式拉取大集；用 `split` / `select` / `shuffle(seed).select(range(n))` 取小切片；`dataset.map(fn, num_proc=N)` 并行处理；`save_to_disk` 存 Arrow，或 `to_json` / `to_parquet` 导出通用格式；用 `revision` 钉版本保证可复现；注意默认缓存目录的清理。

### 对象存储和 MinIO

- **理论**：对象存储把数据组织成「桶(bucket) + 键(key) → 对象(blob + 元数据)」的扁平命名空间，而非文件系统的树形目录；通过 HTTP/S3 API 访问，天然适合海量不可变文件，是数据湖的事实标准。S3 API 是业界通用接口，学一次到处可用。MinIO 是自托管、S3 兼容的对象存储服务，本地起一个等于拥有一个"私有 AWS S3"。
- **实践**：本地用 Docker 起一个 MinIO；建 bucket（如 `mokio-lake`）；用 `boto3` / `minio` 客户端做 `put_object` / `get_object` / `list_objects`；key 用 Hive 分区路径（见下）。选它的原因：便宜、本地可控、API 与真 S3 一致，将来迁云零改动。

### 数据湖分层与 Hive 分区

- **理论**：数据不可变原则下，按"质量阶段"分层落盘：`raw`（原始下载）→ `bronze`（符合 schema）→ `silver`（清洗后）→ `gold`（按配比混合、可直接训练）。每层只读上一层产物生成新版本，不在原地改。Hive 分区把分类维度编码进路径：`raw/source=hf/dataset=tinystories/date=2026-07-08/`，这样按来源/日期筛选就是按前缀列目录，无需额外索引。
- **实践**：前序 commit 已建 raw 层；本 commit 新增 bronze / silver 层。路径严格按 `source=/dataset=/date=` 或 `schema=/date=` 分区；后续 gold 会按 mixture 配比生成，不回改 raw。

### manifest 与数据可追溯

- **理论**：manifest = 每个数据集版本的索引文件，记录来源、schema 版本、切片、样本数、字节数、每文件 sha256。它是"数据指纹"——校验读回数据没坏、判断是否需重下、追溯某条样本来源。lineage（血缘）= 从数据到模型的可追溯链路，工业训练的命门。
- **实践**：raw / bronze / silver 每层都同步生成 `manifest.json`；读回时算 sha256 与 manifest 比对；manifest 记录输入文件列表与清洗统计。

### Schema 转换与清洗

- **理论**：不同来源数据集字段结构不同，不能直接混在一起训练。工业数据流水线通常先定义目标 schema，再把各来源数据适配到统一格式，之后再做过滤、去重、质量打分和配比。
- **实践**：本 commit 定义 `sft.v1`，把 tool calling / multi-turn agent / code SFT / coding trace 数据统一成 `messages` 格式。基础清洗包括空内容过滤、assistant 回复检查、超长过滤、文本规范化和按 messages 文本去重。

### PostgreSQL（元数据存储）

- **理论**：对象存储擅长放"大而笨"的文件，但不擅长查询"有哪些数据集版本、各自状态、哪个 manifest 指向哪条 key"。结构化元数据（版本、任务状态、manifest 索引、eval 结果）放关系数据库，与对象存储互补，不二选一。
- **实践**：本地 Docker 起一个 PostgreSQL；建表登记数据集版本（name / version / source / manifest_path / status / created_at）；本 commit 只做"登记"，不做复杂查询。

### Docker（服务编排）

- **理论**：本 commit 要起的 MinIO 和 PostgreSQL 都是服务进程，直接装在本机不易复现、不易切换版本。Docker 把服务连同依赖打成镜像，`docker-compose.yml` 声明式描述"起哪些服务、挂哪些卷、开哪些端口"，一行命令拉起整套环境，且与队友/CI 环境一致。
- **实践**：写一个 `docker-compose.yml` 同时定义 MinIO + PostgreSQL，数据卷持久化；`docker compose up -d` 起服务；凭据走 `.env`，不入库不入镜像。

### 模型架构（Qwen3 小参数基座）

- **理论**：本项目不再维护仓库内手写 Transformer 架构，而是直接复用 Qwen3 小参数基座模型。Qwen3 dense 主线属于稳定的 decoder-only causal LM：RMSNorm / RoPE / GQA / SwiGLU/SiLU MLP / causal attention。项目重点从“造一个模型结构”调整为“围绕真实开源基座模型跑通工业数据治理、微调、评测和闭环”。
- **实践**：第一版推荐 `Qwen/Qwen3-0.6B`，第二阶段可升级到 `Qwen/Qwen3-1.7B`。通过 Hugging Face Transformers、TRL、PEFT 或 LLaMA-Factory 加载官方架构和 tokenizer，进行 SFT / LoRA / DPO 等训练；不重新训练 tokenizer，不在仓库内手写模型层。
