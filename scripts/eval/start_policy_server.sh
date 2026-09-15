#!/usr/bin/env bash
# 策略服务端：在 GPU 服务器上启动 pi05 RTC PolicyServer，预加载 030000 权重并 warmup
set -euo pipefail

export CUDA_VISIBLE_DEVICES=0
export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export HF_ENDPOINT="https://hf-mirror.com"

rm -f logs/policy_server.ready

exec /data1/hmcai/miniconda3/envs/lerobot/bin/python -m lerobot.async_inference.policy_server \
  --host="0.0.0.0" \
  --port=8080 \
  --fps=30 \
  --pretrained_path="/data1/hmcai/lerobot/outputs/pi05_rtc/checkpoints/030000/pretrained_model" \
  --policy_type=pi05 \
  --device=cuda \
  --actions_per_chunk=50 \
  --warmup=true \
  --warmup_task="Pick up the cube and put it in the pen holder" \
  --ready_file="logs/policy_server.ready"
