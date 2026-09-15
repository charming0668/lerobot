#!/bin/bash
set -eo pipefail

# 默认配置
POLICY_PATH="${1:-/data1/hmcai/lerobot/outputs/train/groot_piper_20260914_220121/checkpoints/004000/pretrained_model}"
DATASET_REPO="${2:-ming/piper-pick-cube_20260904_211137}"
DATASET_ROOT="${3:-/data1/hmcai/lerobot/dataset/ming/piper-pick-cube_20260904_211137}"
GPU_ID="${4:-1}"
NUM_SAMPLES="${5:-8}"
OUTPUT_DIR="${6:-/data1/hmcai/lerobot/outputs/eval_openloop_4000}"

echo "========================================================"
echo "Starting GROOT Open-Loop Evaluation on Dataset"
echo "  Policy:       $POLICY_PATH"
echo "  Dataset:      $DATASET_REPO"
echo "  GPU:          $GPU_ID"
echo "  Samples:      $NUM_SAMPLES"
echo "  Output Dir:   $OUTPUT_DIR"
echo "========================================================"

CUDA_VISIBLE_DEVICES="$GPU_ID" /data1/hmcai/miniconda3/envs/lerobot/bin/python /data1/hmcai/lerobot/examples/rtc/eval_openloop.py \
  --policy.path="$POLICY_PATH" \
  --dataset.repo_id="$DATASET_REPO" \
  --dataset.root="$DATASET_ROOT" \
  --num_samples="$NUM_SAMPLES" \
  --output_dir="$OUTPUT_DIR"

echo "Evaluation completed successfully! Results saved to $OUTPUT_DIR"

