from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import yaml
from datasets import load_dataset
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.exc import SQLAlchemyError


DEFAULT_DATABASE_URL = "postgresql+pg8000://mokio:mokio123456@localhost:5432/mokiofactory"
DEFAULT_S3_ENDPOINT = "http://localhost:9000"
DEFAULT_S3_ACCESS_KEY = "mokioadmin"
DEFAULT_S3_SECRET_KEY = "mokioadmin123456"


metadata = MetaData()

dataset_versions = Table(
    "dataset_versions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("dataset_id", String, nullable=False),
    Column("source", String, nullable=False),
    Column("task_family", String, nullable=False),
    Column("license", String, nullable=False),
    Column("sample_size", Integer, nullable=False),
    Column("raw_path", String, nullable=False),
    Column("manifest_path", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)

dataset_files = Table(
    "dataset_files",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "dataset_version_id",
        String(36),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("object_key", String, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String, nullable=False),
    Column("record_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("config_json", JSON, nullable=False, default=dict),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
)


@dataclass(frozen=True)
class DatasetConfig:
    dataset_id: str
    config_name: str | None
    data_files: str | list[str] | None
    source: str
    task_family: str
    license: str
    split: str
    revision: str
    streaming: bool
    shuffle: bool
    sample_size: int
    seed: int
    shuffle_buffer_size: int
    raw_bucket: str
    raw_prefix: str
    shard_filename: str
    manifest_filename: str
    schema_target: str

    @property
    def shard_key(self) -> str:
        return join_s3_key(self.raw_prefix, self.shard_filename)

    @property
    def manifest_key(self) -> str:
        return join_s3_key(self.raw_prefix, self.manifest_filename)

    @property
    def raw_path(self) -> str:
        return f"s3://{self.raw_bucket}/{ensure_trailing_slash(self.raw_prefix)}"

    @property
    def manifest_path(self) -> str:
        return f"s3://{self.raw_bucket}/{self.manifest_key}"


def ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def join_s3_key(prefix: str, filename: str) -> str:
    return f"{ensure_trailing_slash(prefix)}{filename}".lstrip("/")


def load_config(path: Path) -> DatasetConfig:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    required = {
        "dataset_id",
        "source",
        "task_family",
        "license",
        "split",
        "raw_bucket",
        "raw_prefix",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Missing required config fields: {', '.join(missing)}")

    return DatasetConfig(
        dataset_id=raw["dataset_id"],
        config_name=raw.get("config_name"),
        data_files=raw.get("data_files"),
        source=raw["source"],
        task_family=raw["task_family"],
        license=raw["license"],
        split=raw["split"],
        revision=raw.get("revision", "main"),
        streaming=bool(raw.get("streaming", True)),
        shuffle=bool(raw.get("shuffle", True)),
        sample_size=int(raw.get("sample_size", 1000)),
        seed=int(raw.get("seed", 42)),
        shuffle_buffer_size=int(raw.get("shuffle_buffer_size", 10000)),
        raw_bucket=raw["raw_bucket"],
        raw_prefix=ensure_trailing_slash(raw["raw_prefix"]),
        shard_filename=raw.get("shard_filename", "part-000000.jsonl"),
        manifest_filename=raw.get("manifest_filename", "manifest.json"),
        schema_target=raw.get("schema_target", "raw.v1"),
    )


def s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", DEFAULT_S3_ENDPOINT),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", DEFAULT_S3_ACCESS_KEY),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", DEFAULT_S3_SECRET_KEY),
    )


def ensure_bucket(client: Any, bucket: str) -> None:
    try:
        existing = {item["Name"] for item in client.list_buckets()["Buckets"]}
        if bucket not in existing:
            client.create_bucket(Bucket=bucket)
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(
            "Cannot reach MinIO/S3 endpoint. Make sure MinIO is running with:\n"
            "  cd deploy/MinIO && docker compose up -d\n"
            "Then verify it with:\n"
            "  mc admin info mokio"
        ) from exc


def preflight_database(database_url: str) -> None:
    try:
        engine = create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(select(1))
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Cannot reach PostgreSQL. Make sure PostgreSQL is running with:\n"
            "  cd deploy/PostgreSQL && docker compose up -d\n"
            "Then verify it with:\n"
            "  docker compose exec postgres psql -U mokio -d mokiofactory"
        ) from exc


