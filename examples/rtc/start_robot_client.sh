#!/usr/bin/env bash
# 本机控臂：经 SSH 隧道连服务器 PolicyServer。
# 先在本机开隧道：ssh -L 18080:127.0.0.1:8080 <gpu-server>
# leftover 客户端暂无 --rename_map CLI，相机 key 必须直接用策略名。
set -euo pipefail

PYTHON="${PYTHON:-python}"
SERVER_ADDRESS="${SERVER_ADDRESS:-127.0.0.1:18080}"
PRETRAINED_PATH="${PRETRAINED_PATH:-/data1/hmcai/lerobot/outputs/pi05_rtc/checkpoints/030000/pretrained_model}"
TASK="${TASK:-Pick up the red cube and put it in the pen holder}"
ROBOT_ID="${ROBOT_ID:-my_bi_piper_follower}"
LEFT_ARM_PORT="${LEFT_ARM_PORT:-001E002D5246570620323934}"
RIGHT_ARM_PORT="${RIGHT_ARM_PORT:-003B00485246570620323934}"
CAMERAS="${CAMERAS:-{ base_0_rgb: {type: opencv, index_or_path: 2, width: 640, height: 480, fps: 30}, left_wrist_0_rgb: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}, right_wrist_0_rgb: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30}}}"

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false

exec "$PYTHON" -m lerobot.async_inference.robot_client \
  --server_address="$SERVER_ADDRESS" \
  --policy_type=pi05 \
  --pretrained_name_or_path="$PRETRAINED_PATH" \
  --policy_device=cuda \
  --client_device=cpu \
  --actions_per_chunk=50 \
  --chunk_size_threshold=0.5 \
  --fps=30 \
  --task="$TASK" \
  --rtc.enabled=true \
  --rtc.mode=trained \
  --rtc.execution_horizon=20 \
  --robot.type=bi_piper_follower \
  --robot.id="$ROBOT_ID" \
  --robot.left_arm_config.port="$LEFT_ARM_PORT" \
  --robot.right_arm_config.port="$RIGHT_ARM_PORT" \
  --robot.cameras="$CAMERAS"
