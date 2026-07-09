from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid5, NAMESPACE_URL

import boto3
import yaml


DEFAULT_S3_ENDPOINT = "http://localhost:9000"
DEFAULT_S3_ACCESS_KEY = "mokioadmin"
DEFAULT_S3_SECRET_KEY = "mokioadmin123456"


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class CleanConfig:
    schema_version: str
    input_bucket: str
    raw_prefixes: list[str]
    output_bucket: str
    bronze_prefix: str
    silver_prefix: str
    shard_filename: str
    manifest_filename: str
    min_messages: int
    min_assistant_chars: int
    max_chars: int
    drop_empty_content: bool
    drop_duplicate_message_text: bool
    collapse_whitespace: bool
    strip_control_chars: bool
    default_quality_score: float


def s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", DEFAULT_S3_ENDPOINT),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", DEFAULT_S3_ACCESS_KEY),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", DEFAULT_S3_SECRET_KEY),
    )


def ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def join_s3_key(prefix: str, filename: str) -> str:
    return f"{ensure_trailing_slash(prefix)}{filename}".lstrip("/")


def load_config(path: Path) -> CleanConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    filters = raw.get("filters", {})
    normalization = raw.get("normalization", {})
    output = raw["output"]
    input_cfg = raw["input"]
    return CleanConfig(
        schema_version=raw.get("schema_version", "sft.v1"),
        input_bucket=input_cfg["bucket"],
        raw_prefixes=[ensure_trailing_slash(p) for p in input_cfg["raw_prefixes"]],
        output_bucket=output["bucket"],
        bronze_prefix=ensure_trailing_slash(output["bronze_prefix"]),
        silver_prefix=ensure_trailing_slash(output["silver_prefix"]),
        shard_filename=output.get("shard_filename", "part-000000.jsonl"),
        manifest_filename=output.get("manifest_filename", "manifest.json"),
        min_messages=int(filters.get("min_messages", 2)),
        min_assistant_chars=int(filters.get("min_assistant_chars", 2)),
        max_chars=int(filters.get("max_chars", 120000)),
        drop_empty_content=bool(filters.get("drop_empty_content", True)),
        drop_duplicate_message_text=bool(filters.get("drop_duplicate_message_text", True)),
        collapse_whitespace=bool(normalization.get("collapse_whitespace", True)),
        strip_control_chars=bool(normalization.get("strip_control_chars", True)),
        default_quality_score=float(normalization.get("default_quality_score", 0.8)),
    )


def normalize_text(value: Any, config: CleanConfig) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if config.strip_control_chars:
        text = CONTROL_CHARS.sub("", text)
    if config.collapse_whitespace:
        text = WHITESPACE.sub(" ", text)
    return text.strip()


