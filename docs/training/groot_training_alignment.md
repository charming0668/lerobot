# GR00T N1.7 模型训练调研与决策对齐文档

本文档旨在梳理在 LeRobot 框架中使用数据集训练 GR00T N1.7（基模：`nvidia/GR00T-N1.7-3B`）的完整技术事实、架构机制与执行方案，并明确需要对齐决策的关键选项。

---

## 1. 背景与技术事实核验

### 1.1 模型版本与支持

- **代码库状态**：当前 LeRobot 仓库已彻底移除旧版 GR00T N1.5（仅在 `lerobot<=0.5.1` 保留），全面采用 **GR00T N1.7**。
- **架构组成**：
- 骨干网络（VLM Backbone）：`nvidia/Cosmos-Reason2-2B`（基于 Qwen3-VL 架构）。
- 动作生成头（Action Head）：基于 Flow-Matching 的 Cross-Attention DiT。
- **微调范围**（默认解耦配置）：
- 冻结语言与视觉底座：`tune_llm=False`，`tune_visual=False`。
- 微调跨模态投影与动作扩散模块：`tune_projector=True`，`tune_diffusion_model=True`，`tune_vlln=True`。
- **优化规范**：遵循 Isaac-GR00T 原厂规范，AdamW（`lr=1e-4, betas=(0.9, 0.999), weight_decay=1e-5`），结合 5% Warmup 的 Cosine 退火调度，采用 FP32 主参数 + BF16 混合精度计算。



### 1.2 本地数据集现状

本地目前存在两套可用数据集：


| 数据集标识               | 路径                                                                  | 类型          | 规模                  | FPS | 相机配置                                            | 状态/动作维度         |
| ------------------- | ------------------------------------------------------------------- | ----------- | ------------------- | --- | ----------------------------------------------- | --------------- |
| **实机双臂数据**          | `/data1/hmcai/lerobot/dataset/ming/piper-pick-cube_20260904_211137` | 真实 Piper 双臂 | 50 轨迹 / 21,868 帧    | 30  | `left_wrist`, `right_wrist`, `right_front`      | 14 维绝对角度（含左右夹爪） |
| **RoboTwin 统一仿真数据** | `/data1/hmcai/Dataset/RoboTwin-unified-v30`                         | 仿真平台统一轨迹    | 2500 轨迹 / 549,787 帧 | 15  | `cam_high`, `cam_left_wrist`, `cam_right_wrist` | 14 维绝对状态        |


---



## 2. 关键机制与避坑点

1. **本体标签 (Embodiment Tag)**：

- 自定义或非内置预设机械臂必须采用 `--policy.embodiment_tag=new_embodiment`。
- 该标签下状态与动作会自动扩展至最大 132 维补零空间，并基于数据集元数据动态建立 min/max（q01/q99 分位数）归一化参数。

1. **动作空间转换机制（相对动作 vs 绝对动作）**：

- 数据集原始文件保持绝对位置即可，无需离线转写。
- 若配置 `--policy.use_relative_actions=true`，预处理器会在数据输入时计算增量（$\Delta a = a - s$），后处理器在输出时加回当前状态。
- **夹爪必须排除**：夹爪（连续或开闭值）不能参与相对增量相减，需设置 `--policy.relative_exclude_joints='["gripper"]'`。

1. **相机键名对齐与视角语义固化**：

- 多视角相机在预处理阶段按固定顺序打包转换为 VLM 图像 Tokens。
- 从基模训练（`new_embodiment`）时，系统会自动将数据集中的全部相机键名按固定序列固化到导出的 Checkpoint 中。
- 训练后的模型在部署推理时，输入字典中的相机名称与物理视点必须与训练时完全一致，否则视点颠倒会导致推理行为崩溃。

1. **GPU 算力隔离**：

