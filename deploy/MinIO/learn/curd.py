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