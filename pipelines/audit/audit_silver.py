from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from pipelines.common.storage import (
    iter_jsonl,
    join_key,
    object_exists,
    payload_sha256,
    put_json,
    read_json,
    s3_client,
    upload_file,
    utc_now,
    write_jsonl,
)


def percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * quantile))
    return ordered[index]


def stable_sample(items: list[dict[str, Any]], size: int, seed: int, salt: str) -> list[dict[str, Any]]:
    if len(items) <= size:
        return list(items)
    digest = hashlib.sha256(salt.encode("utf-8")).digest()
    group_seed = seed + int.from_bytes(digest[:8], "big")
    return random.Random(group_seed).sample(items, size)


def valid_turn_order(messages: list[dict[str, Any]]) -> bool:
    conversational = [message for message in messages if message.get("role") != "system"]
    if not conversational:
        return False
    for index, message in enumerate(conversational):
        model_side = message.get("role") == "assistant"
        if model_side != (index % 2 == 1):
            return False
    return True


def inspect_record(
    record: dict[str, Any],
    validator: Draft202012Validator,
    policy: dict[str, Any],
) -> dict[str, Any]:
    messages = record.get("messages") if isinstance(record.get("messages"), list) else []
    tools = record.get("tools") if isinstance(record.get("tools"), list) else []
    total_chars = sum(len(message.get("content", "")) for message in messages if isinstance(message, dict))
    flags: list[str] = []
    schema_errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))

    if schema_errors:
        flags.append("schema_error")
    if any(not str(message.get("content", "")).strip() for message in messages if isinstance(message, dict)):
        flags.append("empty_message")
    if not any(message.get("role") == "assistant" for message in messages if isinstance(message, dict)):
        flags.append("missing_assistant")
    if not valid_turn_order(messages):
        flags.append("invalid_turn_order")
    if total_chars > int(policy["max_total_chars"]):
        flags.append("too_long")
    elif total_chars > int(policy["warn_total_chars"]):
        flags.append("long_sample")
    if len(messages) > int(policy["max_messages"]):
        flags.append("too_many_messages")
    if any(not isinstance(tool, dict) for tool in tools):
        flags.append("unparsed_tool_definition")
    if record.get("source_dataset") in policy.get("sources_requiring_tool_definitions", []) and not tools:
        flags.append("missing_tool_definition")

    function_calls = sum(message.get("name") == "function_call" for message in messages if isinstance(message, dict))
    observations = sum(message.get("role") == "tool" for message in messages if isinstance(message, dict))
    blocking_flags = set(policy.get("blocking_flags", []))
    return {
        "record": record,
        "flags": flags,
        "blocking": bool(blocking_flags.intersection(flags)),
        "metrics": {
            "total_chars": total_chars,
            "message_count": len(messages),
            "assistant_count": sum(message.get("role") == "assistant" for message in messages if isinstance(message, dict)),
            "function_call_count": function_calls,
            "observation_count": observations,
            "tool_definition_count": len(tools),
            "schema_error_count": len(schema_errors),
        },
        "schema_errors": [error.message for error in schema_errors[:5]],
    }


def source_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = [row["metrics"]["total_chars"] for row in rows]
    message_counts = [row["metrics"]["message_count"] for row in rows]
    flags = Counter(flag for row in rows for flag in row["flags"])
    role_sequences = Counter(
        " -> ".join(message["role"] for message in row["record"]["messages"])
        for row in rows
    )
    return {
        "record_count": len(rows),
        "eligible_count": sum(not row["blocking"] for row in rows),
        "blocked_count": sum(row["blocking"] for row in rows),
        "total_chars": {
            "min": min(lengths),
            "p50": percentile(lengths, 0.50),
            "p90": percentile(lengths, 0.90),
            "p99": percentile(lengths, 0.99),
            "max": max(lengths),
        },
        "message_count": {
            "min": min(message_counts),
            "p50": percentile(message_counts, 0.50),
            "p90": percentile(message_counts, 0.90),
            "max": max(message_counts),
        },
        "records_with_tool_definitions": sum(row["metrics"]["tool_definition_count"] > 0 for row in rows),
        "records_with_function_calls": sum(row["metrics"]["function_call_count"] > 0 for row in rows),
        "records_with_observations": sum(row["metrics"]["observation_count"] > 0 for row in rows),
        "flags": dict(sorted(flags.items())),
        "top_role_sequences": [
            {"sequence": sequence, "count": count}
            for sequence, count in role_sequences.most_common(5)
        ],
    }