def write_jsonl_shard(config: DatasetConfig, output_path: Path) -> tuple[int, int, str]:
    load_kwargs: dict[str, Any] = {
        "split": config.split,
        "revision": config.revision,
        "streaming": config.streaming,
    }
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        load_kwargs["token"] = hf_token
        load_kwargs["storage_options"] = {"token": hf_token}
    if config.data_files:
        load_kwargs["data_files"] = config.data_files

    dataset = load_dataset(
        config.dataset_id,
        config.config_name,
        **load_kwargs,
    )

    if config.shuffle:
        if config.streaming:
            dataset = dataset.shuffle(
                seed=config.seed,
                buffer_size=config.shuffle_buffer_size,
            )
        else:
            dataset = dataset.shuffle(seed=config.seed)

    hasher = hashlib.sha256()
    record_count = 0

    with output_path.open("wb") as f:
        for row in dataset:
            if record_count >= config.sample_size:
                break
            line = json.dumps(row, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
            f.write(line)
            hasher.update(line)
            record_count += 1

    size_bytes = output_path.stat().st_size
    return record_count, size_bytes, hasher.hexdigest()


def build_manifest(
    config: DatasetConfig,
    *,
    dataset_version_id: str,
    record_count: int,
    size_bytes: int,
    sha256: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "dataset_version_id": dataset_version_id,
        "dataset_id": config.dataset_id,
        "config_name": config.config_name,
        "data_files": config.data_files,
        "source": config.source,
        "task_family": config.task_family,
        "license": config.license,
        "split": config.split,
        "revision": config.revision,
        "streaming": config.streaming,
        "shuffle": config.shuffle,
        "sample_size": config.sample_size,
        "actual_record_count": record_count,
        "schema_target": config.schema_target,
        "raw_path": config.raw_path,
        "manifest_path": config.manifest_path,
        "created_at": now,
        "files": [
            {
                "bucket": config.raw_bucket,
                "object_key": config.shard_key,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "record_count": record_count,
            }
        ],
    }


def upload_bytes(client: Any, *, bucket: str, key: str, body: bytes, content_type: str) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def insert_metadata(
    database_url: str,
    config: DatasetConfig,
    *,
    dataset_version_id: str,
    pipeline_run_id: str,
    record_count: int,
    size_bytes: int,
    sha256: str,
) -> None:
    engine = create_engine(database_url)
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            insert(pipeline_runs).values(
                id=pipeline_run_id,
                run_type="ingest",
                status="succeeded",
                config_json={
                    "dataset_id": config.dataset_id,
                    "config_name": config.config_name,
                    "data_files": config.data_files,
                    "split": config.split,
                    "revision": config.revision,
                    "sample_size": config.sample_size,
                    "shuffle": config.shuffle,
                    "raw_path": config.raw_path,
                },
            )
        )
        conn.execute(
            insert(dataset_versions).values(
                id=dataset_version_id,
                dataset_id=config.dataset_id,
                source=config.source,
                task_family=config.task_family,
                license=config.license,
                sample_size=record_count,
                raw_path=config.raw_path,
                manifest_path=config.manifest_path,
                status="downloaded",
            )
        )
        conn.execute(
            insert(dataset_files).values(
                id=str(uuid4()),
                dataset_version_id=dataset_version_id,
                object_key=config.shard_key,
                size_bytes=size_bytes,
                sha256=sha256,
                record_count=record_count,
            )
        )


def ingest(config_path: Path) -> None:
    config = load_config(config_path)
    dataset_version_id = str(uuid4())
    pipeline_run_id = str(uuid4())
    database_url = os.getenv("MOKIO_DATABASE_URL", DEFAULT_DATABASE_URL)
    client = s3_client()

    ensure_bucket(client, config.raw_bucket)
    preflight_database(database_url)

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / config.shard_filename
        record_count, size_bytes, sha256 = write_jsonl_shard(config, shard_path)

        manifest = build_manifest(
            config,
            dataset_version_id=dataset_version_id,
            record_count=record_count,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        manifest_body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

        client.upload_file(str(shard_path), config.raw_bucket, config.shard_key)
        upload_bytes(
            client,
            bucket=config.raw_bucket,
            key=config.manifest_key,
            body=manifest_body,
            content_type="application/json",
        )

    insert_metadata(
        database_url,
        config,
        dataset_version_id=dataset_version_id,
        pipeline_run_id=pipeline_run_id,
        record_count=record_count,
        size_bytes=size_bytes,
        sha256=sha256,
    )

    print("Ingest completed.")
    print(f"dataset_version_id: {dataset_version_id}")
    print(f"records: {record_count}")
    print(f"raw shard: s3://{config.raw_bucket}/{config.shard_key}")
    print(f"manifest: {config.manifest_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a Hugging Face dataset shard to MinIO and PostgreSQL.")
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to configs/datasets/*.yaml",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ingest(args.config)


if __name__ == "__main__":
    main()
