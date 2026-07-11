from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml

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


ROLE_MAP = {
    "user": "human",
    "assistant": "gpt",
    "tool": "observation",
}
INPUT_ROLES = {"human", "observation"}
MODEL_ROLES = {"gpt", "function_call"}
ALLOWED_ROLES = INPUT_ROLES | MODEL_ROLES


def validate_sharegpt_turns(conversations: list[dict[str, str]]) -> None:
    if not conversations:
        raise ValueError("ShareGPT conversations must not be empty.")

    for index, message in enumerate(conversations):
        role = message.get("from")
        value = message.get("value")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"Unsupported ShareGPT role at index {index}: {role}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Empty ShareGPT message at index {index}: {role}")

        model_side = role in MODEL_ROLES
        if model_side != (index % 2 == 1):
            raise ValueError(f"Invalid ShareGPT turn order at index {index}: {role}")
        if role == "observation" and conversations[index - 1]["from"] != "function_call":
            raise ValueError(f"Observation at index {index} is not preceded by a function call.")
        if role == "function_call" and index + 1 < len(conversations):
            if conversations[index + 1]["from"] != "observation":
                raise ValueError(f"Function call at index {index} is not followed by an observation.")

    if conversations[0]["from"] != "human":
        raise ValueError("ShareGPT conversation must start with a human message.")
    if conversations[-1]["from"] in INPUT_ROLES:
        raise ValueError(f"ShareGPT conversation ends on input role: {conversations[-1]['from']}")


def repair_sharegpt_turns(conversations: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[str]]:
    repaired = list(conversations)
    repairs: list[str] = []
    if repaired and repaired[-1]["from"] == "observation":
        repaired.pop()
        repairs.append("trimmed_trailing_observation")
    elif repaired and repaired[-1]["from"] == "human":
        last_human = next(
            (index for index in range(len(repaired) - 1, -1, -1) if repaired[index]["from"] == "human"),
            None,
        )
        if last_human is None:
            raise ValueError("Cannot repair trailing input without a human turn boundary.")
        repaired = repaired[:last_human]
        repairs.append("trimmed_trailing_human_turn")

    validate_sharegpt_turns(repaired)
    return repaired, repairs


def validate_tools(tools: Any, record_id: str) -> list[dict[str, Any]]:
    if tools in (None, ""):
        return []
    if isinstance(tools, str):
        try:
            tools = json.loads(tools)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid tools JSON in record {record_id}: {exc}") from exc
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise ValueError(f"Unparsed tool definition in record {record_id}")
    return tools


