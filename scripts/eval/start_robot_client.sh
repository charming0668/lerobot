#!/usr/bin/env bash
# 控臂客户端：在本机运行，经 SSH 隧道（8080 -> GPU 服务器 8080）连入 PolicyServer 评测
set -euo pipefail

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false

exec python -m lerobot.async_inference.robot_client \
  --server_address="127.0.0.1:8080" \
  --policy_type=pi05 \
  --pretrained_name_or_path="/data1/hmcai/lerobot/outputs/piper_pi05_merged_030000" \
  --policy_device=cuda \
  --client_device=cpu \
  --actions_per_chunk=50 \
  --chunk_size_threshold=0.5 \
  --fps=30 \
  --task="Pick up the cube and put it in the pen holder" \
  --rtc.enabled=true \
  --rtc.mode=trained \
  --robot.type=bi_piper_follower \
  --robot.id="my_bi_piper_follower" \
  --robot.left_arm_config.port="001E002D5246570620323934" \
  --robot.right_arm_config.port="003B00485246570620323934" \
  --robot.cameras="{ base_0_rgb: {type: opencv, index_or_path: 2, width: 640, height: 480, fps: 30}, left_wrist_0_rgb: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}, right_wrist_0_rgb: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30}}"
