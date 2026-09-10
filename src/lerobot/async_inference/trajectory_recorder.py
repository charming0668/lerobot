# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Record one executed trajectory between two Enter presses (run → pause)."""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return float(item())
        except (TypeError, ValueError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_dict(data: dict[str, Any] | None) -> dict[str, float]:
    if not data:
        return {}
    out: dict[str, float] = {}
    for key, value in data.items():
        parsed = _as_float(value)
        if parsed is not None:
            out[str(key)] = parsed
    return out


def _sort_keys(keys: set[str]) -> list[str]:
    def rank(name: str) -> tuple[int, str]:
        if name.startswith("left_"):
            return (0, name)
        if name.startswith("right_"):
            return (1, name)
        return (2, name)

    return sorted(keys, key=rank)


@dataclass
class TrajectoryStep:
    t_s: float
    step: int
    cmd: dict[str, float] = field(default_factory=dict)
    obs: dict[str, float] = field(default_factory=dict)


class TrajectoryRecorder:
    """Buffer executed ticks while a run is active; save CSV+PNG on finish.

    ``start_run`` / ``finish_run`` map to the first and second Enter.
    ``discard_run`` drops an unfinished buffer (Space or q before the second Enter).
    """

    def __init__(self, output_dir: str | Path, enabled: bool = True) -> None:
        self.output_dir = Path(output_dir)
        self.enabled = enabled
        self._active = False
        self._steps: list[TrajectoryStep] = []
        self._t0 = 0.0
        self._run_idx = 0
        self._session_dir: Path | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def n_steps(self) -> int:
        return len(self._steps)

    def start_run(self) -> None:
        if not self.enabled:
            return
        self._steps = []
        self._active = True
        self._t0 = time.perf_counter()
        logger.info("Recording trajectory until the next Enter")

    def append(self, cmd: dict[str, Any] | None, obs: dict[str, Any] | None = None) -> None:
        if not self.enabled or not self._active:
            return
        cmd_num = _numeric_dict(cmd)
        if not cmd_num:
            return
        self._steps.append(
            TrajectoryStep(
                t_s=time.perf_counter() - self._t0,
                step=len(self._steps),
                cmd=cmd_num,
                obs=_numeric_dict(obs),
            )
        )

    def finish_run(self) -> Path | None:
        """Save the Enter→Enter buffer. Empty or inactive runs write nothing."""
        if not self.enabled or not self._active:
            self._active = False
            self._steps = []
            return None
        self._active = False
        steps = self._steps
        self._steps = []
        if not steps:
            logger.info("No executed actions between Enter presses; nothing to save")
            return None
        session = self._ensure_session_dir()
        self._run_idx += 1
        stem = f"run_{self._run_idx:03d}"
        csv_path = session / f"{stem}.csv"
        png_path = session / f"{stem}.png"
        self._write_csv(csv_path, steps)
        try:
            self._write_plot(png_path, steps)
        except Exception:
            logger.exception("Saved CSV but failed to plot %s", png_path)
            png_path = None
        logger.info("Saved trajectory %s (%s steps) → %s", stem, len(steps), csv_path)
        return png_path or csv_path

    def discard_run(self) -> None:
        """Drop an unfinished run (Space/q before the closing Enter)."""
        had_steps = bool(self._steps)
        was_active = self._active
        self._active = False
        self._steps = []
        if self.enabled and was_active:
            if had_steps:
                logger.info("Trajectory discarded (home/stop before second Enter)")
            else:
                logger.info("Trajectory recording cancelled")

    def _ensure_session_dir(self) -> Path:
        if self._session_dir is None:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._session_dir = self.output_dir / stamp
            self._session_dir.mkdir(parents=True, exist_ok=True)
        return self._session_dir

    @staticmethod
    def _write_csv(path: Path, steps: list[TrajectoryStep]) -> None:
        keys: set[str] = set()
        for step in steps:
            keys.update(step.cmd)
            keys.update(step.obs)
        ordered = _sort_keys(keys)
        fieldnames = ["t_s", "step", *[f"cmd.{k}" for k in ordered], *[f"obs.{k}" for k in ordered]]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for step in steps:
                row: dict[str, Any] = {"t_s": f"{step.t_s:.6f}", "step": step.step}
                for key in ordered:
                    if key in step.cmd:
                        row[f"cmd.{key}"] = step.cmd[key]
                    if key in step.obs:
                        row[f"obs.{key}"] = step.obs[key]
                writer.writerow(row)

    @staticmethod
    def _write_plot(path: Path, steps: list[TrajectoryStep]) -> None:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        keys: set[str] = set()
        for step in steps:
            keys.update(step.cmd)
            keys.update(step.obs)
        ordered = _sort_keys(keys)
        if not ordered:
            return
        times = [step.t_s for step in steps]
        left = [k for k in ordered if k.startswith("left_")]
        right = [k for k in ordered if k.startswith("right_")]
        other = [k for k in ordered if k not in left and k not in right]

        if left and right:
            groups = [left, right]
            col_titles = ["left", "right"]
        else:
            groups = [ordered]
            col_titles = ["joints"]

        nrows = max(len(group) for group in groups)
        ncols = len(groups)
        fig, axes = plt.subplots(
            nrows,
            ncols,
            figsize=(6.5 * ncols, max(2.2, 1.35 * nrows)),
            sharex=True,
            squeeze=False,
        )
        for col, (group, title) in enumerate(zip(groups, col_titles, strict=True)):
            axes[0, col].set_title(title)
            for row in range(nrows):
                ax = axes[row, col]
                if row >= len(group):
                    ax.axis("off")
                    continue
                key = group[row]
                cmd_y = [step.cmd.get(key) for step in steps]
                obs_y = [step.obs.get(key) for step in steps]
                if any(v is not None for v in cmd_y):
                    ax.plot(times, cmd_y, color="C0", lw=1.4, label="cmd")
                if any(v is not None for v in obs_y):
                    ax.plot(times, obs_y, color="C1", lw=1.2, ls="--", label="obs")
                ax.set_ylabel(key, fontsize=8)
                ax.grid(True, alpha=0.3)
                if row == 0:
                    ax.legend(loc="upper right", fontsize=8)
        for col in range(ncols):
            axes[-1, col].set_xlabel("time (s)")
        if other and left and right:
            fig.suptitle("executed trajectory (Enter → Enter)", fontsize=11)
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
