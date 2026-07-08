# MinIO AIStor 本地单节点部署

本目录用于在本机启动一个单节点 AIStor/MinIO 对象存储，作为 MokioFactory 的本地数据湖。

启动后会得到两个入口：

```text
S3 API:  http://localhost:9000
Console: http://localhost:9001
```

项目后续的数据落湖路径会写入 `mokio-lake` bucket：

```text
s3://mokio-lake/raw/source=hf/dataset=<dataset_name>/date=<YYYY-MM-DD>/
```

## 1. 前置依赖

需要先安装：

```bash
brew install --cask docker
brew install minio/aistor/mc
```

启动 Docker Desktop，并确认 Docker 可用：

```bash
docker version
docker compose version
mc --version
```

## 2. 获取 AIStor license

从 MinIO AIStor 官网获取本地开发用 license，然后放到当前目录：

```text
deploy/MinIO/minio.license
```

当前 `docker-compose.yml` 通过下面这行把 license 挂进容器：

```yaml
command: server /data --console-address ":9001" --license /minio.license
```

对应 volume 挂载：

```yaml
volumes:
  - ./minio.license:/minio.license:ro
```

`minio.license` 是本机私有文件，不应该提交到 Git。

## 3. 启动服务

进入本目录：

```bash
cd /Users/mokio/Project/MokioFactory/deploy/MinIO
```

启动单节点 AIStor/MinIO：

```bash
docker compose up -d
```

查看容器状态：

```bash
docker compose ps
```

健康状态应该类似：

```text
mokio-minio   Up ... (healthy)   0.0.0.0:9000-9001->9000-9001/tcp
```

查看日志：

```bash
docker compose logs -f minio
```

第一次启动时，日志里看到下面内容表示存储盘已经初始化成功：

```text
Pool-0: 1/1 drives formatted and online
```

## 4. 登录 Console

浏览器打开：

```text
http://localhost:9001
```

默认账号密码来自 `docker-compose.yml`：

```text
Username: mokioadmin
Password: mokioadmin123456
```

## 5. 配置 mc 客户端

把本地 `mc` 客户端连接到 AIStor/MinIO：

```bash
mc alias set mokio http://localhost:9000 mokioadmin mokioadmin123456
```

检查连接状态：

```bash
mc admin info mokio
```

正常结果应显示：

```text
MinIO Cluster: Online
Drives: 1/1
```

## 6. 创建数据湖 bucket

创建项目默认 bucket：

```bash
mc mb --ignore-existing mokio/mokio-lake
```

查看 bucket：

```bash
mc ls mokio
```

## 7. 上传 raw 层测试数据

创建一个测试 JSONL：

```bash
printf '{"text":"hello mokio","source":"manual-test"}\n' > /tmp/mokio-minio-test.jsonl
```

上传到 Hive 风格分区路径：

```bash
mc cp /tmp/mokio-minio-test.jsonl \
  mokio/mokio-lake/raw/source=test/dataset=manual/date=2026-07-08/part-000000.jsonl
```

查看上传结果：

```bash
mc ls mokio/mokio-lake/raw/source=test/dataset=manual/date=2026-07-08/
```

如果看到 `part-000000.jsonl`，说明链路已经跑通：

```text
Docker AIStor/MinIO Server -> macOS mc client -> mokio-lake bucket -> raw 分区路径
```

## 8. 后续 Python 访问方式

后续 Hugging Face 数据下载脚本可以通过 S3 API 写入这个 bucket。

环境变量建议：

```bash
export AWS_ACCESS_KEY_ID=mokioadmin
export AWS_SECRET_ACCESS_KEY=mokioadmin123456
export AWS_ENDPOINT_URL=http://localhost:9000
export MOKIO_BUCKET=mokio-lake
```

Python 增删改查示例：

```python
import boto3

BUCKET = "mokio-lake"
KEY = "raw/source=hf/dataset=test/date=2026-07-08/hello.jsonl"

s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    aws_access_key_id="mokioadmin",
    aws_secret_access_key="mokioadmin123456",
)

# Create: 创建 bucket（已存在也不报错）
existing_buckets = {bucket["Name"] for bucket in s3.list_buckets()["Buckets"]}
if BUCKET not in existing_buckets:
    s3.create_bucket(Bucket=BUCKET)

# Create: 上传对象
s3.put_object(
    Bucket=BUCKET,
    Key=KEY,
    Body=b'{"text": "hello"}\n',
    ContentType="application/jsonl",
)

# Read: 读取对象内容
obj = s3.get_object(Bucket=BUCKET, Key=KEY)
print(obj["Body"].read().decode("utf-8"))

# Read: 按 prefix 列出对象
resp = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix="raw/source=hf/dataset=test/date=2026-07-08/",
)
for item in resp.get("Contents", []):
    print(item["Key"], item["Size"])

# Update: S3 没有原地修改，更新就是用同一个 Key 覆盖上传
s3.put_object(
    Bucket=BUCKET,
    Key=KEY,
    Body=b'{"text": "hello updated"}\n',
    ContentType="application/jsonl",
)

# Delete: 删除对象
s3.delete_object(Bucket=BUCKET, Key=KEY)
```

这里的“改”本质是覆盖写入：对象存储没有传统文件系统里的原地编辑。后续下载数据集时，raw 层应遵守不可变原则，不要覆盖已有 key；如果数据内容变了，应该写到新的 `date=`、`revision=` 或 `dataset_version=` 路径下。

## 9. 停止和清理

停止容器，但保留数据卷：

```bash
docker compose down
```

重新启动后数据仍在：

```bash
docker compose up -d
```

如果要彻底清空本地 MinIO 数据卷：

```bash
docker compose down -v
```

这个命令会删除 bucket 和已上传对象，只在你明确想重置本地环境时使用。

## 10. 常见问题

### Invalid secret key

如果执行：

```bash
mc alias set mokio http://localhost:9000 mokio mokio
```

出现：

```text
Invalid secret key
```

说明 secret key 太短或和服务端配置不一致。使用 `docker-compose.yml` 中配置的账号密码：

```bash
mc alias set mokio http://localhost:9000 mokioadmin mokioadmin123456
```

### 分布式模式启动后部分节点 Offline

如果使用 `http://minio{1...4}/data{1...2}` 这类 4 节点分布式配置，而 license 不支持 distributed setup，会看到部分节点 Offline。

本项目第一阶段使用单节点配置即可：

```yaml
command: server /data --console-address ":9001" --license /minio.license
```

这已经足够学习对象存储、bucket、raw/bronze/silver/gold 落湖和 Python S3 API。
