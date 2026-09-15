#!/usr/bin/env bash
# 控臂客户端：在物理连接 Piper 双臂的工控机/PC 上运行，连入 PolicyServer 评测
set -euo pipefail

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false

exec python -m lerobot.async_inference.robot_client \
  --server_address="127.0.0.1:8080" \
  --policy_type=groot \
  --pretrained_name_or_path="outputs/train/groot_piper_20260914_220121/checkpoints/004000/pretrained_model" \
  --policy_device=cuda \
  --client_device=cpu \
  --actions_per_chunk=16 \
  --chunk_size_threshold=0.5 \
  --fps=30 \
  --task="Pick up the cube and put it in the pen holder" \
  --rtc.enabled=false \
  --robot.type=bi_piper_follower \
  --robot.id="my_bi_piper_follower" \
  --robot.left_arm_config.port="001E002D5246570620323934" \
  --robot.right_arm_config.port="003B00485246570620323934" \
  --robot.left_arm_config.cameras="{ wrist: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30} }" \
  --robot.right_arm_config.cameras="{ wrist: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30}, front: {type: opencv, index_or_path: 2, width: 640, height: 480, fps: 30} }" \
  --confirm_chunk=true \
  --record_trajectory=true \
  --trajectory_dir="outputs/eval_traj_4000"

