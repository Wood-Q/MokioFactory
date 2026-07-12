#!/usr/bin/env python3
"""Run pinned benchmark slices against an OpenAI-compatible model endpoint."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark", choices=("bfcl", "evalplus", "tau3"))
    parser.add_argument("--variant", choices=("base", "adapter"), required=True)
    parser.add_argument("--model-name")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--user-model-name")
    parser.add_argument("--user-base-url")
    parser.add_argument(
        "--evalplus-execution", choices=("docker", "local"), default="docker"
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/eval/benchmarks/stage1_phase3.yaml",
    )
    return parser.parse_args()


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def require_file(path: Path, description: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{description} not found: {path}")
    return path


def normalized_base_url(value: str) -> str:
    return value.rstrip("/") + "/v1"


def run_bfcl(args: argparse.Namespace, config: dict[str, Any]) -> None:
    spec = config["benchmarks"]["bfcl"]
    if args.model_path is None:
        raise ValueError("BFCL requires --model-path for tokenizer/config loading")

    env_root = resolve_path(config["paths"]["env_root"])
    bfcl = require_file(env_root / "bfcl/bin/bfcl", "BFCL executable")
    output = resolve_path(config["paths"]["output_root"]) / "bfcl" / args.variant
    output.mkdir(parents=True, exist_ok=True)
    run_ids = require_file(resolve_path(spec["run_ids_file"]), "BFCL run IDs")
    shutil.copy2(run_ids, output / "test_case_ids_to_generate.json")

    environment = os.environ.copy()
    environment.update(
        {
            "BFCL_PROJECT_ROOT": str(output),
            "REMOTE_OPENAI_BASE_URL": normalized_base_url(args.base_url),
            "REMOTE_OPENAI_API_KEY": "EMPTY",
            "REMOTE_OPENAI_TOKENIZER_PATH": str(args.model_path.resolve()),
        }
    )
    model = spec["model_registry_name"]
    generate = [
        str(bfcl),
        "generate",
        "--model",
        model,
        "--run-ids",
        "--skip-server-setup",
        "--local-model-path",
        str(args.model_path.resolve()),
        "--result-dir",
        "result",
    ]
    if args.overwrite:
        generate.append("--allow-overwrite")
    run(generate, env=environment)
    run(
        [
            str(bfcl),
            "evaluate",
            "--model",
            model,
            "--test-category",
            ",".join(spec["categories"]),
            "--result-dir",
            "result",
            "--score-dir",
            "score",
            "--partial-eval",
        ],
        env=environment,
    )


def evaluate_evalplus(
    args: argparse.Namespace,
    spec: dict[str, Any],
    environment: Path,
    dataset: str,
    samples: Path,
    output_root: Path,
) -> None:
    result_file = samples.with_suffix(".eval_results.json")
    if args.overwrite:
        result_file.unlink(missing_ok=True)

    if args.evalplus_execution == "local":
        evaluator = require_file(environment / "bin/evalplus.evaluate", "EvalPlus")
        run(
            [
                str(evaluator),
                "--dataset",
                dataset,
                "--samples",
                str(samples),
                "--output-file",
                str(result_file),
            ]
        )
        return

    relative_samples = samples.relative_to(output_root)
    relative_result = result_file.relative_to(output_root)
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--pids-limit",
            "256",
            "--memory",
            "4g",
            "--cpus",
            "4",
            "-v",
            f"{output_root}:/app",
            spec["docker_image"],
            "evalplus.evaluate",
            "--dataset",
            dataset,
            "--samples",
            f"/app/{relative_samples}",
            "--output-file",
            f"/app/{relative_result}",
        ]
    )


def run_evalplus(args: argparse.Namespace, config: dict[str, Any]) -> None:
    if not args.model_name:
        raise ValueError("evalplus requires --model-name")
    spec = config["benchmarks"]["evalplus"]
    env_root = resolve_path(config["paths"]["env_root"])
    environment = env_root / "evalplus"
    python = require_file(environment / "bin/python", "EvalPlus Python")
    output_root = (
        resolve_path(config["paths"]["output_root"]) / "evalplus" / args.variant
    ).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    bridge = PROJECT_ROOT / "pipelines/eval/benchmarks/evalplus_codegen.py"

    environment_variables = os.environ.copy()
    environment_variables.setdefault("OPENAI_API_KEY", "EMPTY")
    for dataset, dataset_spec in spec["datasets"].items():
        start, end = dataset_spec["id_range"]
        path_file = output_root / f".{dataset}_samples_path"
        command = [
            str(python),
            str(bridge),
            "--dataset",
            dataset,
            "--model",
            args.model_name,
            "--base-url",
            normalized_base_url(args.base_url),
            "--output-root",
            str(output_root),
            "--path-file",
            str(path_file),
            "--start",
            str(start),
            "--end",
            str(end),
        ]
        if args.overwrite:
            command.append("--overwrite")
        run(command, env=environment_variables)
        samples = require_file(
            Path(path_file.read_text(encoding="utf-8").strip()),
            f"{dataset} samples",
        )
        evaluate_evalplus(args, spec, environment, dataset, samples, output_root)


def run_tau3(args: argparse.Namespace, config: dict[str, Any]) -> None:
    if not args.model_name or not args.user_model_name or not args.user_base_url:
        raise ValueError(
            "tau3 requires --model-name, --user-model-name and --user-base-url"
        )

    spec = config["benchmarks"]["tau3"]
    source_root = resolve_path(config["paths"]["source_root"])
    source = source_root / "tau3"
    tau2 = require_file(source / ".venv/bin/tau2", "tau3 executable")
    run_name = f"mokio-stage1-{args.variant}"
    source_result = source / "data/simulations" / run_name
    output = resolve_path(config["paths"]["output_root"]) / "tau3" / args.variant
    if args.overwrite:
        shutil.rmtree(source_result, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)

    agent_args = json.dumps(
        {
            "api_base": normalized_base_url(args.base_url),
            "api_key": "EMPTY",
            "temperature": 0.0,
        }
    )
    user_args = json.dumps(
        {
            "api_base": normalized_base_url(args.user_base_url),
            "api_key": "EMPTY",
            "temperature": 0.0,
        }
    )
    run(
        [
            str(tau2),
            "run",
            "--domain",
            spec["domain"],
            "--agent-llm",
            f"openai/{args.model_name}",
            "--agent-llm-args",
            agent_args,
            "--user-llm",
            f"openai/{args.user_model_name}",
            "--user-llm-args",
            user_args,
            "--task-split-name",
            spec["task_split_name"],
            "--task-ids",
            *spec["task_ids"],
            "--num-trials",
            str(spec["num_trials"]),
            "--max-steps",
            str(spec["max_steps"]),
            "--max-concurrency",
            str(spec["max_concurrency"]),
            "--seed",
            str(spec["seed"]),
            "--save-to",
            run_name,
            "--verbose-logs",
        ],
        cwd=source,
    )
    require_file(source_result, "tau3 result")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_result, output, dirs_exist_ok=True)
    print(f"tau3 results: {output}")


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    if args.benchmark == "bfcl":
        run_bfcl(args, config)
    elif args.benchmark == "evalplus":
        run_evalplus(args, config)
    else:
        run_tau3(args, config)


if __name__ == "__main__":
    main()
