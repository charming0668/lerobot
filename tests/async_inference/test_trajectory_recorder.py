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

from __future__ import annotations

import csv

import pytest

from lerobot.async_inference.trajectory_recorder import TrajectoryRecorder


def test_finish_run_writes_csv_and_png(tmp_path):
    pytest.importorskip("matplotlib")
    rec = TrajectoryRecorder(tmp_path, enabled=True)
    rec.start_run()
    rec.append(
        {"left_joint_1.pos": 0.1, "right_joint_1.pos": -0.2},
        {"left_joint_1.pos": 0.11, "right_joint_1.pos": -0.19},
    )
    rec.append(
        {"left_joint_1.pos": 0.2, "right_joint_1.pos": -0.1},
        {"left_joint_1.pos": 0.21, "right_joint_1.pos": -0.09},
    )
    saved = rec.finish_run()
    assert saved is not None
    csv_path = saved.with_suffix(".csv")
    png_path = saved.with_suffix(".png")
    assert csv_path.is_file()
    assert png_path.is_file()
    with csv_path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert "cmd.left_joint_1.pos" in rows[0]
    assert "obs.right_joint_1.pos" in rows[0]
    assert rec.active is False
    assert rec.n_steps == 0


def test_discard_run_writes_nothing(tmp_path):
    rec = TrajectoryRecorder(tmp_path, enabled=True)
    rec.start_run()
    rec.append({"joint_1.pos": 1.0})
    rec.discard_run()
    assert rec.active is False
    assert list(tmp_path.rglob("*.csv")) == []
    assert list(tmp_path.rglob("*.png")) == []


def test_empty_finish_writes_nothing(tmp_path):
    rec = TrajectoryRecorder(tmp_path, enabled=True)
    rec.start_run()
    assert rec.finish_run() is None
    assert list(tmp_path.rglob("*")) == []


def test_append_ignored_when_inactive(tmp_path):
    rec = TrajectoryRecorder(tmp_path, enabled=True)
    rec.append({"joint_1.pos": 1.0})
    rec.start_run()
    rec.append({})
    rec.append(None)
    rec.finish_run()
    assert list(tmp_path.rglob("*.csv")) == []


def test_second_run_increments_index(tmp_path):
    rec = TrajectoryRecorder(tmp_path, enabled=True)
    rec.start_run()
    rec.append({"joint_1.pos": 1.0})
    rec.finish_run()
    rec.start_run()
    rec.append({"joint_1.pos": 2.0})
    rec.finish_run()
    csvs = sorted(tmp_path.rglob("run_*.csv"))
    assert [p.name for p in csvs] == ["run_001.csv", "run_002.csv"]


def test_disabled_recorder_is_noop(tmp_path):
    rec = TrajectoryRecorder(tmp_path, enabled=False)
    rec.start_run()
    rec.append({"joint_1.pos": 1.0})
    assert rec.finish_run() is None
    rec.discard_run()
    assert list(tmp_path.rglob("*")) == []
