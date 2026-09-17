# RTC 实时推理动作生命周期与 execution_horizon 机制深度解析

本文档以 `lerobot.async_inference` 与 `lerobot.policies.rtc` 源码实现为基准，系统剖析从**动作下发**、**物理执行**、**下一次触发**到**新动作返回融合**的全链路闭环流程，并厘清 `execution_horizon`、`chunk_size_threshold` 与 `inference_delay` 在各阶段的具体职责。

---

## 1. 核心架构与并发模型

在客户端（[robot_client.py](/data1/hmcai/lerobot/src/lerobot/async_inference/robot_client.py:1)）中，系统由两个核心并发线程驱动：

1. **控制循环线程（Control Loop Thread）**：
   - 以固定频率（如 30 Hz，周期 dt = 33.3 ms）执行。
   - 严格串行从 `ActionQueue`（[action_queue.py](/data1/hmcai/lerobot/src/lerobot/policies/rtc/action_queue.py:1)）中提取单一动作（`queue.get()`）并直接下发到底层电机 CAN 总线。
2. **观测与调度线程（Observation & Dispatch Thread）**：
   - 高频轮询当前动作队列余量。
   - 当满足提前触发条件时，捕获当前相机帧与未执行动作残差（Leftover），打包异步发送给 PolicyServer。

---

## 2. 推理动作生命周期全流程

### 阶段一：当前动作 Chunk 的执行与消耗

假设单次推理生成的动作块大小为 H = 50 步（在 30 FPS 下对应约 1.67 秒的轨迹）：

1. 动作入队后，`ActionQueue` 内部同时维护两份数据：
   - `queue`：**服务端在发回前已经完成反归一化（Unnormalizer）的物理动作**，直接用于机械臂硬件执行（客户端本地不执行反归一化）。
   - `original_queue`：**服务端一同打包发回的归一化空间模型原始输出**，客户端仅在本地队列中缓存，用于下一次向服务端发回历史动作上下文（Leftover）。
2. 控制循环每周期消费一个动作步，消费游标 `last_index` 单调递增，剩余动作步数（`qsize()`）逐渐减小。

---

### 阶段二：提前触发下一次推理请求（Early Trigger）

为消除 GPU 计算与网络往返延迟导致的机器人停顿，系统不会等 50 步全部执行完才去推理，而是采用**水位线提前触发机制**：

1. **触发判断（[robot_client.py:567](/data1/hmcai/lerobot/src/lerobot/src/lerobot/async_inference/robot_client.py:567)）**：
   - 检查标志位：若 `_rtc_inflight == True`（即上一笔异步请求尚未返回），本次不触发，确保网络中最多仅有一笔待处理请求。
   - 检查队列水位：
     `rtc_queue.qsize() / action_chunk_size <= chunk_size_threshold`
     （若设为 0.5，则当 50 步动作执行了 25 步、剩余 25 步时立即触发）。
2. **状态快照与参数装配**：
   - **截取 Leftover**：调用 `rtc_queue.get_left_over()`，将当前尚未被执行的剩余动作（如第 26~50 步）切片保存为 `prev_chunk_left_over`。
   - **计算预期延迟（inference_delay）**：依据历史网络与推理 RTT 统计（`latency_tracker.max()`），换算为离散步数 d（例如 300 ms 约对应 9 步，且满足 d ≤ rtc_training_max_delay）。
   - **锁定当前上下文**：记录开始时间戳 `_rtc_request_start` 与发起时的游标 `_rtc_index_before`，设置 `_rtc_inflight = True`。
3. **打包发送**：向服务端发起 gRPC 请求，负载包含当前图像观测、`prev_chunk_left_over`、`inference_delay` 以及 `execution_horizon`。

---

### 阶段三：服务端条件化轨迹生成（Inpainting / Guided）

服务端 PolicyServer 在拿到客户端请求后，结合模型训练特性执行条件化生成：

1. **若为 trained 模式（针对以 rtc_training_max_delay 训练的模型，如本项目）**：
   - 提取 `prev_chunk_left_over` 的前 d 步作为已知硬前缀（`hard_prefix`）。
   - 在 Euler 积分流匹配去噪过程中，前 d 步动作在每个时间步被强制写死为已知值，时间标量设为 0；后 50 - d 步则由神经网络根据当前视觉与前缀完全自适应续写。
2. **若为 guided 模式（未经过 RTC 训练的模型）**：
   - 利用 `get_prefix_weights(inference_delay, execution_horizon, H)` 生成加权掩码。
   - 在每步 Euler 去噪中，计算新生成轨迹与旧轨迹的残差梯度，通过反向传播（autograd）施加牵引速度。

---

### 阶段四：等待期间机器人的连续执行（Zero-Stall Execution）

