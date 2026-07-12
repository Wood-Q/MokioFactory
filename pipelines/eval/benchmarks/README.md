# Stage 1 外部 Benchmark

本目录接入三类互补测评。小切片用于低成本验证流水线和比较 base/adapter，不等同于官方完整榜单成绩。

| Benchmark | 本阶段切片 | 测什么 | 主要指标 |
| --- | ---: | --- | --- |
| BFCL v4 | 5 类共 25 条 | 单次、顺序、并行、混合调用与拒绝无关工具 | 官方 AST accuracy |
| EvalPlus | HumanEval+、MBPP+ 各取 ID `[0, 20)` | 生成代码能否通过基础及增强隐藏测试 | base/plus `pass@1` |
| τ³-bench | retail test split 5 条 | 多轮对话、政策遵循、真实工具执行与最终数据库状态 | task reward、`pass^1` |

原始 `tau-bench` 仓库已被官方标记为任务过期。本项目使用其后继仓库 `tau2-bench` 的 τ³-bench `v1.0.0` 修复版，但能力层仍简称 τ-bench。

## 为什么隔离环境

三个官方项目对 Python、Transformers 和推理后端的约束不同，不应塞进 MokioFactory 主环境：

```text
MokioFactory .venv     读取 YAML、编排命令
BFCL env               官方生成与 AST evaluator
EvalPlus env           官方数据、代码生成与 evaluator
τ³ source/.venv        官方 Agent、用户模拟器和 retail 环境
vLLM env               只负责提供 OpenAI-compatible 推理服务
Docker                 隔离执行模型生成的 Python 代码
```

配置锁定了三个官方 Git commit。源码和环境写入 `artifacts/benchmarks/`，结果写入 `outputs/benchmarks/`，都不提交 Git。

## 安装

