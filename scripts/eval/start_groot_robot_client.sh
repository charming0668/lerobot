 #!/usr/bin/env bash
 # 控臂客户端（与当前仓库代码相同）：连接 PolicyServer 评测
 set -euo pipefail
 
 PYTHON="${PYTHON:-/data1/hmcai/miniconda3/envs/lerobot/bin/python}"
 SERVER_ADDRESS="${SERVER_ADDRESS:-127.0.0.1:8080}"
 PRETRAINED_PATH="${PRETRAINED_PATH:-/data1/hmcai/lerobot/outputs/train/groot_piper_20260914_220121/checkpoints/004000/pretrained_model}"
 TASK="${TASK:-Pick up the red cube and put it in the pen holder}"
 ROBOT_ID="${ROBOT_ID:-my_bi_piper_follower}"
 LEFT_ARM_PORT="${LEFT_ARM_PORT:-001E002D5246570620323934}"
 RIGHT_ARM_PORT="${RIGHT_ARM_PORT:-003B00485246570620323934}"
 
 # 注意：相机名称必须与训练时的数据集键名严格对齐：
 # left_wrist, right_wrist, right_front
 CAMERAS="${CAMERAS:-{ right_front: {type: opencv, index_or_path: 2, width: 640, height: 480, fps: 30}, left_wrist: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}, right_wrist: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30}}}"
 
 export PYTHONUNBUFFERED=1
 export TOKENIZERS_PARALLELISM=false
 
 exec "$PYTHON" -m lerobot.async_inference.robot_client \
   --server_address="$SERVER_ADDRESS" \
   --policy_type=groot \
   --pretrained_name_or_path="$PRETRAINED_PATH" \
   --policy_device=cuda \
   --client_device=cpu \
   --actions_per_chunk=16 \
   --chunk_size_threshold=0.5 \
   --fps=30 \
   --task="$TASK" \
   --rtc.enabled=false \
   --robot.type=bi_piper_follower \
   --robot.id="$ROBOT_ID" \
   --robot.left_arm_config.port="$LEFT_ARM_PORT" \
   --robot.right_arm_config.port="$RIGHT_ARM_PORT" \
   --robot.cameras="$CAMERAS"