def stable_id(source_dataset: str, raw_id: Any, row_index: int, messages: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {
            "source_dataset": source_dataset,
            "raw_id": raw_id,
            "row_index": row_index,
            "messages": messages,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return str(uuid5(NAMESPACE_URL, payload))


def source_from_key(key: str) -> str:
    match = re.search(r"dataset=([^/]+)", key)
    return match.group(1) if match else "unknown"


def task_family_from_source(source_dataset: str) -> str:
    mapping = {
        "salesforce-xlam-function-calling-60k": "agent_tool_calling",
        "xlam-function-calling-60k": "agent_tool_calling",
        "salesforce-apigen-mt-5k": "agent_multi_turn",
        "openthoughts-agent-v1-sft": "agent_terminal_code",
        "nvidia-opencodeinstruct": "code_sft",
        "glint-fable-5-traces": "code_project_agent",
    }
    return mapping.get(source_dataset, "sft")


def domain_from_task_family(task_family: str) -> str:
    if "code" in task_family:
        return "code"
    if "agent" in task_family:
        return "agent"
    return "general"


def message(role: str, content: Any, config: CleanConfig) -> dict[str, str] | None:
    text = normalize_text(content, config)
    if config.drop_empty_content and not text:
        return None
    return {"role": role, "content": text}


def parse_conversations(conversations: Any, config: CleanConfig) -> list[dict[str, str]]:
    if not isinstance(conversations, list):
        return []
    messages: list[dict[str, str]] = []
    for item in conversations:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or item.get("from") or item.get("speaker")
        content = item.get("content") or item.get("value") or item.get("text")
        role_map = {
            "human": "user",
            "gpt": "assistant",
            "user": "user",
            "assistant": "assistant",
            "system": "system",
            "tool": "tool",
            "observation": "tool",
        }
        normalized_role = role_map.get(str(role).lower(), "assistant" if messages else "user")
        msg = message(normalized_role, content, config)
        if msg:
            messages.append(msg)
    return messages


def fable_content_to_text(content: Any, config: CleanConfig) -> str:
    if isinstance(content, str):
        return normalize_text(content, config)
    if not isinstance(content, list):
        return normalize_text(content, config)

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            text = normalize_text(item, config)
            if text:
                parts.append(text)
            continue

        part_type = item.get("type")
        if part_type == "text":
            text = normalize_text(item.get("text"), config)
        elif part_type == "thinking":
            text = normalize_text(item.get("thinking"), config)
        elif part_type == "toolCall":
            tool_call = {
                "type": "tool_call",
                "id": item.get("id"),
                "name": item.get("name"),
                "arguments": item.get("arguments") or {},
            }
            text = normalize_text(tool_call, config)
        else:
            text = normalize_text(item, config)

        if text:
            parts.append(text)

    return "\n\n".join(parts)


def fable_tools_from_content(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []

    tools: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "toolCall":
            continue
        tools.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "arguments": item.get("arguments") or {},
            }
        )
    return tools


def fable_event_to_message(row: dict[str, Any], config: CleanConfig) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
    payload = row.get("message")
    if not isinstance(payload, dict):
        return None, []

    role = str(payload.get("role") or "").lower()
    if role not in {"user", "assistant", "system", "tool"}:
        return None, []

    content = payload.get("content")
    text = fable_content_to_text(content, config)
    msg = message(role, text, config)
    return msg, fable_tools_from_content(content)


def fable_trace_to_record(
    rows: list[tuple[int, dict[str, Any]]],
    *,
    source_key: str,
    trace_index: int,
    config: CleanConfig,
) -> tuple[dict[str, Any] | None, str | None]:
    source_dataset = source_from_key(source_key)
    task_family = task_family_from_source(source_dataset)
    messages: list[dict[str, str]] = []
    tools: list[dict[str, Any]] = []
    session_row: dict[str, Any] | None = None
    model_id: Any = None
    thinking_level: Any = None

    for _, row in rows:
        row_type = row.get("type")
        if row_type == "session":
            session_row = row
        elif row_type == "model_change":
            model_id = row.get("modelId")
        elif row_type == "thinking_level_change":
            thinking_level = row.get("thinkingLevel")
        elif row_type == "message":
            msg, row_tools = fable_event_to_message(row, config)
            if msg:
                messages.append(msg)
            tools.extend(row_tools)

    if len(messages) < config.min_messages:
        return None, "too_few_messages"
    if not any(m["role"] == "assistant" and len(m["content"]) >= config.min_assistant_chars for m in messages):
        return None, "missing_assistant"

    total_chars = sum(len(m["content"]) for m in messages)
    if total_chars > config.max_chars:
        return None, "too_long"

    raw_id = session_row.get("id") if session_row else f"trace-{trace_index}"
    dedupe_key = "\n".join(f"{m['role']}:{m['content']}" for m in messages)
    record = {
        "id": stable_id(source_dataset, raw_id, trace_index, messages),
        "schema_version": config.schema_version,
        "source_dataset": source_dataset,
        "task_family": task_family,
        "domain": domain_from_task_family(task_family),
        "messages": messages,
        "tools": tools,
        "quality_score": config.default_quality_score,
        "meta": {
            "raw_object_key": source_key,
            "raw_row_index": rows[0][0] if rows else None,
            "raw_row_indexes": [idx for idx, _ in rows],
            "raw_id": raw_id,
            "raw_event_count": len(rows),
            "trace_index": trace_index,
            "cwd": session_row.get("cwd") if session_row else None,
            "model_id": model_id,
            "thinking_level": thinking_level,
            "dedupe_key_sha256": hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest(),
        },
    }
    return record, None


def fable_traces_from_rows(rows: Iterable[tuple[int, dict[str, Any]]]) -> Iterable[list[tuple[int, dict[str, Any]]]]:
    current: list[tuple[int, dict[str, Any]]] = []
    for row_index, row in rows:
        if row.get("type") == "session" and current:
            yield current
            current = []
        current.append((row_index, row))
    if current:
        yield current


def clean_fable_records(
    rows: Iterable[tuple[int, dict[str, Any]]],
    *,
    source_key: str,
    config: CleanConfig,
) -> Iterable[tuple[dict[str, Any] | None, str | None]]:
    for trace_index, trace_rows in enumerate(fable_traces_from_rows(rows)):
        yield fable_trace_to_record(trace_rows, source_key=source_key, trace_index=trace_index, config=config)


def row_to_messages(row: dict[str, Any], source_dataset: str, config: CleanConfig) -> tuple[list[dict[str, str]], list[Any]]:
    tools = row.get("tools") or []

    if isinstance(row.get("messages"), list):
        return parse_conversations(row["messages"], config), tools

    if isinstance(row.get("conversations"), list):
        messages = []
        system = message("system", row.get("system"), config)
        if system:
            messages.append(system)
        messages.extend(parse_conversations(row["conversations"], config))
        return messages, tools

    if "query" in row and "answers" in row:
        messages = []
        user = message("user", row.get("query"), config)
        assistant = message("assistant", row.get("answers"), config)
        if user:
            messages.append(user)
        if assistant:
            messages.append(assistant)
        return messages, tools

    for prompt_key in ("instruction", "prompt", "question", "input"):
        for answer_key in ("output", "response", "answer", "completion", "solution"):
            if prompt_key in row and answer_key in row:
                prompt = row.get(prompt_key)
                if prompt_key != "input" and row.get("input"):
                    prompt = f"{prompt}\n{row.get('input')}"
                messages = []
                user = message("user", prompt, config)
                assistant = message("assistant", row.get(answer_key), config)
                if user:
                    messages.append(user)
                if assistant:
                    messages.append(assistant)
                return messages, tools

    return [], tools


def clean_record(
    row: dict[str, Any],
    *,
    source_key: str,
    row_index: int,
    config: CleanConfig,
) -> tuple[dict[str, Any] | None, str | None]:
    source_dataset = source_from_key(source_key)
    task_family = task_family_from_source(source_dataset)
    messages, tools = row_to_messages(row, source_dataset, config)

    if len(messages) < config.min_messages:
        return None, "too_few_messages"
    if not any(m["role"] == "assistant" and len(m["content"]) >= config.min_assistant_chars for m in messages):
        return None, "missing_assistant"

    total_chars = sum(len(m["content"]) for m in messages)
    if total_chars > config.max_chars:
        return None, "too_long"

    dedupe_key = "\n".join(f"{m['role']}:{m['content']}" for m in messages)
    record = {
        "id": stable_id(source_dataset, row.get("id"), row_index, messages),
        "schema_version": config.schema_version,
        "source_dataset": source_dataset,
        "task_family": task_family,
        "domain": domain_from_task_family(task_family),
        "messages": messages,
        "tools": tools if isinstance(tools, list) else [tools],
        "quality_score": config.default_quality_score,
        "meta": {
            "raw_object_key": source_key,
            "raw_row_index": row_index,
            "raw_id": row.get("id"),
            "dedupe_key_sha256": hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest(),
        },
    }
    return record, None


def list_raw_objects(client: Any, bucket: str, prefixes: Iterable[str]) -> list[str]:
    keys: list[str] = []
    for prefix in prefixes:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith(".jsonl"):
                    keys.append(key)
    return sorted(keys)


def iter_s3_jsonl(client: Any, bucket: str, key: str) -> Iterable[tuple[int, dict[str, Any]]]:
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    for idx, raw_line in enumerate(body.iter_lines()):
        if not raw_line:
            continue
        yield idx, json.loads(raw_line.decode("utf-8"))


def write_jsonl(records: Iterable[dict[str, Any]], path: Path) -> tuple[int, int, str]:
    hasher = hashlib.sha256()
    count = 0
    with path.open("wb") as f:
        for record in records:
            line = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            f.write(line)
            hasher.update(line)
            count += 1
    return count, path.stat().st_size, hasher.hexdigest()


def build_manifest(config: CleanConfig, layer: str, key: str, count: int, size: int, sha256: str, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "layer": layer,
        "bucket": config.output_bucket,
        "object_key": key,
        "record_count": count,
        "size_bytes": size,
        "sha256": sha256,
        "stats": stats,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def upload_file(client: Any, bucket: str, key: str, path: Path, content_type: str = "application/jsonl") -> None:
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})


