#!/usr/bin/env python3
"""Generate an EvalPlus slice through an OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from evalplus.codegen import run_codegen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=("humaneval", "mbpp"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--path-file", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def target_path(args: argparse.Namespace) -> Path:
    identifier = args.model.strip("./").replace("/", "--")
    identifier += "_openai_temp_0.0.jsonl"
    return args.output_root / args.dataset / identifier


def main() -> None:
    args = parse_args()
    target = target_path(args)
    if args.overwrite:
        target.unlink(missing_ok=True)
        target.with_name(target.name.replace(".jsonl", ".raw.jsonl")).unlink(
            missing_ok=True
        )

    samples = run_codegen(
        model=args.model,
        dataset=args.dataset,
        root=str(args.output_root),
        backend="openai",
        base_url=args.base_url,
        greedy=True,
        id_range=[args.start, args.end],
        max_new_tokens=args.max_new_tokens,
        resume=not args.overwrite,
    )
    args.path_file.parent.mkdir(parents=True, exist_ok=True)
    args.path_file.write_text(str(Path(samples).resolve()) + "\n", encoding="utf-8")
    print(f"samples: {samples}")


if __name__ == "__main__":
    main()

