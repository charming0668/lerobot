#!/usr/bin/env bash
# 本机控 Piper：经 SSH 隧道连接远端 GPU 服务器的 OpenPI π₀.₅ gRPC PolicyServer (方案 B)
# 客户端不加载权重、不跑推理；仅负责 CAN 底盘控制与 RealSense 相机采集传输
#
# 用法:
#   bash scripts/eval/start_openpi_robot_client.sh --probe   # 只测隧道 + gRPC Ready，不动机械臂 / 不 setup CAN
#   bash scripts/eval/start_openpi_robot_client.sh           # 测通后启动 robot_client 闭环控臂
#
# 环境变量覆盖示例: SSH_HOST / LOCAL_PORT / REMOTE_PORT / TRAJECTORY_DIR
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

# 环境激活：兼容工控机与本地环境；两边都失败则退出
if [ -f /home/ming/miniforge3/etc/profile.d/conda.sh ]; then
  source /home/ming/miniforge3/etc/profile.d/conda.sh
  conda activate lerobot
elif [ -f /data1/hmcai/miniconda3/etc/profile.d/conda.sh ]; then
  source /data1/hmcai/miniconda3/etc/profile.d/conda.sh
  conda activate lerobot
else
  echo "错误: 未找到 conda.sh（尝试过 /home/ming/miniforge3 与 /data1/hmcai/miniconda3）" >&2
  exit 1
fi

# SSH 目标：默认 amax / 3090（~/.ssh/config）；也可设 SSH_HOST=10.10.18.152
SSH_HOST="${SSH_HOST:-3090}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
REMOTE_PORT="${REMOTE_PORT:-8080}"
SERVER_ADDRESS="127.0.0.1:${LOCAL_PORT}"

# 臂口 / USB-CAN
LEFT_ARM_PORT="${LEFT_ARM_PORT:-001E002D5246570620323934}"
RIGHT_ARM_PORT="${RIGHT_ARM_PORT:-003B00485246570620323934}"
USB_CAN_SERIAL_1="${USB_CAN_SERIAL_1:-0019002E5443570A20393433}"
USB_CAN_SERIAL_2="${USB_CAN_SERIAL_2:-001700395443570A20393433}"
CAN_SERIALS="${CAN_SERIALS:-${USB_CAN_SERIAL_1},${USB_CAN_SERIAL_2},${LEFT_ARM_PORT},${RIGHT_ARM_PORT}}"

# 相机序列号（左右腕 + 右臂 front）
LEFT_WRIST_CAM="${LEFT_WRIST_CAM:-352122272576}"
RIGHT_WRIST_CAM="${RIGHT_WRIST_CAM:-352122273050}"
RIGHT_FRONT_CAM="${RIGHT_FRONT_CAM:-050522071191}"

TRAJECTORY_DIR="${TRAJECTORY_DIR:-outputs/eval_traj_openpi_30000}"
ROBOT_ID="${ROBOT_ID:-my_bi_piper_follower}"
TASK="${TASK:-Pick up the cube and put it in the pen holder}"

realsense_cam_yaml() {
  local serial="$1"
  printf '{type: intelrealsense, serial_number_or_name: '\''%s'\'', width: 640, height: 480, fps: 30, warmup_s: 2}' "${serial}"
}

PROBE_ONLY=0
for arg in "$@"; do
  case "${arg}" in
    --probe|-p) PROBE_ONLY=1 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: ${arg}" >&2
      echo "用法: $0 [--probe]" >&2
      exit 2
      ;;
  esac
done

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false

port_listening() {
  ss -ltn "sport = :${LOCAL_PORT}" | awk 'NR>1 {found=1} END {exit !found}'
}

ensure_tunnel() {
  if port_listening; then
    echo "==> 本地 ${LOCAL_PORT} 已在监听，复用现有转发"
    ss -ltnp "sport = :${LOCAL_PORT}" || true
    return 0
  fi

  echo "==> 建立 SSH 隧道: 127.0.0.1:${LOCAL_PORT} -> ${SSH_HOST}:127.0.0.1:${REMOTE_PORT}"
  ssh -f -N \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "${SSH_HOST}"

  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if port_listening; then
      echo "==> 隧道已监听 127.0.0.1:${LOCAL_PORT}"
      return 0
    fi
    sleep 0.2
  done

  echo "错误: 隧道启动后 ${LOCAL_PORT} 仍未监听" >&2
  return 1
}

