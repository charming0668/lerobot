#!/usr/bin/env bash
# 策略服务端：在 GPU 服务器上启动 Piper pi05 RTC PolicyServer，预加载合并后的 030000 权重并 warmup
set -euo pipefail

export CUDA_VISIBLE_DEVICES=0
export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export HF_ENDPOINT="https://hf-mirror.com"

PROJECT_ROOT="/data1/hmcai/lerobot"
CONDA_ENV="/data1/hmcai/miniconda3/envs/lerobot"
PYTHON_BIN="${CONDA_ENV}/bin/python"
PRETRAINED_MODEL="${PROJECT_ROOT}/outputs/piper_pi05_merged_030000"
LOG_DIR="${PROJECT_ROOT}/logs/eval"
mkdir -p "${LOG_DIR}"

READY_FILE="${LOG_DIR}/policy_server_piper.ready"
if [ -e "${READY_FILE}" ]; then
  unlink "${READY_FILE}"
fi

exec "${PYTHON_BIN}" -m lerobot.async_inference.policy_server \
  --host="0.0.0.0" \
  --port=8080 \
  --fps=30 \
  --pretrained_path="${PRETRAINED_MODEL}" \
  --policy_type=pi05 \
  --device=cuda \
  --actions_per_chunk=50 \
  --warmup=true \
  --warmup_task="Pick up the cube and put it in the pen holder" \
  --ready_file="${READY_FILE}"

