#!/usr/bin/env bash
# 本机控臂：经 SSH 隧道连接远端 GPU 服务器（4090）的 GR00T PolicyServer
#
# 用法:
#   bash scripts/eval/start_groot_robot_client.sh --probe   # 只测隧道 + gRPC Ready，不动机械臂
#   bash scripts/eval/start_groot_robot_client.sh           # 测通后：启动先回零，Enter=开始/暂停，Space=回零，q/Esc=退出
#
# 隧道、CAN 初始化、环境激活都由本脚本完成；本机端口被占用时用 LOCAL_PORT=18080 覆盖
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 本脚本用裸 python，先激活本机 lerobot 环境；切到仓库根目录是为了解析下面的相对路径
source /home/ming/miniforge3/etc/profile.d/conda.sh
conda activate lerobot

SSH_HOST="${SSH_HOST:-4090}"   # ~/.ssh/config: 4090 -> 10.10.18.149
LOCAL_PORT="${LOCAL_PORT:-8080}"
REMOTE_PORT="${REMOTE_PORT:-8080}"
SERVER_ADDRESS="127.0.0.1:${LOCAL_PORT}"
CAN_SERIALS="${CAN_SERIALS:-0019002E5443570A20393433,001700395443570A20393433,001E002D5246570620323934,003B00485246570620323934}"

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
    return 1
  fi
  echo "    TCP 通"

  echo "==> gRPC Ready() 探测 PolicyServer（会重置服务端会话，不加载新权重、不控臂）"
  python - "${SERVER_ADDRESS}" <<'PY'
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
}

ensure_tunnel
probe_grpc

if [[ "${PROBE_ONLY}" -eq 1 ]]; then
  echo "==> 通信测试完成。未启动 robot_client，机械臂未连接。"
  exit 0
fi

echo "==> setup CAN"
lerobot-setup-can --mode=setup --usb_can_serials="${CAN_SERIALS}"

echo "==> 启动 robot_client（GR00T, 无 RTC）；连 ${SERVER_ADDRESS}"
exec python -m lerobot.async_inference.robot_client \
  --server_address="${SERVER_ADDRESS}" \
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
  --robot.left_arm_config.cameras="{ wrist: {type: intelrealsense, serial_number_or_name: '352122272576', width: 640, height: 480, fps: 30, warmup_s: 2} }" \
  --robot.right_arm_config.cameras="{ wrist: {type: intelrealsense, serial_number_or_name: '352122273050', width: 640, height: 480, fps: 30, warmup_s: 2}, front: {type: intelrealsense, serial_number_or_name: '050522071191', width: 640, height: 480, fps: 30, warmup_s: 2} }" \
  --confirm_chunk=true \
  --record_trajectory=true \
  --trajectory_dir="outputs/eval_traj_4000"