def to_sharegpt(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    system_parts: list[str] = []
    conversations: list[dict[str, str]] = []
    for message in record["messages"]:
        role = message["role"]
        if role == "system":
            system_parts.append(message["content"])
            continue
        if role not in ROLE_MAP:
            raise ValueError(f"Unsupported canonical role: {role}")
        sharegpt_role = "function_call" if message.get("name") == "function_call" else ROLE_MAP[role]
        conversations.append({"from": sharegpt_role, "value": message["content"]})

    conversations, repairs = repair_sharegpt_turns(conversations)
    tools = validate_tools(record.get("tools"), record["id"])
    return {
        "id": record["id"],
        "source_dataset": record["source_dataset"],
        "conversations": conversations,
        "system": "\n\n".join(system_parts),
        "tools": json.dumps(tools, ensure_ascii=False) if tools else "",
    }, repairs


def gold_records(client: Any, bucket: str, manifest: dict[str, Any], split: str) -> Iterable[dict[str, Any]]:
    for file in manifest["splits"][split]["files"]:
        yield from iter_jsonl(client, bucket, file["object_key"])


def build_dataset_info(config: dict[str, Any]) -> dict[str, Any]:
    datasets = config["datasets"]
    output = config["output"]
    shared = {
        "formatting": "sharegpt",
        "columns": {
            "messages": "conversations",
            "system": "system",
            "tools": "tools",
        },
    }
    return {
        datasets["train_name"]: {"file_name": output["train_filename"], **shared},
        datasets["validation_name"]: {"file_name": output["validation_filename"], **shared},
    }


def export(config_path: Path, *, overwrite: bool) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    input_config = config["input"]
    output = config["output"]
    client = s3_client()
    keys = {
        "train": join_key(output["prefix"], output["train_filename"]),
        "validation": join_key(output["prefix"], output["validation_filename"]),
        "dataset_info": join_key(output["prefix"], output["dataset_info_filename"]),
        "schema_audit": join_key(output["prefix"], output["schema_audit_filename"]),
        "manifest": join_key(output["prefix"], output["manifest_filename"]),
    }
    if not overwrite:
        existing = [key for key in keys.values() if object_exists(client, output["bucket"], key)]
        if existing:
            raise RuntimeError(f"Export output already exists: {existing}. Use --overwrite to replace this run_id.")

    gold_manifest = read_json(client, input_config["bucket"], input_config["gold_manifest_key"])
    split_metadata: dict[str, dict[str, Any]] = {}
    role_counts: Counter[str] = Counter()
    repair_counts: Counter[str] = Counter()
    split_audit: dict[str, dict[str, int]] = {}
    function_call_records = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        for split in ("train", "validation"):
            converted: list[dict[str, Any]] = []
            repaired_records = 0
            for record in gold_records(client, input_config["bucket"], gold_manifest, split):
                try:
                    example, repairs = to_sharegpt(record)
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError(f"{split}: LLaMA-Factory schema audit failed for {record.get('id')}: {exc}") from exc
                converted.append(example)
                repair_counts.update(repairs)
                repaired_records += int(bool(repairs))
                roles = [message["from"] for message in example["conversations"]]
                role_counts.update(roles)
                function_call_records += int("function_call" in roles)

            expected_count = gold_manifest["splits"][split]["record_count"]
            if len(converted) != expected_count:
                raise RuntimeError(f"{split}: expected {expected_count} records, exported {len(converted)}.")
            path = temp_dir / output[f"{split}_filename"]
            metadata = write_jsonl(converted, path)
            upload_file(client, output["bucket"], keys[split], path)
            split_metadata[split] = {"object_key": keys[split], **metadata}
            split_audit[split] = {
                "input_records": expected_count,
                "output_records": len(converted),
                "repaired_records": repaired_records,
            }

    dataset_info = build_dataset_info(config)
    put_json(client, output["bucket"], keys["dataset_info"], dataset_info)
    schema_audit = {
        "schema": "llamafactory_sharegpt",
        "status": "passed_with_repairs" if repair_counts else "passed",
        "created_at": utc_now(),
        "splits": split_audit,
        "repair_counts": dict(sorted(repair_counts.items())),
        "rejected_records": 0,
        "policy": {
            "start_role": "human",
            "input_roles": sorted(INPUT_ROLES),
            "model_roles": sorted(MODEL_ROLES),
            "require_non_empty_messages": True,
            "require_function_call_observation_pairs": True,
            "require_model_side_ending": True,
            "repair_trailing_observation": "drop_observation_and_keep_function_call_target",
            "repair_trailing_human_turn": "trim_from_last_human_turn",
            "unrepairable_record_action": "fail_export",
        },
    }
    put_json(client, output["bucket"], keys["schema_audit"], schema_audit)
    manifest = {
        "layer": "training_export",
        "export_version": config["export_version"],
        "format": "llamafactory_sharegpt",
        "created_at": utc_now(),
        "input_gold_manifest_key": input_config["gold_manifest_key"],
        "input_gold_manifest_sha256": payload_sha256(gold_manifest),
        "datasets": config["datasets"],
        "files": {
            **split_metadata,
            "dataset_info": {"object_key": keys["dataset_info"]},
            "schema_audit": {"object_key": keys["schema_audit"]},
        },
        "record_count": sum(metadata["record_count"] for metadata in split_metadata.values()),
        "conversation_roles": dict(sorted(role_counts.items())),
        "records_with_function_calls": function_call_records,
        "schema_audit": schema_audit,
        "config_sha256": payload_sha256(config),
    }
    put_json(client, output["bucket"], keys["manifest"], manifest)

    print("LLaMA-Factory export completed.")
    print(f"train: {split_metadata['train']['record_count']}")
    print(f"validation: {split_metadata['validation']['record_count']}")
    print(f"function-call records: {function_call_records}")
    print(f"schema repairs: {dict(repair_counts)}")
    print(f"manifest: s3://{output['bucket']}/{keys['manifest']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export gold sft.v1 records to LLaMA-Factory ShareGPT JSONL.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export(args.config, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
