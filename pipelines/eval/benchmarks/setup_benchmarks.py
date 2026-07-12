#!/usr/bin/env python3
"""Clone pinned benchmark sources and create isolated uv environments."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/eval/benchmarks/stage1_phase3.yaml",
    )
    parser.add_argument(
        "--benchmark",
        choices=("all", "bfcl", "evalplus", "tau3"),
        default="all",
    )
    parser.add_argument("--no-install", action="store_true")
    return parser.parse_args()


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    environment = os.environ.copy()
    if command[0] == "git":
        environment["GIT_LFS_SKIP_SMUDGE"] = "1"
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def checkout_source(name: str, spec: dict[str, Any], source_root: Path) -> Path:
    destination = source_root / name
    if not (destination / ".git").exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                spec["repository"],
                str(destination),
            ]
        )

    sparse_path = spec.get("sparse_path")
    if sparse_path:
        sparse_paths = [sparse_path] if isinstance(sparse_path, str) else sparse_path
        run(["git", "sparse-checkout", "init", "--cone"], cwd=destination)
        run(["git", "sparse-checkout", "set", *sparse_paths], cwd=destination)

    run(
        ["git", "fetch", "--depth", "1", "origin", spec["revision"]],
        cwd=destination,
    )
    run(["git", "checkout", "--detach", spec["revision"]], cwd=destination)
    return destination


def create_uv_environment(
    name: str,
    spec: dict[str, Any],
    source: Path,
    env_root: Path,
) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required: https://docs.astral.sh/uv/")

    if name == "tau3":
        command = [uv, "sync", "--project", str(source)]
        for extra in spec.get("extras", []):
            command.extend(["--extra", extra])
        run(command)
        return source / ".venv"

    environment = env_root / name
    environment.parent.mkdir(parents=True, exist_ok=True)
    if not (environment / "bin/python").exists():
        run([uv, "venv", "--python", spec["python"], str(environment)])
    package_path = source / spec.get("package_path", ".")
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(environment / "bin/python"),
            "-e",
            str(package_path),
        ]
    )
    return environment


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    source_root = resolve_path(config["paths"]["source_root"])
    env_root = resolve_path(config["paths"]["env_root"])
    selected = (
        config["benchmarks"].keys()
        if args.benchmark == "all"
        else (args.benchmark,)
    )

    manifest_path = source_root.parent / "setup_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "benchmarks": {},
        }
    manifest["updated_at"] = datetime.now(UTC).isoformat()
    manifest["config"] = str(args.config.resolve())
    for name in selected:
        spec = config["benchmarks"][name]
        source = checkout_source(name, spec, source_root)
        environment = None
        if not args.no_install:
            environment = create_uv_environment(name, spec, source, env_root)
        manifest["benchmarks"][name] = {
            "repository": spec["repository"],
            "revision": spec["revision"],
            "source": str(source),
            "environment": str(environment) if environment else None,
        }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
