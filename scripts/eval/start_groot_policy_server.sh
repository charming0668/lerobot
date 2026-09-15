 #!/usr/bin/env bash
 # GPU 服务器：启动 GR00T 4000 步 PolicyServer
 set -euo pipefail
 
 ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
 cd "$ROOT"
 
 PYTHON="${PYTHON:-/data1/hmcai/miniconda3/envs/lerobot/bin/python}"
 PRETRAINED_PATH="${PRETRAINED_PATH:-/data1/hmcai/lerobot/outputs/train/groot_piper_20260914_220121/checkpoints/004000/pretrained_model}"
 HOST="${HOST:-0.0.0.0}"
 PORT="${PORT:-8080}"
 FPS="${FPS:-30}"
 DEVICE="${DEVICE:-cuda}"
 ACTIONS_PER_CHUNK="${ACTIONS_PER_CHUNK:-16}"
 WARMUP_TASK="${WARMUP_TASK:-Pick up the red cube and put it in the pen holder}"
 READY_FILE="${READY_FILE:-logs/policy_server_groot.ready}"
 LOG_FILE="${LOG_FILE:-logs/policy_server_groot.log}"
 
 export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
 export PYTHONUNBUFFERED=1
 export TOKENIZERS_PARALLELISM=false
 
 mkdir -p logs
 rm -f "$READY_FILE"
 
 echo "Starting GROOT PolicyServer (4000 steps checkpoint)"
 echo "  python=$PYTHON"
 echo "  gpu=$CUDA_VISIBLE_DEVICES"
 echo "  bind=${HOST}:${PORT}"
 echo "  checkpoint=$PRETRAINED_PATH"
 echo "  actions_per_chunk=$ACTIONS_PER_CHUNK"
 
 "$PYTHON" -m lerobot.async_inference.policy_server \
   --host="$HOST" \
   --port="$PORT" \
   --fps="$FPS" \
   --pretrained_path="$PRETRAINED_PATH" \
   --policy_type=groot \
   --device="$DEVICE" \
   --actions_per_chunk="$ACTIONS_PER_CHUNK" \
   --warmup=true \
   --warmup_task="$WARMUP_TASK" \
   --ready_file="$READY_FILE" 2>&1 | tee -a "$LOG_FILE"
