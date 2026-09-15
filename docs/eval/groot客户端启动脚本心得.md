# GR00T 评测客户端启动脚本心得

记录 `scripts/eval/start_groot_robot_client.sh` 这次改造的结论与依据，便于以后照抄或排障。

## 1. 脚本与角色

- `scripts/eval/start_groot_robot_client.sh`：本机控臂入口。一条命令完成：激活环境 → 切仓库根 → 建/复用 SSH 隧道 → gRPC 探活 → CAN 初始化 → 启动 robot_client。
- 参考实现：`/home/ming/VLA/scripts/lerobot_eval.sh`（pi05 RTC 版，同结构）。`port_listening` / `ensure_tunnel` / `probe_grpc` 三个函数与它逐字节一致（用 diff 比对过）。

## 2. 两个必须的前置，缺一即失败

- 环境：脚本用裸 `python`。在 `(base)` 下执行 `python -m lerobot.async_inference.robot_client` 会报 `ModuleNotFoundError: No module named 'lerobot'`，所以脚本内部自己 `source /home/ming/miniforge3/etc/profile.d/conda.sh` 后 `conda activate lerobot`。
- 目录：脚本用相对路径（检查点字符串、轨迹目录 `outputs/...`），所以脚本内部 `cd` 到仓库根，与从哪个目录启动无关。

## 3. 隧道

- ssh 别名 `4090` = `10.10.18.149`（见 `~/.ssh/config`），等价命令是 `ssh -N -L 8080:127.0.0.1:8080 hmcai@10.10.18.149`。
- 脚本幂等：本地端口已在监听就复用现有转发，否则 `ssh -f -N` 建隧道并轮询确认监听。
- 端口可覆盖：`SSH_HOST` / `LOCAL_PORT` / `REMOTE_PORT`。本机 8080 常被占用（pi05 那套固定用 18080），冲突时用 `LOCAL_PORT=18080`，脚本内的 `--server_address` 会自动跟随。

## 4. probe（`--probe`）

- 只做三件事：TCP 探测、gRPC `Ready()`、退出。不启动 robot_client，不连机械臂。
- `Ready()` 语义：服务端 warmup 未完成时返回 UNAVAILABLE（`policy_server.py:231`），所以成功即代表服务端已加载完成；但它会调用 `_reset_server()` 重置会话，且只返回空响应，无法判断加载的是哪个策略或 checkpoint。

## 5. CAN 初始化

- 启动客户端前执行 `lerobot-setup-can --mode=setup --usb_can_serials=<四个适配器序列号>`（参数名用 `--help` 核实过）。
- 现状检查：`ip -br link show type can` 应看到 `can0`–`can3` 处于 UP。

## 6. 相机（本机实测，不能靠猜）

- 本机三台 RealSense 的 SDK 序列号：D405 `352122272576`（左腕）、D405 `352122273050`（右腕）、D435I `050522071191`（前视）。
- 每台相机在 V4L2 下暴露 6 个 `/dev/videoN` 节点；`udevadm` 报的 USB iSerial（如 `254623078083`）与 SDK 序列号不是同一串数字，配置里要填 SDK 序列号。
- 所以不能用 `opencv` + `index_or_path: 0/1/2`：0 和 2 属于同一台相机，1 是 metadata 节点（`ID_V4L_CAPABILITIES` 为空，不能取流）。
- 正确写法：`{ wrist: {type: intelrealsense, serial_number_or_name: '352122272576', width: 640, height: 480, fps: 30, warmup_s: 2} }`；左腕挂 `left_arm_config`，右腕与前视挂 `right_arm_config`。
- 字段规则（`configuration_realsense.py`）：`serial_number_or_name` 全数字按序列号匹配、非数字按名字匹配；`fps`/`width`/`height` 必须三个同时给或都不给；`warmup_s` 默认 1。

## 7. 键名与 `--rename_map`

- 送出的相机键名由臂前缀决定（`bi_piper_follower.py:92` 给每路相机加 `left_`/`right_` 前缀），三路合起来是 `observation.images.left_wrist`、`observation.images.right_wrist`、`observation.images.right_front`。
- 服务端处理顺序（`helpers.py:148`）：展开成 `observation.*` → 按客户端发来的 `rename_map` 改名 → 要求每个图像键都存在于 checkpoint 的 image features 里，否则抛 KeyError，消息里带 `missing=` 与 `policy_image_keys=`。
- 本次结论：服务端确认 checkpoint 校验集与客户端键名一致，因此不需要 `--rename_map`，保持为空即可。
- 以后自查位置：服务端日志的 `Observation rename_map: {...}` 行（`policy_server.py:153`）；不匹配时最直接的证据是 KeyError 文本；GR00T 另有模态键告警（`processor_groot.py:1570`）。

## 8. 检查点路径

- 客户端不校验该路径本地是否存在，只是把字符串发给服务端；服务端用 `Path(客户端字符串).resolve()` 与自己的预加载路径比较，相同则跳过重载（`policy_server.py:274`）。
- 所以相对路径能对上，前提是服务端从自己的仓库根启动（服务端脚本里有 `cd "$ROOT"`）。服务端换目录启动时，客户端这里要改成绝对路径。

## 9. 按键

- 由 `--confirm_chunk=true` 启用：`Enter` 运行/暂停切换（暂停是保持当前姿态，不回零），`Space` 回零后等 `Enter`，`q`/`Esc` 退出。
- TTY 模式只读本终端（`utils/keyboard_input.py` 的 `TerminalKeyListener`），终端失焦时按键不生效。

## 10. 未决事项

- 本机 `main` 与 `origin/main` 已分叉（各 2 个提交）。远端那两个提交回退了：pi05 脚本迁到 `scripts/eval/` 的改动、`AGENTS.md` 里对应的两条说明、`scripts/eval/start_groot_policy_server.sh` 的 `WARMUP_TASK` 无 red 版本。本地尚未 push。

