# PostgreSQL 本地单节点部署

本目录用于在本机启动一个 PostgreSQL，作为 MokioFactory 的结构化元数据存储。

MinIO 负责放数据文件，PostgreSQL 负责记录这些文件的索引、版本、状态和血缘关系：

```text
MinIO:
  s3://mokio-lake/raw/source=hf/dataset=xlam/date=2026-07-08/part-000000.jsonl
  s3://mokio-lake/raw/source=hf/dataset=xlam/date=2026-07-08/manifest.json

PostgreSQL:
  dataset_id
  dataset_version
  manifest_path
  object_key
  sha256
  record_count
  pipeline_run_status
```

## 1. 前置依赖

需要先安装并启动 Docker Desktop：

```bash
brew install --cask docker
```

确认 Docker 可用：

```bash
docker version
docker compose version
```

建议安装 PostgreSQL 客户端工具，方便用 `psql` 手动连接：

```bash
brew install libpq
brew link --force libpq
```

如果不想安装本机 `psql`，也可以用容器里的 `psql`，见第 5 节。

## 2. 启动服务

进入本目录：

```bash
cd /Users/mokio/Project/MokioFactory/deploy/PostgreSQL
```

启动 PostgreSQL：

```bash
docker compose up -d
```

查看容器状态：

```bash
docker compose ps
```

健康状态应该类似：

```text
mokio-postgres   Up ... (healthy)   0.0.0.0:5432->5432/tcp
```

查看日志：

```bash
docker compose logs -f postgres
```

## 3. 连接信息

默认连接信息来自 `docker-compose.yml`：

```text
Host: localhost
Port: 5432
Database: mokiofactory
User: mokio
Password: mokio123456
```

连接 URL：

```text
postgresql://mokio:mokio123456@localhost:5432/mokiofactory
```

后续 Python 脚本可以使用环境变量：

```bash
export MOKIO_DATABASE_URL=postgresql://mokio:mokio123456@localhost:5432/mokiofactory
```

## 4. 用本机 psql 连接

如果已经安装 `psql`：

```bash
psql postgresql://mokio:mokio123456@localhost:5432/mokiofactory
```

进入后可以执行：

```sql
SELECT version();
```

退出：

```sql
\q
```

## 5. 用容器里的 psql 连接

不安装本机客户端也可以直接进入容器执行：

```bash
docker compose exec postgres psql -U mokio -d mokiofactory
```

执行简单查询：

```sql
SELECT current_database(), current_user;
```

## 6. 最小元数据表设计

第一阶段只需要三张表，足够支撑 Hugging Face 下载数据到 MinIO 的记录：

```text
dataset_versions  数据集版本，一次下载/抽样/落湖就是一个版本
dataset_files     数据文件清单，记录每个 object key、大小、hash、样本数
pipeline_runs     流水线运行记录，记录 ingest/clean/tokenize/train/eval 的状态
```

这些表不是存 JSONL 正文的地方。正文、manifest、checkpoint、报告都放 MinIO，PostgreSQL 只记录索引和状态。

## 7. Python 增删改查示例

先安装 Python 依赖：

```bash
pip install sqlalchemy psycopg[binary]
```

运行示例：

```bash
python3 deploy/PostgreSQL/learn/crud.py
```

这个脚本使用 SQLAlchemy ORM，不直接手写 SQL 命令。它会完成：

```text
Create: 建表，插入 dataset_version 和 dataset_file
Read:   查询 dataset_version 和关联文件
Update: 更新 dataset_version 状态
Delete: 删除示例数据
```

脚本默认连接：

```text
postgresql+psycopg://mokio:mokio123456@localhost:5432/mokiofactory
```

也可以通过环境变量覆盖：

```bash
MOKIO_DATABASE_URL=postgresql+psycopg://mokio:mokio123456@localhost:5432/mokiofactory \
  python3 deploy/PostgreSQL/learn/crud.py
```

## 8. 和 MinIO 的关系

后续 Hugging Face 数据下载脚本应该同时写两边：

```text
1. 数据文件写入 MinIO:
   s3://mokio-lake/raw/source=hf/dataset=<dataset_name>/date=<YYYY-MM-DD>/part-000000.jsonl

2. manifest 写入 MinIO:
   s3://mokio-lake/raw/source=hf/dataset=<dataset_name>/date=<YYYY-MM-DD>/manifest.json

3. 元数据写入 PostgreSQL:
   dataset_versions.raw_path
   dataset_versions.manifest_path
   dataset_files.object_key
   dataset_files.size_bytes
   dataset_files.sha256
   dataset_files.record_count
```

这样后面清洗、配比、训练、测评都可以通过 PostgreSQL 查到上一阶段产物。

## 9. 停止和清理

停止容器，但保留数据卷：

```bash
docker compose down
```

重新启动后数据仍在：

```bash
docker compose up -d
```

如果要彻底清空本地 PostgreSQL 数据卷：

```bash
docker compose down -v
```

这个命令会删除数据库里的表和数据，只在你明确想重置本地环境时使用。

## 10. 常见问题

### 端口 5432 被占用

如果本机已经运行了 PostgreSQL，可能会看到端口冲突。可以先停掉本机服务，或者把 compose 端口改成：

```yaml
ports:
  - "5433:5432"
```

这时连接 URL 也要改成：

```text
postgresql://mokio:mokio123456@localhost:5433/mokiofactory
```

### password authentication failed

确认连接信息和 `docker-compose.yml` 一致：

```text
User: mokio
Password: mokio123456
Database: mokiofactory
```

如果你改过密码但复用了旧 volume，PostgreSQL 不会自动改旧数据库用户密码。学习环境里可以用下面命令重置：

```bash
docker compose down -v
docker compose up -d
```