def upload_json(client: Any, bucket: str, key: str, payload: dict[str, Any]) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def normalize(config_path: Path) -> None:
    config = load_config(config_path)
    client = s3_client()
    raw_keys = list_raw_objects(client, config.input_bucket, config.raw_prefixes)
    if not raw_keys:
        raise RuntimeError("No raw JSONL objects found. Check MinIO connectivity and raw_prefixes.")

    bronze_records: list[dict[str, Any]] = []
    silver_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    stats: dict[str, Any] = {"raw_files": raw_keys, "raw_rows": 0, "bronze_rows": 0, "silver_rows": 0, "dropped": {}}

    for key in raw_keys:
        source_dataset = source_from_key(key)
        raw_rows = list(iter_s3_jsonl(client, config.input_bucket, key))
        stats["raw_rows"] += len(raw_rows)

        if source_dataset == "glint-fable-5-traces":
            cleaned = clean_fable_records(raw_rows, source_key=key, config=config)
        else:
            cleaned = (
                clean_record(row, source_key=key, row_index=row_index, config=config)
                for row_index, row in raw_rows
            )

        for record, reason in cleaned:
            if record is None:
                stats["dropped"][reason or "unknown"] = stats["dropped"].get(reason or "unknown", 0) + 1
                continue
            bronze_records.append(record)
            dedupe_key = record["meta"]["dedupe_key_sha256"]
            if config.drop_duplicate_message_text and dedupe_key in seen:
                stats["dropped"]["duplicate"] = stats["dropped"].get("duplicate", 0) + 1
                continue
            seen.add(dedupe_key)
            silver_records.append(record)

    stats["bronze_rows"] = len(bronze_records)
    stats["silver_rows"] = len(silver_records)

    bronze_key = join_s3_key(config.bronze_prefix, config.shard_filename)
    silver_key = join_s3_key(config.silver_prefix, config.shard_filename)
    bronze_manifest_key = join_s3_key(config.bronze_prefix, config.manifest_filename)
    silver_manifest_key = join_s3_key(config.silver_prefix, config.manifest_filename)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bronze_path = tmp / f"bronze-{config.shard_filename}"
        silver_path = tmp / f"silver-{config.shard_filename}"
        bronze_count, bronze_size, bronze_hash = write_jsonl(bronze_records, bronze_path)
        silver_count, silver_size, silver_hash = write_jsonl(silver_records, silver_path)
        upload_file(client, config.output_bucket, bronze_key, bronze_path)
        upload_file(client, config.output_bucket, silver_key, silver_path)
        upload_json(client, config.output_bucket, bronze_manifest_key, build_manifest(config, "bronze", bronze_key, bronze_count, bronze_size, bronze_hash, stats))
        upload_json(client, config.output_bucket, silver_manifest_key, build_manifest(config, "silver", silver_key, silver_count, silver_size, silver_hash, stats))

    print("SFT normalization completed.")
    print(f"raw rows: {stats['raw_rows']}")
    print(f"bronze rows: {bronze_count}")
    print(f"silver rows: {silver_count}")
    print(f"silver shard: s3://{config.output_bucket}/{silver_key}")
    print(f"silver manifest: s3://{config.output_bucket}/{silver_manifest_key}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize raw JSONL shards into MokioFactory SFT schema.")
    parser.add_argument("--config", required=True, type=Path, help="Path to configs/cleaning/*.yaml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    normalize(args.config)


if __name__ == "__main__":
    main()