在 PolicyServer 处理推理请求与数据传输期间（耗时约 200~400 ms）：
- 机械臂控制循环**毫不停顿**，继续消费原有队列中的动作（第 26 步、第 27 步、第 28 步...）；
- 机器人动作完全连续平滑，外界观测不到任何因网络或算力瓶颈引起的抖动或顿挫。

---

### 阶段五：新动作返回、队列对齐与无缝融合

当客户端 gRPC 线程收到服务端返回的新 50 步 Chunk 时，触发核心合并逻辑：

1. **实测消耗步数换算（[robot_client.py:382](/data1/hmcai/lerobot/src/lerobot/src/lerobot/async_inference/robot_client.py:382)）**：
   `consumed = max(0, rtc_queue.get_action_index() - index_before)`
   计算在等待结果期间，机器人实际已经向前走掉了多少步。
2. **队列替换（[action_queue.py:157](/data1/hmcai/lerobot/src/lerobot/src/lerobot/policies/rtc/action_queue.py:157)）**：
   - 在 RTC 模式下，新 Chunk 的前 `consumed` 步对应的是机器人刚才已经执行过的时刻，直接丢弃（`clamped_delay = consumed`）。
   - 队列被新 Chunk 的剩余有效步数整体替换：
     `queue = processed_actions[clamped_delay:]`
   - 重置 `last_index = 0`。
3. **接缝余弦柔顺融合（Seam Blend，[robot_client.py:720](/data1/hmcai/lerobot/src/lerobot/src/lerobot/async_inference/robot_client.py:720)）**：
   - 为防止新旧 Chunk 交界处存在细微的位置跳变，计算当前机器人实时保持位姿（Held Action）与新动作首帧之间的 L2 空间距离。
   - 在前 n 步（通常 2~8 步）应用基于升余弦曲线的加权融合：
     `blended[:n] = (1 - alpha) * held + alpha * processed[:n]`
   - 彻底消解关节力矩突变。
4. **状态复位**：
   - 清除在途标记（`_rtc_inflight = False`）；
   - 系统自然滑入下一轮周期的监控中。

---

## 3. execution_horizon 的角色与精确界定

### 1. 它在整个流程中到底影响什么？

在 `src/lerobot/policies/rtc/modeling_rtc.py` 中，`execution_horizon` 是**过渡引导区间的上界终点（仅在 mode='guided' 模式下生效，在 mode='trained' 下互斥禁用）**：

```text
步骤索引:  0 ────────── d (inference_delay) ────────── E (execution_horizon) ────────── H (chunk_size: 50)
权重分布:  [   1.0 (完全硬锁/重合)   ] [      1.0 → 0.0 (平滑衰减)      ] [      0.0 (完全自由预测)       ]
```

- **0 到 d 步**：对应推理耗时期间机械臂已经走掉的动作。权重恒为 1.0，强行约束新轨迹起点与当前物理状态严密咬合。
- **d 到 E 步（受 execution_horizon 控制）**：过渡缓冲区。在此区间内，旧规划对新轨迹的约束力从 1.0 线性或指数衰减到 0.0。
- **E 到 H 步**：完全摆脱旧规划的牵制，完全服从最新视觉反馈生成未来轨迹。

### 2. 核心边界与参数配置准则

系统约束检验规则（[src/lerobot/rollout/context.py:120](/data1/hmcai/lerobot/src/lerobot/src/lerobot/rollout/context.py:120)）：

1. **下界约束**：`execution_horizon >= rtc_training_max_delay`
   - 过渡带终点不能小于硬延迟步数，否则过渡区间长度为负，失去平滑意义。
2. **上界约束**：`execution_horizon <= chunk_size - rtc_training_max_delay`
   - 过渡带不能侵占过多的预测空间，必须留出足够的步数供模型根据新视觉目标自由输出。

---

## 4. 机制时序总览图

```text
[ Control Loop ] ────执行第 0~24 步────> 执行第 25 步 ──────继续执行第 26~34 步─────> [接缝平滑融合] ──> 执行新动作
                                            │                                            ▲
                                    (满足 threshold=0.5)                                  │
                                            ▼                                            │
[ Observation  ] ────────────────── 捕获 Leftover (26~50步)                               │
                                     估算 delay=9 步                                     │
                                     异步发送 gRPC ─────────┐                            │
                                                            │ (网络传输)                  │ (返回新 50 步)
                                                            ▼                            │
[ PolicyServer ] ────────────────────────────────── GPU 流匹配去噪生成 ────────────────────┘
                                                    - 前 9 步: Hard Inpaint
                                                    - 9~20 步: execution_horizon 约束
                                                    - 20~50 步: 自由生成
```