def build_review_queue(
    inspected: list[dict[str, Any]],
    sampling: dict[str, Any],
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_flag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seed = int(sampling["seed"])

    for row in inspected:
        by_source[row["record"]["source_dataset"]].append(row)
        for flag in row["flags"]:
            by_flag[flag].append(row)

    for source, rows in sorted(by_source.items()):
        for row in stable_sample(rows, int(sampling["per_source"]), seed, f"source:{source}"):
            record_id = row["record"]["id"]
            selected[record_id] = row
            reasons[record_id].add(f"source_sample:{source}")

    for flag, rows in sorted(by_flag.items()):
        for row in stable_sample(rows, int(sampling["per_flag"]), seed, f"flag:{flag}"):
            record_id = row["record"]["id"]
            selected[record_id] = row
            reasons[record_id].add(f"flag_sample:{flag}")

    queue: list[dict[str, Any]] = []
    for record_id in sorted(selected):
        row = selected[record_id]
        queue.append(
            {
                "review_id": hashlib.sha256(f"silver-audit.v1:{record_id}".encode("utf-8")).hexdigest(),
                "record_id": record_id,
                "source_dataset": row["record"]["source_dataset"],
                "task_family": row["record"]["task_family"],
                "selection_reasons": sorted(reasons[record_id]),
                "flags": row["flags"],
                "metrics": row["metrics"],
                "record": row["record"],
                "review": {"status": "pending", "labels": [], "notes": ""},
            }
        )
    return queue


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stage 1 Phase 1 Silver Data Audit",
        "",
        f"- Gate: `{report['gate']['status']}`",
        f"- Records: `{report['summary']['record_count']}`",
        f"- Eligible for gold: `{report['summary']['eligible_count']}`",
        f"- Blocked: `{report['summary']['blocked_count']}`",
        f"- Blocked fraction: `{report['summary']['blocked_fraction']:.4%}`",
        f"- Manual review: `{report['manual_review']['status']}`",
        "",
        "## Source Profile",
        "",
        "| Source | Records | Eligible | Blocked | chars p50 | chars p99 | Tools | Function calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for source, profile in sorted(report["sources"].items()):
        lines.append(
            f"| `{source}` | {profile['record_count']} | {profile['eligible_count']} | "
            f"{profile['blocked_count']} | {profile['total_chars']['p50']} | "
            f"{profile['total_chars']['p99']} | {profile['records_with_tool_definitions']} | "
            f"{profile['records_with_function_calls']} |"
        )
    lines.extend(["", "## Flags", ""])
    for flag, count in sorted(report["summary"]["flags"].items()):
        lines.append(f"- `{flag}`: {count}")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["gate"]["decision"],
            "",
            "Production use still requires a human reviewer to update the generated review queue.",
            "",
        ]
    )
    return "\n".join(lines)


