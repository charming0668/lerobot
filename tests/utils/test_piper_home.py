#!/usr/bin/env python

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
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

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lerobot.robots.piper_follower.piper_follower import PiperFollower
from lerobot.teleoperators.piper_leader.piper_leader import PiperLeader
from lerobot.utils.piper_sdk import (
    PIPER_JOINT_NAMES,
    go_zero_joints,
    req_master_arm_home,
    run_piper_homes_parallel,
)


class FakeArm:
    def __init__(self, joint_raw: int = 0):
        self.home_modes: list[int] = []
        self.joint_ctrl_calls: list[tuple[int, ...]] = []
        self.joint_raw = joint_raw

    def ReqMasterArmMoveToHome(self, mode: int) -> None:
        self.home_modes.append(mode)

    def JointCtrl(self, *args: int) -> None:
        self.joint_ctrl_calls.append(args)

    def GetArmJointMsgs(self):
        return SimpleNamespace(
            joint_state=SimpleNamespace(**dict.fromkeys(PIPER_JOINT_NAMES, self.joint_raw))
        )


def test_req_master_arm_home_rejects_mode_2():
    arm = FakeArm()
    with pytest.raises(ValueError, match="0 or 1"):
        req_master_arm_home(arm, 2)  # type: ignore[arg-type]
    assert arm.home_modes == []


def test_leader_go_home_uses_mode_1_then_0():
    leader = PiperLeader.__new__(PiperLeader)
    leader._is_connected = True
    leader.arm = FakeArm(joint_raw=0)
    leader.set_manual_control = MagicMock()
    leader._manual_control_enabled = True

    PiperLeader.go_home(leader, settle_s=0.05)

    assert leader.arm.home_modes == [1, 0]
    assert 2 not in leader.arm.home_modes
    leader.set_manual_control.assert_called_once_with(True)


def test_follower_go_home_repeats_joint_zeros_and_does_not_call_mode_2():
    follower = PiperFollower.__new__(PiperFollower)
    follower._is_connected = True
    follower.arm = FakeArm(joint_raw=0)
    follower.cameras = {}

    PiperFollower.go_home(follower, settle_s=0.05, period_s=0.01)

    assert follower.arm.home_modes == []
    assert follower.arm.joint_ctrl_calls
    assert all(call == (0, 0, 0, 0, 0, 0) for call in follower.arm.joint_ctrl_calls)


def test_go_zero_joints_sends_six_zeros():
    arm = FakeArm()
    go_zero_joints(arm)
    assert arm.joint_ctrl_calls == [(0, 0, 0, 0, 0, 0)]


def test_run_piper_homes_parallel_runs_both():
    seen: list[str] = []
    run_piper_homes_parallel(lambda: seen.append("left"), lambda: seen.append("right"))
    assert sorted(seen) == ["left", "right"]
