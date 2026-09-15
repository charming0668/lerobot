# ruff: noqa: N802
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lerobot.robots.piper_follower.piper_follower import PiperFollower
from lerobot.teleoperators.piper_leader.piper_leader import PiperLeader
from lerobot.utils.piper_sdk import (
    PIPER_JOINT_ACTION_KEYS,
    PIPER_JOINT_NAMES,
    go_zero_joints,
    req_master_arm_home,
    run_piper_homes_parallel,
    wait_piper_joints_near_zero,
)


class FakeArm:
    def __init__(self, joint_raw: int = 0):
        self.home_modes: list[int] = []
        self.joint_ctrl_calls: list[tuple[int, ...]] = []
        self.motion_ctrl_2_calls: list[tuple[int, ...]] = []
        self.enable_calls = 0
        self.joint_raw = joint_raw

    def ReqMasterArmMoveToHome(self, mode: int) -> None:
        self.home_modes.append(mode)

    def MotionCtrl_2(self, *args: int) -> None:
        self.motion_ctrl_2_calls.append(args)

    def JointCtrl(self, *args: int) -> None:
        self.joint_ctrl_calls.append(args)

    def EnablePiper(self) -> bool:
        self.enable_calls += 1
        return True

    def GetArmJointMsgs(self):
        return SimpleNamespace(
            joint_state=SimpleNamespace(**dict.fromkeys(PIPER_JOINT_NAMES, self.joint_raw))
        )

    def GetArmJointCtrl(self):
        return SimpleNamespace(
            time_stamp=1.0,
            joint_ctrl=SimpleNamespace(**dict.fromkeys(PIPER_JOINT_NAMES, self.joint_raw)),
        )

    def GetArmGripperCtrl(self):
        return SimpleNamespace(time_stamp=1.0, gripper_ctrl=SimpleNamespace(grippers_angle=0))


def _make_follower(joint_raw: int = 0, *, enable_on_connect: bool = True) -> PiperFollower:
    follower = PiperFollower.__new__(PiperFollower)
    follower._is_connected = True
    follower.config = SimpleNamespace(
        high_follow=True,
        speed_ratio=100,
        home_speed_ratio=30,
        enable_on_connect=enable_on_connect,
        enable_timeout_s=0.01,
        sync_gripper=False,
    )
    follower.arm = FakeArm(joint_raw=joint_raw)
    follower.cameras = {}
    return follower


def test_follower_go_home_repeats_joint_zeros_and_does_not_call_mode_2():
    follower = _make_follower(joint_raw=0)

    settled = PiperFollower.go_home(follower, settle_s=0.05, period_s=0.02)

    assert settled is True
    assert follower.arm.home_modes == []
    assert follower.arm.enable_calls >= 1
    assert follower.arm.joint_ctrl_calls
    assert all(call == (0, 0, 0, 0, 0, 0) for call in follower.arm.joint_ctrl_calls)
    assert follower.arm.motion_ctrl_2_calls
    assert all(call == (0x01, 0x01, 30, 0x00) for call in follower.arm.motion_ctrl_2_calls)


def test_follower_go_home_waits_full_settle_even_if_already_near_zero():
    follower = _make_follower(joint_raw=0)
    start = time.perf_counter()
    settled = PiperFollower.go_home(follower, settle_s=0.12, period_s=0.02)
    elapsed = time.perf_counter() - start

    assert settled is True
    assert elapsed >= 0.10
    assert len(follower.arm.joint_ctrl_calls) >= 3


def test_follower_go_home_fails_closed_when_joints_never_settle():
    follower = _make_follower(joint_raw=90_000)

    settled = PiperFollower.go_home(follower, settle_s=0.05, period_s=0.02)

    assert settled is False
    assert follower.arm.joint_ctrl_calls
    assert all(call == (0x01, 0x01, 30, 0x00) for call in follower.arm.motion_ctrl_2_calls)


def test_follower_send_action_refreshes_motion_ctrl_before_joint_ctrl():
    follower = _make_follower()
    action = {key: 0.0 for key in PIPER_JOINT_ACTION_KEYS}

    PiperFollower.send_action(follower, action)

    assert follower.arm.motion_ctrl_2_calls == [(0x01, 0x01, 100, 0xAD)]
    assert follower.arm.joint_ctrl_calls == [(0, 0, 0, 0, 0, 0)]


def test_wait_near_zero_can_keep_commanding_until_timeout():
    arm = FakeArm(joint_raw=0)
    ticks = []
    start = time.perf_counter()
    reached = wait_piper_joints_near_zero(
        arm,
        timeout_s=0.08,
        period_s=0.02,
        on_tick=lambda: ticks.append(1),
        stop_on_near_zero=False,
    )
    elapsed = time.perf_counter() - start

    assert reached is True
    assert elapsed >= 0.06
    assert len(ticks) >= 2


def test_go_zero_joints_sends_six_zeros():
    arm = FakeArm()
    go_zero_joints(arm)
    assert arm.joint_ctrl_calls == [(0, 0, 0, 0, 0, 0)]


def test_req_master_arm_home_rejects_mode_2():
    arm = FakeArm()
    with pytest.raises(ValueError, match="0 or 1"):
        req_master_arm_home(arm, 2)  # type: ignore[arg-type]
    assert arm.home_modes == []


def test_leader_go_home_uses_mode_1_then_0(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(
        "lerobot.teleoperators.piper_leader.piper_leader.time.sleep",
        lambda seconds: slept.append(seconds),
    )
    leader = PiperLeader.__new__(PiperLeader)
    leader._is_connected = True
    leader.arm = FakeArm(joint_raw=0)
    leader.arm.GetArmJointMsgs = MagicMock(side_effect=AssertionError("leader must not poll 0x2A5"))
    leader.set_manual_control = MagicMock()
    leader._manual_control_enabled = True
    leader._manual_action = {"joint_1.pos": 1.0, "gripper.pos": 0.2}
    leader.config = SimpleNamespace(sync_gripper=True)

    PiperLeader.go_home(leader, settle_s=6.0)

    assert leader.arm.home_modes == [1, 0]
    assert 2 not in leader.arm.home_modes
    assert slept == [6.0]
    leader.set_manual_control.assert_not_called()
    assert leader._manual_control_enabled is True
    assert leader._manual_action["joint_1.pos"] == 0.0
    assert leader._manual_action["gripper.pos"] == 0.2
    assert leader._last_control_joint_timestamp == 1.0


def test_run_piper_homes_parallel_runs_both():
    seen: list[str] = []
    run_piper_homes_parallel(lambda: seen.append("left"), lambda: seen.append("right"))
    assert sorted(seen) == ["left", "right"]
