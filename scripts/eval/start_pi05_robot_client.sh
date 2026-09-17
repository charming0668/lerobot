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
  --robot.left_arm_config.cameras="{ wrist: {type: intelrealsense, serial_number_or_name: '352122272576', width: 640, height: 480, fps: 30, warmup_s: 2} }" \
  --robot.right_arm_config.cameras="{ wrist: {type: intelrealsense, serial_number_or_name: '352122273050', width: 640, height: 480, fps: 30, warmup_s: 2}, front: {type: intelrealsense, serial_number_or_name: '050522071191', width: 640, height: 480, fps: 30, warmup_s: 2} }" \
  --rename_map='{"observation.images.right_front": "observation.images.base_0_rgb", "observation.images.left_wrist": "observation.images.left_wrist_0_rgb", "observation.images.right_wrist": "observation.images.right_wrist_0_rgb"}' \
  --confirm_chunk=true