先安装 [uv](https://docs.astral.sh/uv/)，再按需创建独立环境：

```bash
.venv/bin/python -m pipelines.eval.benchmarks.setup_benchmarks \
  --benchmark bfcl

.venv/bin/python -m pipelines.eval.benchmarks.setup_benchmarks \
  --benchmark evalplus

.venv/bin/python -m pipelines.eval.benchmarks.setup_benchmarks \
  --benchmark tau3
```

EvalPlus 默认在 Docker 中执行不可信代码。镜像必须从锁定的源码构建，避免 `latest` 与 evaluator 版本漂移：

```bash
docker build \
  -t mokio/evalplus:26d6d00 \
  artifacts/benchmarks/src/evalplus
```

安装器可重复执行，并在 `artifacts/benchmarks/setup_manifest.json` 记录仓库、revision、源码和环境路径。τ³ 使用 sparse checkout 且跳过 Git LFS 语音资源，本阶段只下载文本 retail 所需内容。

τ³ `v1.0.0` 的文本 CLI 在模块初始化时仍会导入 voice 包和 banking knowledge registry。配置因此安装官方 `voice`、`knowledge` extras，但 sparse checkout 不下载这些领域的数据，本阶段也不会运行语音或 banking 任务。Ubuntu/Debian 需要先安装 PyAudio 编译依赖：

```bash
sudo apt-get install -y portaudio19-dev
```

## 启动 vLLM

以下命令在 NVIDIA 算力机执行。每个服务独占一张 GPU，并在前台运行；另开终端执行 benchmark。

Base 服务：

```bash
SERVED_MODEL_NAME=qwen3-base \
MAX_MODEL_LEN=16384 \
MAX_NUM_SEQS=8 \
  bash pipelines/eval/benchmarks/serve_vllm.sh base 8100 2
```

Adapter 服务：

```bash
SERVED_MODEL_NAME=qwen3-base \
LORA_NAME=qwen3-agent \
MAX_MODEL_LEN=16384 \
MAX_NUM_SEQS=8 \
  bash pipelines/eval/benchmarks/serve_vllm.sh adapter 8101 3
```

τ³ 的用户模拟器必须固定，不随被测 agent 变化：

```bash
SERVED_MODEL_NAME=qwen3-user \
MAX_MODEL_LEN=16384 \
MAX_NUM_SEQS=8 \
  bash pipelines/eval/benchmarks/serve_vllm.sh base 8102 4
```

τ³ 的 retail policy、工具 schema 和多轮历史会超过 8192 tokens，agent 与 user 两个服务都应设置 `MAX_MODEL_LEN=16384 MAX_NUM_SEQS=8`。BFCL 和 EvalPlus 小切片继续使用默认 8192 即可。

服务使用 `temperature=0` 的调用方配置、Qwen/Hermes tool parser 和动态 LoRA。base 与 adapter 共用同一基座、tokenizer 和上下文长度。

## BFCL

BFCL 的 Qwen handler调用 `/v1/completions`，并用本地模型路径作为请求中的 model 名。启动服务时需要让 endpoint 暴露同名模型。

Base 服务：

```bash
MODEL_PATH=/home/qhk/models/Qwen3-4B-Instruct-2507 \
SERVED_MODEL_NAME=/home/qhk/models/Qwen3-4B-Instruct-2507 \
  bash pipelines/eval/benchmarks/serve_vllm.sh base 8110 2
```

Adapter 服务中，把 LoRA alias 设置为 BFCL 请求的模型路径：

```bash
MODEL_PATH=/home/qhk/models/Qwen3-4B-Instruct-2507 \
SERVED_MODEL_NAME=qwen3-base \
LORA_NAME=/home/qhk/models/Qwen3-4B-Instruct-2507 \
  bash pipelines/eval/benchmarks/serve_vllm.sh adapter 8111 3
```

分别运行官方生成器和 evaluator：

```bash
.venv/bin/python -m pipelines.eval.benchmarks.run_benchmark bfcl \
  --variant base \
  --base-url http://127.0.0.1:8110 \
  --model-path /home/qhk/models/Qwen3-4B-Instruct-2507 \
  --overwrite

.venv/bin/python -m pipelines.eval.benchmarks.run_benchmark bfcl \
  --variant adapter \
  --base-url http://127.0.0.1:8111 \
  --model-path /home/qhk/models/Qwen3-4B-Instruct-2507 \
  --overwrite
```

结果位于 `outputs/benchmarks/bfcl/<variant>/result` 和 `score`。项目只选择官方 ID，未复制或修改 BFCL 测试和判分代码。

## EvalPlus

EvalPlus 先通过 vLLM endpoint 生成代码，再把 JSONL 挂载进无网络、限 CPU/内存/PID 的容器运行官方测试：

```bash
.venv/bin/python -m pipelines.eval.benchmarks.run_benchmark evalplus \
  --variant base \
  --model-name qwen3-base \
  --base-url http://127.0.0.1:8100 \
  --overwrite

.venv/bin/python -m pipelines.eval.benchmarks.run_benchmark evalplus \
  --variant adapter \
  --model-name qwen3-agent \
  --base-url http://127.0.0.1:8101 \
  --overwrite
```

主要比较 `base pass@1` 与 `plus pass@1`。plus 测试远多于原始测试，两者差距可以暴露只记住常见样例、边界条件不稳的问题。`--evalplus-execution local` 只用于可信 smoke；默认 Docker 执行，模型生成代码不得直接在训练机主环境运行。

## τ³-bench

τ³ 不是 input/output 精确匹配。Agent 需要在 retail 政策约束下与用户多轮沟通、查询订单、调用会改变数据库的工具，最后按动作、信息传达和数据库状态评分。

```bash
.venv/bin/python -m pipelines.eval.benchmarks.run_benchmark tau3 \
  --variant base \
  --model-name qwen3-base \
  --base-url http://127.0.0.1:8100 \
  --user-model-name qwen3-user \
  --user-base-url http://127.0.0.1:8102 \
  --overwrite

.venv/bin/python -m pipelines.eval.benchmarks.run_benchmark tau3 \
  --variant adapter \
  --model-name qwen3-agent \
  --base-url http://127.0.0.1:8101 \
  --user-model-name qwen3-user \
  --user-base-url http://127.0.0.1:8102 \
  --overwrite
```

两组必须使用同一个 user endpoint、task IDs、seed 和最大步数。使用本地 Qwen3-4B 作为用户模拟器适合内部 A/B，但与官方榜单使用的用户模型不同，因此不能直接横向比较 leaderboard 成绩。

## 进入正式评测前

小切片跑通后再扩大规模，并遵守以下门禁：

1. 记录 benchmark commit、模型和 adapter hash、prompt/template、生成参数与容器镜像。
2. 保存原始响应、完整轨迹、执行日志和聚合指标，不只保存最终分数。
3. benchmark 测试样本不得回填训练集；错误分析只能转化为新规则或独立构造的数据。
4. base/adapter 必须由同一运行器生成，除 adapter 外不改变任何实验变量。
