#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${LLAMAFACTORY_IMAGE:-hiyouga/llamafactory:latest}"
DATA_DIR="${MOKIO_DATA_DIR:-${ROOT_DIR}/data/llamafactory/stage1_agent_code_v1}"
CONFIG_DIR="${ROOT_DIR}/configs/training/llamafactory"
OUTPUT_DIR="${MOKIO_OUTPUT_DIR:-${ROOT_DIR}/outputs/llamafactory}"
HF_CACHE_DIR="${HF_HOME:-${HOME}/.cache/huggingface}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This full runner requires an NVIDIA Linux host." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required." >&2
  exit 1
fi

for required_file in train.jsonl validation.jsonl dataset_info.json schema_audit.json; do
  if [[ ! -f "${DATA_DIR}/${required_file}" ]]; then
    echo "Missing ${DATA_DIR}/${required_file}. Run materialize_dataset.py first." >&2
    exit 1
  fi
done

mkdir -p "${OUTPUT_DIR}" "${HF_CACHE_DIR}"

docker run --rm \
  --gpus all \
  --ipc=host \
  -e HF_TOKEN \
  -e HF_ENDPOINT \
  -v "${DATA_DIR}:/workspace/dataset:ro" \
  -v "${CONFIG_DIR}:/workspace/config:ro" \
  -v "${OUTPUT_DIR}:/workspace/output" \
  -v "${HF_CACHE_DIR}:/root/.cache/huggingface" \
  "${IMAGE}" \
  llamafactory-cli train /workspace/config/qwen3_4b_qlora_sft_full.yaml
