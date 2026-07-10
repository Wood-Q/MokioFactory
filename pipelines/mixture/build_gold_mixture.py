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

from pipelines.audit.audit_silver import inspect_record
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


def source_rng(seed: int, source: str, purpose: str) -> random.Random:
    digest = hashlib.sha256(f"{purpose}:{source}".encode("utf-8")).digest()
    return random.Random(seed + int.from_bytes(digest[:8], "big"))


def deterministic_sample(rows: list[dict[str, Any]], count: int, seed: int, source: str, purpose: str) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["record"]["id"])
    if count > len(ordered):
        raise RuntimeError(f"{source}: requested {count} records but only {len(ordered)} passed the audit gate.")
    return source_rng(seed, source, purpose).sample(ordered, count)


def stable_shuffle(records: list[dict[str, Any]], seed: int, split: str) -> list[dict[str, Any]]:
    shuffled = list(records)
    source_rng(seed, "all-sources", split).shuffle(shuffled)
    return shuffled


def upload_split(
    client: Any,
    *,
    bucket: str,
    prefix: str,
    split: str,
    records: list[dict[str, Any]],
    records_per_shard: int,
    overwrite: bool,
) -> list[dict[str, Any]]:
    shard_count = (len(records) + records_per_shard - 1) // records_per_shard
    keys = [join_key(prefix, f"split={split}/part-{index:06d}.jsonl") for index in range(shard_count)]
    if not overwrite:
        existing = [key for key in keys if object_exists(client, bucket, key)]
        if existing:
            raise RuntimeError(f"Gold shard already exists: {existing}. Use --overwrite to replace this run_id.")

    files: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        for index, key in enumerate(keys):
            start = index * records_per_shard
            shard_records = records[start : start + records_per_shard]
            path = temp_dir / f"part-{index:06d}.jsonl"
            metadata = write_jsonl((row["record"] for row in shard_records), path)
            upload_file(client, bucket, key, path)
            files.append({"object_key": key, **metadata})
    return files


def build(config_path: Path, *, overwrite: bool) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    input_config = config["input"]
    output = config["output"]
    selection = config["selection"]
    client = s3_client()
    manifest_key = join_key(output["prefix"], output["manifest_filename"])
    if not overwrite and object_exists(client, output["bucket"], manifest_key):
        raise RuntimeError(f"Gold manifest already exists: {manifest_key}. Use --overwrite to replace this run_id.")

    silver_manifest = read_json(client, input_config["bucket"], input_config["silver_manifest_key"])
    audit_report = read_json(client, input_config["bucket"], input_config["audit_report_key"])
    if audit_report["gate"]["status"] == "failed":
        raise RuntimeError("Audit gate failed; gold cannot be built from this silver version.")
    if audit_report["input"]["manifest_sha256"] != silver_manifest["sha256"]:
        raise RuntimeError("Audit report does not belong to the configured silver manifest.")
    if (
        audit_report["manual_review"]["status"] != "approved"
        and not bool(selection["allow_pending_manual_review_for_stage1_smoke"])
    ):
        raise RuntimeError("Manual review is not approved for this mixture.")

    schema = json.loads(Path(input_config["schema_path"]).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blocked_count = 0
    for record in iter_jsonl(client, input_config["bucket"], input_config["silver_shard_key"]):
        inspected = inspect_record(record, validator, audit_report["policy"])
        if inspected["blocking"]:
            blocked_count += 1
            continue
        by_source[record["source_dataset"]].append(inspected)

    seed = int(selection["seed"])
    validation_fraction = float(selection["validation_fraction"])
    train_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    selected_ids: list[str] = []

    for source, source_config in config["sources"].items():
        target = int(source_config["target_records"])
        selected = deterministic_sample(by_source[source], target, seed, source, "select")
        validation_count = round(target * validation_fraction)
        if validation_fraction > 0 and target > 1:
            validation_count = max(1, validation_count)
        validation = deterministic_sample(selected, validation_count, seed, source, "validation")
        validation_ids = {row["record"]["id"] for row in validation}
        train = [row for row in selected if row["record"]["id"] not in validation_ids]
        train_rows.extend(train)
        validation_rows.extend(validation)
        selected_ids.extend(row["record"]["id"] for row in selected)
        source_summary[source] = {
            "available_after_audit": len(by_source[source]),
            "selected": len(selected),
            "train": len(train),
            "validation": len(validation),
            "license": source_config["license"],
            "purpose": source_config["purpose"],
        }

    train_rows = stable_shuffle(train_rows, seed, "train")
    validation_rows = stable_shuffle(validation_rows, seed, "validation")
    records_per_shard = int(output["records_per_shard"])
    train_files = upload_split(
        client,
        bucket=output["bucket"],
        prefix=output["prefix"],
        split="train",
        records=train_rows,
        records_per_shard=records_per_shard,
        overwrite=overwrite,
    )
    validation_files = upload_split(
        client,
        bucket=output["bucket"],
        prefix=output["prefix"],
        split="validation",
        records=validation_rows,
        records_per_shard=records_per_shard,
        overwrite=overwrite,
    )

    manifest = {
        "layer": "gold",
        "schema_version": "sft.v1",
        "mixture_version": config["mixture_version"],
        "run_id": config["run_id"],
        "created_at": utc_now(),
        "input": {
            "silver_manifest_key": input_config["silver_manifest_key"],
            "silver_manifest_sha256": silver_manifest["sha256"],
            "audit_report_key": input_config["audit_report_key"],
            "audit_gate_status": audit_report["gate"]["status"],
            "audit_blocked_records_excluded": blocked_count,
            "manual_review_status": audit_report["manual_review"]["status"],
        },
        "governance": config["governance"],
        "selection": {
            "seed": seed,
            "sampling": "without_replacement",
            "validation_fraction": validation_fraction,
            "selected_id_sha256": hashlib.sha256("\n".join(sorted(selected_ids)).encode("utf-8")).hexdigest(),
        },
        "sources": source_summary,
        "splits": {
            "train": {"record_count": len(train_rows), "files": train_files},
            "validation": {"record_count": len(validation_rows), "files": validation_files},
        },
        "record_count": len(train_rows) + len(validation_rows),
        "config_sha256": payload_sha256(config),
    }
    put_json(client, output["bucket"], manifest_key, manifest)

    print("Gold mixture completed.")
    print(f"train: {len(train_rows)}")
    print(f"validation: {len(validation_rows)}")
    print(f"manifest: s3://{output['bucket']}/{manifest_key}")
    print(f"source counts: {dict(Counter(row['record']['source_dataset'] for row in train_rows + validation_rows))}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a deterministic gold SFT mixture from audited silver records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build(args.config, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