def audit(config_path: Path, *, overwrite: bool) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    input_config = config["input"]
    output = config["output"]
    client = s3_client()
    report_key = join_key(output["prefix"], output["report_filename"])
    review_key = join_key(output["prefix"], output["review_queue_filename"])
    manifest_key = join_key(output["prefix"], output["manifest_filename"])

    if not overwrite:
        existing = [key for key in (report_key, review_key, manifest_key) if object_exists(client, output["bucket"], key)]
        if existing:
            raise RuntimeError(f"Audit output already exists: {existing}. Use --overwrite to replace this run_id.")

    schema = json.loads(Path(input_config["schema_path"]).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    input_manifest = read_json(client, input_config["bucket"], input_config["manifest_key"])
    records = list(iter_jsonl(client, input_config["bucket"], input_config["shard_key"]))
    inspected = [inspect_record(record, validator, config["policy"]) for record in records]
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inspected:
        by_source[row["record"]["source_dataset"]].append(row)

    flag_counts = Counter(flag for row in inspected for flag in row["flags"])
    blocked_count = sum(row["blocking"] for row in inspected)
    blocked_fraction = blocked_count / len(inspected) if inspected else 1.0
    max_blocked_fraction = float(config["gate"]["max_blocked_fraction"])
    zero_tolerance_flags = set(config["gate"].get("zero_tolerance_flags", []))
    zero_tolerance_violations = {
        flag: flag_counts[flag]
        for flag in sorted(zero_tolerance_flags)
        if flag_counts[flag] > 0
    }
    gate_failed = blocked_fraction > max_blocked_fraction or bool(zero_tolerance_violations)
    gate_status = "failed" if gate_failed else ("passed_with_warnings" if flag_counts else "passed")
    decision = (
        "Automated quality gate passed for the Stage 1 smoke dataset; blocked records must be excluded from gold."
        if gate_status != "failed"
        else "Automated quality gate failed; fix or reject blocking records before building gold."
    )
    review_queue = build_review_queue(inspected, config["sampling"])
    report = {
        "audit_version": config["audit_version"],
        "created_at": utc_now(),
        "input": {
            "bucket": input_config["bucket"],
            "shard_key": input_config["shard_key"],
            "manifest_key": input_config["manifest_key"],
            "manifest_sha256": input_manifest["sha256"],
        },
        "summary": {
            "record_count": len(inspected),
            "eligible_count": len(inspected) - blocked_count,
            "blocked_count": blocked_count,
            "blocked_fraction": blocked_fraction,
            "flags": dict(sorted(flag_counts.items())),
        },
        "sources": {source: source_profile(rows) for source, rows in sorted(by_source.items())},
        "gate": {
            "status": gate_status,
            "max_blocked_fraction": max_blocked_fraction,
            "zero_tolerance_violations": zero_tolerance_violations,
            "decision": decision,
        },
        "manual_review": {
            "status": "pending",
            "review_queue_key": review_key,
            "sample_count": len(review_queue),
            "stage1_smoke_can_proceed": bool(config["gate"]["stage1_smoke_can_proceed_without_manual_approval"]),
            "production_requires_approval": bool(config["gate"]["production_requires_manual_approval"]),
        },
        "policy": config["policy"],
        "config_sha256": payload_sha256(config),
    }

    local_markdown = Path(output["local_markdown_path"])
    local_markdown.parent.mkdir(parents=True, exist_ok=True)
    local_markdown.write_text(report_markdown(report), encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmpdir:
        review_path = Path(tmpdir) / output["review_queue_filename"]
        review_meta = write_jsonl(review_queue, review_path)
        upload_file(client, output["bucket"], review_key, review_path)
        put_json(client, output["bucket"], report_key, report)
        manifest = {
            "layer": "audit",
            "audit_version": config["audit_version"],
            "created_at": utc_now(),
            "input_manifest": input_config["manifest_key"],
            "input_manifest_sha256": input_manifest["sha256"],
            "report_key": report_key,
            "review_queue": {"object_key": review_key, **review_meta},
            "config_sha256": payload_sha256(config),
            "gate_status": gate_status,
        }
        put_json(client, output["bucket"], manifest_key, manifest)

    print("Silver audit completed.")
    print(f"records: {len(inspected)}")
    print(f"eligible: {len(inspected) - blocked_count}")
    print(f"blocked: {blocked_count}")
    print(f"gate: {gate_status}")
    print(f"review samples: {len(review_queue)}")
    print(f"report: s3://{output['bucket']}/{report_key}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile silver SFT records and build a manual review queue.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audit(args.config, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
