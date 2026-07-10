# kind cleaning job

本目录用于把本地已经跑通的清洗脚本，放进 kind 模拟的 Kubernetes 集群里执行。

这一步不是为了提升单机性能，而是为了学习工业界常见的数据处理形态：

```text
本地 Python 脚本
  -> Docker 镜像
  -> Kubernetes Job
  -> 后续扩展成多个 Job / 多 Pod / Argo Workflow
```

## kind 是什么

`kind` 是 Kubernetes in Docker。

它会在你的电脑上用 Docker 容器模拟一个 K8s 集群，适合本地学习：

- 不需要云厂商。
- 不需要真实多台机器。
- 可以练习 `kubectl apply / logs / delete / Job / Secret`。
- 可以把后续工业里的 K8s 数据清洗流程先在本地缩小版跑通。

在真实工业环境里，数据清洗通常会跑在：

- Kubernetes Job：一次性批处理任务。
- Spark / Ray / Flink：更大规模分布式数据处理。
- Argo Workflows / Airflow：把下载、清洗、质检、混合、训练串成 DAG。

当前项目 Stage 1 先用单个 K8s Job 跑清洗；Stage 2 再拆分 shard，让多个 Pod 并行处理。

## 当前架构

```text
Mac / Docker
  ├─ MinIO: http://localhost:9000
  └─ kind cluster
      └─ Job: mokio-clean-sft
          └─ normalize_sft.py
              ├─ 读取 MinIO raw
              ├─ 写入 bronze
              └─ 写入 silver
```

注意：Job 在 kind 容器内部运行，所以访问宿主机 MinIO 时不能用 `localhost:9000`。

当前清单里使用：

```text
http://host.docker.internal:9000
```

这在 Docker Desktop for Mac 上通常可用。

## 1. 安装工具

```bash
brew install kind kubectl
```

确认 Docker Desktop 已经启动：

```bash
docker ps
```

## 2. 确认 MinIO 正在运行

```bash
cd deploy/MinIO
docker compose up -d
mc ls mokio/mokio-lake/raw/source=hf/
```

## 3. 创建 kind 集群

在项目根目录执行：

```bash
kind create cluster --config deploy/kind/kind-config.yaml
```

查看节点：

```bash
kubectl get nodes
```

预期能看到 1 个 control-plane 和 2 个 worker。

## 4. 构建清洗镜像

```bash
docker build \
  -f deploy/docker/clean.Dockerfile \
  -t mokiofactory-clean:stage1 \
  .
```

把本地镜像加载进 kind 集群：

```bash
kind load docker-image mokiofactory-clean:stage1 --name mokio-clean
```

## 5. 运行清洗 Job

```bash
kubectl apply -f deploy/kind/clean-sft-job.yaml
```

查看状态：

```bash
kubectl get pods
kubectl get jobs
```

查看日志：

```bash
kubectl logs job/mokio-clean-sft
```

成功时会看到类似：

```text
SFT normalization completed.
raw rows: 10500
bronze rows: 10100
silver rows: 10057
silver shard: s3://mokio-lake/silver/schema=sft.v1/date=2026-07-09/part-000000.jsonl
```

## 6. 验证对象存储输出

```bash
mc ls mokio/mokio-lake/bronze/schema=sft.v1/date=2026-07-09/
mc ls mokio/mokio-lake/silver/schema=sft.v1/date=2026-07-09/
mc cat mokio/mokio-lake/silver/schema=sft.v1/date=2026-07-09/manifest.json
```

## 7. 重跑 Job

K8s Job 名字不能重复创建。重跑前先删除旧 Job：

```bash
kubectl delete job mokio-clean-sft
kubectl apply -f deploy/kind/clean-sft-job.yaml
```

当前输出路径固定，所以重跑会覆盖同一天的 bronze / silver 文件。

后续更工业化的做法是给输出路径加 `run_id`：

```text
silver/schema=sft.v1/date=2026-07-09/run_id=20260709_001/
```

## 8. 清理环境

删除 Job：

```bash
kubectl delete job mokio-clean-sft
```

删除 kind 集群：

```bash
kind delete cluster --name mokio-clean
```

## 常见问题

### Pod 里连不上 MinIO

先看日志：

```bash
kubectl logs job/mokio-clean-sft
```

如果看到连接错误，重点检查：

- MinIO 容器是否启动。
- 宿主机上 `http://localhost:9000` 是否可访问。
- Docker Desktop for Mac 是否支持 `host.docker.internal`。

### 镜像拉取失败

如果 Pod 状态是 `ImagePullBackOff`，通常是没有把本地镜像加载进 kind：

```bash
kind load docker-image mokiofactory-clean:stage1 --name mokio-clean
```

### Secret 要不要提交

当前是本地学习配置，用户名密码和 MinIO README 保持一致。

真实项目里不要提交生产密钥，应该改用：

- `.env`
- External Secrets
- Vault
- 云厂商 Secret Manager

## 下一步

当前是单 Job 单 Pod。

后续可以继续升级：

```text
按 raw shard 拆分任务
  -> 每个 Pod 处理一个 shard
  -> 每个 Pod 写自己的 part 文件
  -> 最后 merge manifest
  -> 用 Argo Workflow 串联 ingest / clean / mix / train
```
