#!/usr/bin/env bash
set -euo pipefail

variant=${1:?usage: serve_vllm.sh <base|adapter> <port> <gpu>}
port=${2:?usage: serve_vllm.sh <base|adapter> <port> <gpu>}
gpu=${3:?usage: serve_vllm.sh <base|adapter> <port> <gpu>}

MODEL_PATH=${MODEL_PATH:-/home/qhk/models/Qwen3-4B-Instruct-2507}
ADAPTER_PATH=${ADAPTER_PATH:-/home/qhk/MokioFactory/outputs/llamafactory/qwen3-4b-qlora-sft-full-v1}
SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-qwen3-base}
LORA_NAME=${LORA_NAME:-qwen3-agent}
VLLM_BIN=${VLLM_BIN:-/home/qhk/conda_envs/qwen4b-vllm-fa/bin/vllm}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-8192}
MAX_NUM_SEQS=${MAX_NUM_SEQS:-32}
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.8}

common=(
  serve "$MODEL_PATH"
  --host 127.0.0.1
  --port "$port"
  --served-model-name "$SERVED_MODEL_NAME"
  --dtype bfloat16
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-seqs "$MAX_NUM_SEQS"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --enforce-eager
  --trust-remote-code
  --enable-auto-tool-choice
  --tool-call-parser hermes
)

export CUDA_VISIBLE_DEVICES="$gpu"
export VLLM_USE_V1=0

case "$variant" in
  base)
    exec "$VLLM_BIN" "${common[@]}"
    ;;
  adapter)
    exec "$VLLM_BIN" "${common[@]}" \
      --enable-lora \
      --max-lora-rank 64 \
      --lora-modules "$LORA_NAME=$ADAPTER_PATH"
    ;;
  *)
    echo "variant must be base or adapter" >&2
    exit 2
    ;;
esac
