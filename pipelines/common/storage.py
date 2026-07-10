from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import boto3


DEFAULT_S3_ENDPOINT = "http://localhost:9000"
DEFAULT_S3_ACCESS_KEY = "mokioadmin"
DEFAULT_S3_SECRET_KEY = "mokioadmin123456"


def s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", DEFAULT_S3_ENDPOINT),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", DEFAULT_S3_ACCESS_KEY),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", DEFAULT_S3_SECRET_KEY),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def join_key(prefix: str, filename: str) -> str:
    return f"{prefix.rstrip('/')}/{filename.lstrip('/')}"


def object_exists(client: Any, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except client.exceptions.ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return False
        raise
    return True


def read_json(client: Any, bucket: str, key: str) -> dict[str, Any]:
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body.decode("utf-8"))


def iter_jsonl(client: Any, bucket: str, key: str) -> Iterable[dict[str, Any]]:
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    for raw_line in body.iter_lines():
        if raw_line:
            yield json.loads(raw_line.decode("utf-8"))


def put_json(client: Any, bucket: str, key: str, payload: dict[str, Any]) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


def upload_file(
    client: Any,
    bucket: str,
    key: str,
    path: Path,
    *,
    content_type: str = "application/jsonl",
) -> None:
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})


def write_jsonl(records: Iterable[dict[str, Any]], path: Path) -> dict[str, Any]:
    hasher = hashlib.sha256()
    count = 0
    with path.open("wb") as handle:
        for record in records:
            line = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            handle.write(line)
            hasher.update(line)
            count += 1
    return {
        "record_count": count,
        "size_bytes": path.stat().st_size,
        "sha256": hasher.hexdigest(),
    }


def payload_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
