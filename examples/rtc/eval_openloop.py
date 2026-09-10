#!/usr/bin/env python
"""Open-loop dataset fit: predict action chunks from dataset observations and compare to ground truth.

Does not use RTC leftover. Each sample is an independent teacher-forced chunk:
the policy sees (images, state, task) at time t and predicts H future actions,
which are compared to the dataset actions [t, t+H).
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

try:
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    plt = None

from lerobot.configs import DatasetConfig, PreTrainedConfig, parser
from lerobot.datasets import LeRobotDataset, LeRobotDatasetMetadata, resolve_delta_timestamps
from lerobot.policies import get_policy_class, make_pre_post_processors
from lerobot.utils.constants import ACTION
from lerobot.utils.hub import HubMixin
from lerobot.utils.utils import init_logging


def _scalar(value) -> int:
    if isinstance(value, (list, tuple)):
        value = value[0]
    if hasattr(value, "item"):
        return int(value.item())
    return int(value)


def _require_matplotlib():
    if not MATPLOTLIB_AVAILABLE:
        raise ImportError("matplotlib is required. Install with: pip install matplotlib")


@dataclass
class OpenLoopEvalConfig(HubMixin):
    policy: PreTrainedConfig | None = None
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    device: str | None = field(default=None, metadata={"help": "cuda, cpu, mps, or auto"})
    output_dir: str = field(default="outputs/openloop", metadata={"help": "Directory for plots and metrics"})
    seed: int = 10
    num_samples: int = field(default=8, metadata={"help": "Number of frames, spread across episodes"})

    def __post_init__(self):
        policy_path = parser.get_path_arg("policy")
        if not policy_path:
            raise ValueError("Policy path is required (--policy.path)")
        cli_overrides = parser.get_cli_overrides("policy")
        self.policy = PreTrainedConfig.from_pretrained(policy_path, cli_overrides=cli_overrides)
        self.policy.pretrained_path = policy_path

        if self.device is None or self.device == "auto":
            if torch.cuda.is_available():
                self.device = "cuda"
            elif torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"

    @classmethod
    def __get_path_fields__(cls) -> list[str]:
        return ["policy"]


def _postprocess_chunk(postprocessor, action_chunk: torch.Tensor) -> torch.Tensor:
    """Unnormalize (B, H, A) model-space actions to robot space."""
    processed = []
    for i in range(action_chunk.shape[1]):
        processed.append(postprocessor(action_chunk[:, i, :]))
    return torch.stack(processed, dim=1)


def _select_frame_indices(dataset: LeRobotDataset, chunk_size: int, num_samples: int, seed: int) -> list[int]:
    """Pick one valid frame per selected episode, with a full H-step action horizon."""
    rng = np.random.default_rng(seed)
    n_ep = dataset.num_episodes
    episode_ids = np.linspace(0, n_ep - 1, num=min(num_samples, n_ep), dtype=int)
    episode_ids = np.unique(episode_ids)
    if len(episode_ids) < num_samples:
        extra = rng.choice(n_ep, size=num_samples - len(episode_ids), replace=True)
        episode_ids = np.concatenate([episode_ids, extra])

    indices: list[int] = []
    for ep_idx in episode_ids[:num_samples]:
        ep = dataset.meta.episodes[int(ep_idx)]
        start = _scalar(ep["dataset_from_index"])
        end = _scalar(ep["dataset_to_index"])
        length = end - start
        if length <= chunk_size:
            indices.append(start)
            continue
        # Mid-episode so both past (if any) and H-step future are inside the episode.
        offset = max(0, (length - chunk_size) // 3)
        indices.append(start + int(offset))
    return indices


def _dim_labels(policy_cfg) -> list[str]:
    names = getattr(policy_cfg, "action_feature_names", None)
    if names:
        return list(names)
    action_dim = policy_cfg.output_features[ACTION].shape[0]
    return [f"dim_{i}" for i in range(action_dim)]


def _plot_overlay(preds, gts, masks, labels, names, output_path: Path):
    _require_matplotlib()
    n_show = min(4, len(preds))
    action_dim = preds[0].shape[-1]
    fig, axes = plt.subplots(action_dim, n_show, figsize=(4.2 * n_show, 1.35 * action_dim), sharex=True)
    if action_dim == 1:
        axes = np.array([[axes]])
    elif n_show == 1:
        axes = axes[:, None]

    for col in range(n_show):
        pred = preds[col]
        gt = gts[col]
        mask = masks[col]
        valid = int((~mask).sum())
        steps = np.arange(valid)
        for dim in range(action_dim):
            ax = axes[dim, col]
            ax.plot(steps, gt[:valid, dim], color="black", lw=1.6, label="Dataset GT")
            ax.plot(steps, pred[:valid, dim], color="C0", lw=1.4, ls="--", label="Open-loop pred")
            ax.grid(True, alpha=0.3)
            if col == 0:
                ax.set_ylabel(names[dim], fontsize=8)
            if dim == 0:
                ax.set_title(labels[col], fontsize=10)
            if dim == action_dim - 1:
                ax.set_xlabel("chunk step")

    handles, legend_labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper right", fontsize=9)
    fig.suptitle("Open-loop action chunk vs dataset ground truth", fontsize=13)
    fig.tight_layout(rect=[0, 0, 0.98, 0.97])
    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _plot_mae_bars(mae_per_dim: np.ndarray, names: list[str], output_path: Path):
    _require_matplotlib()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    x = np.arange(len(names))
    ax.bar(x, mae_per_dim, color="C0", alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("MAE (robot units)")
    ax.set_title("Open-loop mean absolute error per action dimension")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _plot_horizon(mae_vs_h: np.ndarray, output_path: Path):
    _require_matplotlib()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(np.arange(len(mae_vs_h)), mae_vs_h, color="C0", lw=2)
    ax.set_xlabel("chunk step")
    ax.set_ylabel("MAE (mean over dims and samples)")
    ax.set_title("Open-loop error vs prediction horizon")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


@parser.wrap()
def main(cfg: OpenLoopEvalConfig):
    init_logging()
    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)
    os.makedirs(cfg.output_dir, exist_ok=True)

    ds_meta = LeRobotDatasetMetadata(cfg.dataset.repo_id, root=cfg.dataset.root)
    delta_timestamps = resolve_delta_timestamps(cfg.policy, ds_meta)
    dataset = LeRobotDataset(cfg.dataset.repo_id, root=cfg.dataset.root, delta_timestamps=delta_timestamps)
    logging.info("Dataset %s | %s frames | %s episodes", cfg.dataset.repo_id, len(dataset), dataset.num_episodes)

    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=cfg.policy,
        pretrained_path=cfg.policy.pretrained_path,
        preprocessor_overrides={"device_processor": {"device": cfg.device}},
    )

    policy_class = get_policy_class(cfg.policy.type)
    policy_cfg = PreTrainedConfig.from_pretrained(cfg.policy.pretrained_path)
    policy = policy_class.from_pretrained(cfg.policy.pretrained_path, config=policy_cfg)
    policy = policy.to(cfg.device)
    policy.eval()

    chunk_size = policy.config.chunk_size
    frame_indices = _select_frame_indices(dataset, chunk_size, cfg.num_samples, cfg.seed)
    logging.info("Evaluating %s frames: %s", len(frame_indices), frame_indices)

    preds, gts, masks, labels = [], [], [], []
    with torch.no_grad():
        for abs_idx in frame_indices:
            sample = dataset[int(abs_idx)]
            gt = sample[ACTION]
            if gt.ndim == 1:
                raise ValueError("Expected temporally stacked dataset actions. Check delta_timestamps.")
            pad_key = f"{ACTION}_is_pad"
            pad = sample[pad_key] if pad_key in sample else torch.zeros(gt.shape[0], dtype=torch.bool)

            batch = {k: (v.unsqueeze(0) if isinstance(v, torch.Tensor) else [v]) for k, v in sample.items()}
            obs = preprocessor(batch)
            pred_model = policy.predict_action_chunk(obs)
            pred = _postprocess_chunk(postprocessor, pred_model).squeeze(0).cpu().float()
            gt = gt.cpu().float()
            pad = pad.cpu().bool()
            horizon = min(pred.shape[0], gt.shape[0])
            pred, gt, pad = pred[:horizon], gt[:horizon], pad[:horizon]

            ep_idx = int(sample["episode_index"])
            frame_idx = int(sample["frame_index"])
            labels.append(f"ep{ep_idx} t={frame_idx}")
            preds.append(pred.numpy())
            gts.append(gt.numpy())
            masks.append(pad.numpy())
            valid_l1 = np.abs(pred.numpy()[~pad.numpy()] - gt.numpy()[~pad.numpy()]).mean()
            logging.info("  %s | valid steps=%s | MAE=%.4f", labels[-1], int((~pad).sum()), valid_l1)

    abs_err = []
    for pred, gt, mask in zip(preds, gts, masks, strict=True):
        err = np.abs(pred - gt)
        err[mask] = np.nan
        abs_err.append(err)
    stacked = np.stack(abs_err, axis=0)
    mae_per_dim = np.nanmean(stacked, axis=(0, 1))
    mae_vs_h = np.nanmean(stacked, axis=(0, 2))
    overall = float(np.nanmean(stacked))

    names = _dim_labels(policy.config)[: stacked.shape[-1]]
    metrics = {
        "overall_mae": overall,
        "mae_per_dim": {name: float(v) for name, v in zip(names, mae_per_dim, strict=True)},
        "mae_vs_horizon": mae_vs_h.tolist(),
        "frame_indices": frame_indices,
        "labels": labels,
        "chunk_size": chunk_size,
        "num_samples": len(preds),
    }
    metrics_path = Path(cfg.output_dir) / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    logging.info("Overall MAE: %.4f", overall)

    overlay_path = Path(cfg.output_dir) / "openloop_overlay.png"
    mae_path = Path(cfg.output_dir) / "openloop_mae_per_dim.png"
    horizon_path = Path(cfg.output_dir) / "openloop_error_vs_horizon.png"
    _plot_overlay(preds, gts, masks, labels, names, overlay_path)
    _plot_mae_bars(mae_per_dim, names, mae_path)
    _plot_horizon(mae_vs_h, horizon_path)
    logging.info("Wrote %s", overlay_path)
    logging.info("Wrote %s", mae_path)
    logging.info("Wrote %s", horizon_path)


if __name__ == "__main__":
    main()
