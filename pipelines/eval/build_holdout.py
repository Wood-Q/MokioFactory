from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from pipelines.audit.audit_silver import inspect_record
from pipelines.common.storage import iter_jsonl, payload_sha256, read_json, s3_client, utc_now, write_jsonl
from pipelines.export.export_llamafactory import to_sharegpt
from pipelines.mixture.build_gold_mixture import deterministic_sample, stable_shuffle


DATASET_NAMES = {
    "salesforce-xlam-function-calling-60k": "mokio_holdout_xlam",
    "salesforce-apigen-mt-5k": "mokio_holdout_apigen_mt",
    "openthoughts-agent-v1-sft": "mokio_holdout_openthoughts_agent",
}


def gold_ids(client: Any, bucket: str, manifest: dict[str, Any]) -> set[str]:
    selected: set[str] = set()
    for split in ("train", "validation"):
        for file in manifest["splits"][split]["files"]:
            selected.update(record["id"] for record in iter_jsonl(client, bucket, file["object_key"]))
    return selected


def dataset_info(files: dict[str, str]) -> dict[str, Any]:
    shared = {
        "formatting": "sharegpt",
        "columns": {
            "messages": "conversations",
            "system": "system",
            "tools": "tools",
        },
    }
    return {name: {"file_name": filename, **shared} for name, filename in files.items()}


def build(config_path: Path, *, overwrite: bool) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    input_config = config["input"]
    output = config["output"]
    destination = Path(output["local_dir"])
    manifest_path = destination / output["manifest_filename"]
    if manifest_path.exists() and not overwrite:
        raise RuntimeError(f"Holdout already exists: {manifest_path}. Use --overwrite to rebuild it.")
    destination.mkdir(parents=True, exist_ok=True)

    client = s3_client()
    silver_manifest = read_json(client, input_config["bucket"], input_config["silver_manifest_key"])
    audit_report = read_json(client, input_config["bucket"], input_config["audit_report_key"])
    gold_manifest = read_json(client, input_config["bucket"], input_config["gold_manifest_key"])
    excluded_ids = gold_ids(client, input_config["bucket"], gold_manifest)

    schema = json.loads(Path(input_config["schema_path"]).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in iter_jsonl(client, input_config["bucket"], input_config["silver_shard_key"]):
        if record["id"] in excluded_ids:
            continue
        inspected = inspect_record(record, validator, audit_report["policy"])
        if not inspected["blocking"]:
            candidates[record["source_dataset"]].append(inspected)

    seed = int(config["selection"]["seed"])
    all_records: list[dict[str, Any]] = []
    source_metadata: dict[str, Any] = {}
    info_files: dict[str, str] = {"mokio_holdout_all": output["all_filename"]}
    repair_counts: Counter[str] = Counter()

    for source, target_value in config["selection"]["sources"].items():
        target = int(target_value)
        selected = deterministic_sample(candidates[source], target, seed, source, "holdout")
        converted: list[dict[str, Any]] = []
        for row in selected:
            example, repairs = to_sharegpt(row["record"])
            converted.append(example)
            repair_counts.update(repairs)
        converted = stable_shuffle(converted, seed, source)
        filename = f"test_{source}.jsonl"
        metadata = write_jsonl(converted, destination / filename)
        dataset_name = DATASET_NAMES[source]
        info_files[dataset_name] = filename
        source_metadata[source] = {
            "available_after_gold_exclusion": len(candidates[source]),
            "selected": len(converted),
            "dataset_name": dataset_name,
            "file": {"filename": filename, **metadata},
        }
        all_records.extend(converted)

    all_records = stable_shuffle(all_records, seed, "holdout-all")
    all_metadata = write_jsonl(all_records, destination / output["all_filename"])
    info = dataset_info(info_files)
    info_path = destination / output["dataset_info_filename"]
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    selected_ids = sorted(record["id"] for record in all_records)
    overlap = excluded_ids.intersection(selected_ids)
    if overlap:
        raise RuntimeError(f"Holdout overlaps gold data: {len(overlap)} records")
    manifest = {
        "test_version": config["test_version"],
        "created_at": utc_now(),
        "selection": {
            "seed": seed,
            "sampling": "without_replacement",
            "selected_id_sha256": hashlib.sha256("\n".join(selected_ids).encode("utf-8")).hexdigest(),
        },
        "input": {
            "silver_manifest_key": input_config["silver_manifest_key"],
            "silver_manifest_sha256": payload_sha256(silver_manifest),
            "gold_manifest_key": input_config["gold_manifest_key"],
            "gold_manifest_sha256": payload_sha256(gold_manifest),
            "gold_ids_excluded": len(excluded_ids),
        },
        "record_count": len(all_records),
        "gold_overlap_count": len(overlap),
        "schema_repairs": dict(sorted(repair_counts.items())),
        "sources": source_metadata,
        "files": {
            "all": {"filename": output["all_filename"], **all_metadata},
            "dataset_info": {"filename": output["dataset_info_filename"]},
        },
        "config_sha256": payload_sha256(config),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("Independent holdout completed.")
    print(f"records: {len(all_records)}")
    print(f"gold overlap: {len(overlap)}")
    print(f"sources: {dict(Counter(record['source_dataset'] for record in all_records))}")
    print(f"dataset dir: {destination}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an independent LLaMA-Factory holdout from unused silver data.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build(args.config, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
