# ingest pipeline

本目录放数据采集与下载入口。第一版目标是跑通：

```text
Hugging Face -> JSONL shard -> MinIO raw 层 -> manifest.json -> PostgreSQL metadata
```

## 前置服务

先启动 MinIO 和 PostgreSQL：

```bash
cd /Users/mokio/Project/MokioFactory/deploy/MinIO
docker compose up -d

cd /Users/mokio/Project/MokioFactory/deploy/PostgreSQL
docker compose up -d
```

确认连接：

```bash
mc admin info mokio
docker compose -f deploy/PostgreSQL/docker-compose.yml ps
```

## 安装依赖

```bash
pip install -e .
```

如果当前环境不支持 editable install，也可以先直接安装必要依赖：

```bash
pip install boto3 datasets pyyaml sqlalchemy pg8000
```

## `from datasets import load_dataset` 是什么

`datasets` 是 Hugging Face 提供的数据集工具库，专门用来下载、缓存、读取和处理 Hugging Face Hub 上的数据集。我们在下载脚本里写：

```python
from datasets import load_dataset
```

意思是从 `datasets` 这个库里导入 `load_dataset` 函数。后续脚本就可以通过它拉取 Hugging Face 上的数据集：

```python
dataset = load_dataset(
    "minpeter/xlam-function-calling-60k-parsed",
    split="train",
    revision="main",
    streaming=True,
)
```

几个关键参数：

- `dataset_id`：Hugging Face 上的数据集名称，例如 `minpeter/xlam-function-calling-60k-parsed`。
- `split`：读取哪个切分，常见有 `train`、`validation`、`test`。
- `revision`：读取哪个版本，常见是 `main`，也可以指定 commit hash / tag，方便保证数据可复现。
- `streaming=True`：边下载边读取，不需要一次性把完整数据集落到本地，适合游戏本和小样本实验。
- `streaming=False`：先把数据集下载并缓存到本地，再从本地读取，适合数据量不大、希望重复实验更快的场景。

本项目第一版使用 `load_dataset` 的方式是：

```text
Hugging Face dataset -> Python iterator -> JSONL shard -> MinIO raw -> PostgreSQL metadata
```

也就是说，`load_dataset` 只负责“把 Hugging Face 数据读出来”。读出来之后，我们自己的 ingest 脚本会继续做三件事：

- 把样本逐行写成 `.jsonl` 分片。
- 把 `.jsonl` 和 `manifest.json` 上传到 MinIO。
- 把数据集版本、样本数、文件路径、hash 等元数据写入 PostgreSQL。

它和 `git clone` / 直接下载文件不太一样：`load_dataset` 会理解 Hugging Face 数据集仓库的格式，自动处理常见的 `json`、`jsonl`、`parquet`、`csv` 等数据文件，并统一返回可以遍历的数据记录。

## 配置 Hugging Face 镜像

如果直接访问 Hugging Face 较慢或失败，可以临时使用 `hf-mirror`：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

然后再执行下载脚本：

```bash
python pipelines/ingest/download_hf_dataset.py \
  --config configs/datasets/xlam_function_calling_60k_sample.yaml
```

如果希望每次打开终端都自动生效，可以写入当前 shell 配置。`zsh` 用户一般写入：

```bash
echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.zshrc
source ~/.zshrc
```

不需要镜像时，临时取消：

```bash
unset HF_ENDPOINT
```

## 下载一个 Hugging Face 数据集小切片

```bash
python pipelines/ingest/download_hf_dataset.py \
  --config configs/datasets/xlam_function_calling_60k_sample.yaml
```

默认连接：

```text
MinIO:      http://localhost:9000
Bucket:     mokio-lake
PostgreSQL: postgresql+pg8000://mokio:mokio123456@localhost:5432/mokiofactory
```

也可以通过环境变量覆盖：

```bash
export AWS_ACCESS_KEY_ID=mokioadmin
export AWS_SECRET_ACCESS_KEY=mokioadmin123456
export AWS_ENDPOINT_URL=http://localhost:9000
export MOKIO_DATABASE_URL=postgresql+pg8000://mokio:mokio123456@localhost:5432/mokiofactory
```

## 验证结果

查看 MinIO raw 层：

```bash
mc ls --recursive mokio/mokio-lake/raw/source=hf/dataset=xlam-function-calling-60k/date=2026-07-08/
```

查看 PostgreSQL 元数据：

```bash
cd /Users/mokio/Project/MokioFactory/deploy/PostgreSQL
docker compose exec postgres psql -U mokio -d mokiofactory
```

```sql
SELECT dataset_id, task_family, sample_size, status, raw_path
FROM dataset_versions
ORDER BY created_at DESC
LIMIT 5;
```

## 配置说明

数据集配置放在 `configs/datasets/*.yaml`。关键字段：

```yaml
dataset_id: minpeter/xlam-function-calling-60k-parsed
source: hf
task_family: agent_tool_calling
license: cc-by-4.0
split: train
revision: main
streaming: true
sample_size: 1000
seed: 42
shuffle_buffer_size: 10000

raw_bucket: mokio-lake
raw_prefix: raw/source=hf/dataset=xlam-function-calling-60k/date=2026-07-08/
shard_filename: part-000000.jsonl
manifest_filename: manifest.json
schema_target: sft.v1
```

raw 层遵守不可变原则。不要覆盖已有正式数据路径；如果重新抽样或换 revision，写到新的 `date=`、`revision=` 或 `dataset_version=` 路径。
