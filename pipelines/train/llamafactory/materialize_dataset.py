from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from pipelines.common.storage import payload_sha256, read_json, s3_client, utc_now


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def download_atomic(client: Any, bucket: str, key: str, destination: Path) -> None:
    partial = destination.with_suffix(f"{destination.suffix}.partial")
    partial.unlink(missing_ok=True)
    try:
        client.download_file(bucket, key, str(partial))
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def materialize(config_path: Path, *, overwrite: bool) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    input_config = config["input"]
    output = config["output"]
    destination = Path(output["local_dir"])
    destination.mkdir(parents=True, exist_ok=True)
    local_manifest_path = destination / output["local_manifest_filename"]
    if local_manifest_path.exists() and not overwrite:
        raise RuntimeError(f"Dataset is already materialized: {local_manifest_path}. Use --overwrite to refresh it.")

    client = s3_client()
    export_manifest = read_json(client, input_config["bucket"], input_config["export_manifest_key"])
    materialized_files: dict[str, Any] = {}
    for name in ("train", "validation", "dataset_info", "schema_audit"):
        source = export_manifest["files"][name]
        object_key = source["object_key"]
        local_path = destination / Path(object_key).name
        if local_path.exists() and not overwrite:
            raise RuntimeError(f"Local file already exists: {local_path}. Use --overwrite to refresh it.")
        download_atomic(client, input_config["bucket"], object_key, local_path)
        actual_sha256 = file_sha256(local_path)
        expected_sha256 = source.get("sha256")
        if expected_sha256 and actual_sha256 != expected_sha256:
            local_path.unlink(missing_ok=True)
            raise RuntimeError(f"Checksum mismatch for {object_key}: {actual_sha256} != {expected_sha256}")
        metadata = {
            "object_key": object_key,
            "local_path": str(local_path),
            "size_bytes": local_path.stat().st_size,
            "sha256": actual_sha256,
        }
        if local_path.suffix == ".jsonl":
            metadata["record_count"] = line_count(local_path)
            if metadata["record_count"] != source["record_count"]:
                raise RuntimeError(
                    f"Record count mismatch for {object_key}: "
                    f"{metadata['record_count']} != {source['record_count']}"
                )
        materialized_files[name] = metadata

    local_manifest = {
        "materialized_at": utc_now(),
        "source_export_manifest_key": input_config["export_manifest_key"],
        "source_export_manifest_sha256": payload_sha256(export_manifest),
        "record_count": export_manifest["record_count"],
        "files": materialized_files,
    }
    local_manifest_path.write_text(
        json.dumps(local_manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print("LLaMA-Factory dataset materialized.")
    print(f"train: {materialized_files['train']['record_count']}")
    print(f"validation: {materialized_files['validation']['record_count']}")
    print(f"dataset dir: {destination}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and verify a LLaMA-Factory dataset export from S3/MinIO.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    materialize(args.config, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
