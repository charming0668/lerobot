 #!/usr/bin/env bash
 set -e
 
 source /data1/hmcai/miniconda3/etc/profile.d/conda.sh
 conda activate lerobot
 
 cd /data1/hmcai/lerobot
 
 export HF_HOME="/data1/hmcai/.cache/huggingface"
 export CUDA_VISIBLE_DEVICES=0,1,2,3
 
 export NCCL_P2P_DISABLE=1
 export NCCL_SHM_DISABLE=1
 export NCCL_IB_DISABLE=1
 
 RUN_TAG=$(date +%Y%m%d_%H%M%S)
 LOG_FILE="/data1/hmcai/lerobot/logs/train/groot_piper_pick_cube_${RUN_TAG}.log"
 OUTPUT_DIR="/data1/hmcai/lerobot/outputs/train/groot_piper_${RUN_TAG}"
 
 echo "Logging to: ${LOG_FILE}"
 echo "Output dir: ${OUTPUT_DIR}"
 
 torchrun --nproc_per_node=4 $(which lerobot-train) \
   --dataset.repo_id="ming/piper-pick-cube_20260904_211137" \
   --dataset.root="/data1/hmcai/lerobot/dataset/ming/piper-pick-cube_20260904_211137" \
   --policy.type=groot \
   --policy.base_model_path="/data1/hmcai/lerobot/downloads/GR00T-N1.7-3B" \
   --policy.embodiment_tag=new_embodiment \
   --policy.chunk_size=16 \
   --policy.n_action_steps=16 \
   --policy.use_relative_actions=true \
   --policy.relative_exclude_joints='["gripper"]' \
   --policy.use_bf16=true \
   --policy.push_to_hub=false \
   --batch_size=16 \
   --num_workers=4 \
   --steps=5000 \
   --save_checkpoint=true \
   --save_freq=1000 \
   --log_freq=1 \
   --output_dir="${OUTPUT_DIR}" \
   --job_name="groot_piper_${RUN_TAG}" \
   --wandb.enable=true 2>&1 | tee -a "${LOG_FILE}"
