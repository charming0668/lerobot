#!/usr/bin/env bash
# GPU 服务器：预加载 pi05 Piper checkpoint，warmup 成功后再开 gRPC。
# 用法：
#   bash examples/rtc/start_policy_server.sh
#   tmux new-session -s lerobot-policy -c /data1/hmcai/lerobot \
#     'bash examples/rtc/start_policy_server.sh'
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-/data1/hmcai/miniconda3/envs/lerobot/bin/python}"
PRETRAINED_PATH="${PRETRAINED_PATH:-/data1/hmcai/lerobot/outputs/pi05_rtc/checkpoints/030000/pretrained_model}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
FPS="${FPS:-30}"
DEVICE="${DEVICE:-cuda}"
ACTIONS_PER_CHUNK="${ACTIONS_PER_CHUNK:-50}"
WARMUP_TASK="${WARMUP_TASK:-Only pick up the red cube and put it in the pen holder}"
READY_FILE="${READY_FILE:-logs/policy_server.ready}"
LOG_FILE="${LOG_FILE:-logs/policy_server.log}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: python not found: $PYTHON" >&2
  exit 1
fi
if [[ ! -f "$PRETRAINED_PATH/model.safetensors" ]]; then
  echo "ERROR: checkpoint missing: $PRETRAINED_PATH/model.safetensors" >&2
  exit 1
fi
if ss -tln 2>/dev/null | grep -qE ":${PORT}\\b"; then
  echo "ERROR: port ${PORT} already in use" >&2
  exit 1
fi

mkdir -p logs
rm -f "$READY_FILE"

echo "Starting PolicyServer"
echo "  python=$PYTHON"
echo "  gpu=$CUDA_VISIBLE_DEVICES"
echo "  bind=${HOST}:${PORT}"
echo "  checkpoint=$PRETRAINED_PATH"
echo "  ready_file=$READY_FILE"
echo "  log_file=$LOG_FILE"

"$PYTHON" -m lerobot.async_inference.policy_server \
  --host="$HOST" \
  --port="$PORT" \
  --fps="$FPS" \
  --pretrained_path="$PRETRAINED_PATH" \
  --policy_type=pi05 \
  --device="$DEVICE" \
  --actions_per_chunk="$ACTIONS_PER_CHUNK" \
  --warmup=true \
  --warmup_task="$WARMUP_TASK" \
  --ready_file="$READY_FILE" \
  2>&1 | tee -a "$LOG_FILE"