probe_grpc() {
  echo "==> TCP 探测 ${SERVER_ADDRESS}"
  if ! timeout 3 bash -c "echo >/dev/tcp/127.0.0.1/${LOCAL_PORT}"; then
    echo "错误: TCP 连接 ${SERVER_ADDRESS} 失败" >&2
    echo "提示: 请确认 GPU 服务器上是否已启动 OpenPI gRPC PolicyServer（端口 ${REMOTE_PORT}）" >&2
    return 1
  fi
  echo "    TCP 连接成功"

  echo "==> gRPC Ready() 探测 OpenPI π₀.₅ PolicyServer"
  if ! python - "${SERVER_ADDRESS}" <<'PY'
import sys
import time
import grpc
from lerobot.transport import services_pb2, services_pb2_grpc

address = sys.argv[1]
channel = grpc.insecure_channel(address)
try:
    grpc.channel_ready_future(channel).result(timeout=8)
    stub = services_pb2_grpc.AsyncInferenceStub(channel)
    t0 = time.perf_counter()
    stub.Ready(services_pb2.Empty(), timeout=8)
    dt = time.perf_counter() - t0
    print(f"    gRPC Ready 成功 ({dt:.3f}s)  server={address}")
except Exception as exc:
    print(f"错误: gRPC Ready 失败: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    channel.close()
PY
  then
    echo "提示: Ready 失败时请检查服务端日志，确认模型是否仍在 JIT Warmup 中" >&2
    return 1
  fi
}

ensure_tunnel
probe_grpc

if [[ "${PROBE_ONLY}" -eq 1 ]]; then
  echo "==> 通信测试完成。未启动 robot_client，机械臂未连接，未 setup CAN。"
  exit 0
fi

echo "==> setup CAN"
lerobot-setup-can --mode=setup --usb_can_serials="${CAN_SERIALS}"

LEFT_CAMERAS="{ wrist: $(realsense_cam_yaml "${LEFT_WRIST_CAM}") }"
RIGHT_CAMERAS="{ wrist: $(realsense_cam_yaml "${RIGHT_WRIST_CAM}"), front: $(realsense_cam_yaml "${RIGHT_FRONT_CAM}") }"

echo "==> 启动 robot_client（连 OpenPI π₀.₅ gRPC PolicyServer）；目标: ${SERVER_ADDRESS}"
# OpenPI action_horizon=10，将 actions_per_chunk 设为 10 对齐
# chunk_size_threshold=0.5: 剩余 5 步时异步触发下一次推理，确保动作连续无顿挫
exec python -m lerobot.async_inference.robot_client \
  --server_address="${SERVER_ADDRESS}" \
  --policy_type=pi05 \
  --pretrained_name_or_path=openpi-pi05 \
  --policy_device=cpu \
  --client_device=cpu \
  --actions_per_chunk=10 \
  --chunk_size_threshold=0.5 \
  --fps=30 \
  --task="${TASK}" \
  --robot.type=bi_piper_follower \
  --robot.id="${ROBOT_ID}" \
  --robot.left_arm_config.port="${LEFT_ARM_PORT}" \
  --robot.right_arm_config.port="${RIGHT_ARM_PORT}" \
  --robot.left_arm_config.cameras="${LEFT_CAMERAS}" \
  --robot.right_arm_config.cameras="${RIGHT_CAMERAS}" \
  --rename_map='{"observation.images.right_front": "observation.images.base_0_rgb", "observation.images.left_wrist": "observation.images.left_wrist_0_rgb", "observation.images.right_wrist": "observation.images.right_wrist_0_rgb"}' \
  --confirm_chunk=true \
  --record_trajectory=true \
  --trajectory_dir="${TRAJECTORY_DIR}"