- 当前服务器 GPU 0（运行中）、GPU 4-7（运行中）处于占用状态。
- **空闲 GPU 为 GPU 1, 2, 3**。必须明确指定卡号（如 `CUDA_VISIBLE_DEVICES=1`）。

1. **进程防挂断与产物路径**：

- 长时间训练任务必须放置在 `tmux` 会话中运行。
- 产物与日志归档至 `/data1/hmcai/lerobot/outputs/train/<job_name>`。

---



## 3. 待决策与对齐事项（Checklist）

请根据实验目的与规划确认以下选项（采用 Markdown 任务复选框  确认选项，配合  记录自定义要求与备注）：

### 问题 1：目标训练数据集采用哪一个？

- [x] **选项 A（快速闭环验证）**：先使用实机双臂数据 。
  - *依据*：体量小（50 episodes），可快速验证从数据加载、GR00T N1.7 初始化、前向反向传播、权重保存到推理导出的完整链路，显存与用时可控。
- [ ] **选项 B（规模化训练）**：直接使用大规模仿真数据 。
  - *依据*：2500 episodes，数据量充沛，但训练耗时较长（通常需要数小时至数天），且相机名与实机不同。
- [ ] **选项 C**：其他指定子集。

补充：

### 问题 2：动作空间是否启用相对动作（Relative Action）？

- [x] **选项 A**：启用相对动作（）。
  - *依据*：符合 Isaac-GR00T 官方推荐最佳实践，对初始姿态漂移更鲁棒，生成的动作更加平滑。
- [ ] **选项 B**：保持绝对动作（）。
  - *依据*：逻辑更直接，但机械臂动作平滑度及跨位置泛化能力较差。

补充：

### 问题 3：动作块长度（Chunk Size）与 Horizon 配置？

- [x] **选项 A**：。
  - *依据*：适合高频闭环反应，显存开销适中，推理延迟更低。
- [ ] **选项 B**：。
  - *依据*：GR00T 原生预设长度，时域规划范围更大，但显存开销更大。

补充：

### 问题 4：单卡还是多卡并行训练？

- [ ] **选项 A**：单卡训练（指定单个 GPU）。
- [x] **选项 B**：多卡 DDP 训练。

补充： 选用前4卡训练（GPU 0, 1, 2, 3，当前 GPU 0-3 均为空闲）

### 问题 5：实验日志跟踪方式？

- [ ] **选项 A**：本地日志（，控制台 + 本地指标日志文件）。
- [x] **选项 B**：开启 Weights & Biases 在线记录（）。

补充：

---

## 4. 推荐执行命令示例

根据上述已对齐的决策项（实机双臂数据 + 相对动作排除夹爪 + Chunk Size 16 + 前4卡 DDP + WandB 开启），生成对应的多卡启动命令：



```bash
# 1. 建立并进入 tmux 会话（防止进程挂断）
tmux new -s train_groot
conda activate lerobot
cd /data1/hmcai/lerobot

# 2. 使用 torchrun 启动前4卡 DDP 并行训练
CUDA_VISIBLE_DEVICES=0,1,2,3 torchrun --nproc_per_node=4    --dataset.repo_id="ming/piper-pick-cube_20260904_211137"   --dataset.root="/data1/hmcai/lerobot/dataset/ming/piper-pick-cube_20260904_211137"   --policy.type=groot   --policy.base_model_path="nvidia/GR00T-N1.7-3B"   --policy.embodiment_tag=new_embodiment   --policy.chunk_size=16   --policy.n_action_steps=16   --policy.use_relative_actions=true   --policy.relative_exclude_joints='["gripper"]'   --policy.use_bf16=true   --policy.push_to_hub=false   --batch_size=16   --num_workers=4   --steps=20000   --save_checkpoint=true   --save_freq=5000   --log_freq=20   --output_dir="/data1/hmcai/lerobot/outputs/train/groot_piper_pick_cube"   --job_name="groot_piper_pick_cube_4gpu"   --wandb.enable=true
```
