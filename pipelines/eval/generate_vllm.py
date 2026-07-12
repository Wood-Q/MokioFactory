#!/usr/bin/env python3
"""Generate deterministic holdout predictions with vLLM and optional LoRA."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer
from vllm import LLM, SamplingParams
from vllm.lora.request import LoRARequest


TARGET_ROLES = {"gpt", "assistant", "function_call"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=Path)
    parser.add_argument("output_file", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9)
    parser.add_argument("--max-num-seqs", type=int, default=32)
    parser.add_argument("--enforce-eager", action="store_true")
    return parser.parse_args()


def load_json(value: str, field: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {field}: {exc}") from exc


def assistant_message(value: str, tool_call: bool) -> dict[str, Any]:
    if not tool_call:
        return {"role": "assistant", "content": value}
    calls = load_json(value, "function_call")
    if isinstance(calls, dict):
        calls = [calls]
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call.get("arguments", call.get("parameters", {})),
                },
            }
            for call in calls
        ],
    }


def convert_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message["from"]
    value = message["value"]
    if role in {"human", "user"}:
        return {"role": "user", "content": value}
    if role in {"gpt", "assistant"}:
        return assistant_message(value, tool_call=False)
    if role == "function_call":
        return assistant_message(value, tool_call=True)
    if role in {"observation", "tool"}:
        return {"role": "tool", "content": value}
    if role == "system":
        return {"role": "system", "content": value}
    raise ValueError(f"unsupported conversation role: {role}")


def split_prompt_and_label(row: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    conversations = row["conversations"]
    if not conversations or conversations[-1]["from"] not in TARGET_ROLES:
        raise ValueError(f"row {row.get('id')} does not end with an assistant target")

    target = conversations[-1]
    label = target["value"]
    messages = [convert_message(message) for message in conversations[:-1]]
    system = row.get("system", "").strip()
    if system and (not messages or messages[0]["role"] != "system"):
        messages.insert(0, {"role": "system", "content": system})
    return messages, label


def load_rows(path: Path, limit: int | None) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input_file, args.limit)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)

    prompts = []
    labels = []
    for row in rows:
        messages, label = split_prompt_and_label(row)
        tools = load_json(row["tools"], "tools") if row.get("tools") else None
        prompts.append(
            tokenizer.apply_chat_template(
                messages,
                tools=tools,
                tokenize=False,
                add_generation_prompt=True,
            )
        )
        labels.append(label)

    model = LLM(
        model=args.model,
        trust_remote_code=True,
        enable_lora=bool(args.adapter),
        max_lora_rank=64,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_memory_utilization,
        max_num_seqs=args.max_num_seqs,
        enforce_eager=args.enforce_eager,
    )
    sampling = SamplingParams(temperature=0.0, max_tokens=args.max_new_tokens)
    lora_request = LoRARequest("mokio-agent-code", 1, args.adapter) if args.adapter else None
    outputs = model.generate(prompts, sampling, lora_request=lora_request)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    with args.output_file.open("w", encoding="utf-8") as handle:
        for row, prompt, label, output in zip(rows, prompts, labels, outputs, strict=True):
            result = {
                "id": row.get("id"),
                "source_dataset": row.get("source_dataset"),
                "prompt": prompt,
                "predict": output.outputs[0].text.strip(),
                "label": label,
            }
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    print(f"generated: {len(rows)}")
    print(f"output: {args.output_file}")


if __name__ == "__main__":
    main()
