#!/usr/bin/env python3
"""Score generated tool calls from a JSONL prediction file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("prediction_file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--examples", type=int, default=5)
    return parser.parse_args()


def _decode_json(value: str) -> Any:
    value = value.strip()
    fenced = CODE_FENCE_RE.match(value)
    if fenced:
        value = fenced.group(1).strip()
    return json.loads(value)


def _as_calls(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("tool-call payload must be an object or a list of objects")
    return payload


def extract_calls(text: str) -> tuple[list[dict[str, Any]] | None, bool]:
    blocks = TOOL_CALL_RE.findall(text)
    if blocks:
        calls: list[dict[str, Any]] = []
        try:
            for block in blocks:
                calls.extend(_as_calls(_decode_json(block)))
        except (json.JSONDecodeError, ValueError):
            return None, False
        return calls, True

    try:
        return _as_calls(_decode_json(text)), False
    except (json.JSONDecodeError, ValueError):
        return None, False


def normalize_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for call in calls:
        name = call.get("name")
        arguments = call.get("arguments", call.get("parameters", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        normalized.append({"name": name, "arguments": arguments})
    return normalized


def score_file(path: Path, example_limit: int) -> dict[str, Any]:
    counts = {
        "total": 0,
        "json_valid": 0,
        "wrapper_valid": 0,
        "call_count_exact": 0,
        "tool_names_exact": 0,
        "arguments_exact": 0,
        "tool_calls_exact": 0,
    }
    failures = []

    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            counts["total"] += 1
            predicted, has_wrapper = extract_calls(row["predict"])
            expected, _ = extract_calls(row["label"])

            if predicted is not None:
                counts["json_valid"] += 1
            if predicted is not None and has_wrapper:
                counts["wrapper_valid"] += 1
            if expected is None:
                raise ValueError(f"invalid label tool call at line {line_number}")

            expected = normalize_calls(expected)
            predicted = normalize_calls(predicted) if predicted is not None else None
            count_exact = predicted is not None and len(predicted) == len(expected)
            names_exact = count_exact and [item["name"] for item in predicted] == [
                item["name"] for item in expected
            ]
            arguments_exact = names_exact and [item["arguments"] for item in predicted] == [
                item["arguments"] for item in expected
            ]

            counts["call_count_exact"] += int(count_exact)
            counts["tool_names_exact"] += int(names_exact)
            counts["arguments_exact"] += int(arguments_exact)
            counts["tool_calls_exact"] += int(arguments_exact)

            if not arguments_exact and len(failures) < example_limit:
                failures.append(
                    {
                        "line": line_number,
                        "prompt": row.get("prompt", ""),
                        "prediction": row["predict"],
                        "label": row["label"],
                    }
                )

    total = counts["total"]
    rates = {
        key: round(value / total, 6)
        for key, value in counts.items()
        if key != "total"
    } if total else {}
    return {"file": str(path), "counts": counts, "rates": rates, "failures": failures}


def main() -> None:
    args = parse_args()
    report = score_file(args.prediction_file, args.examples)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
