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

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

PIPER_JOINT_NAMES = (
    "joint_1",
    "joint_2",
    "joint_3",
    "joint_4",
    "joint_5",
    "joint_6",
)
PIPER_JOINT_ACTION_KEYS = tuple(f"{joint}.pos" for joint in PIPER_JOINT_NAMES)
PIPER_ACTION_KEYS = PIPER_JOINT_ACTION_KEYS + ("gripper.pos",)
PIPER_ROLE_LEADER = 0xFA
PIPER_ROLE_FOLLOWER = 0xFC
PIPER_HOME_JOINT_TOLERANCE_DEG = 2.0
_SYS_CLASS_NET = Path("/sys/class/net")


def resolve_piper_can_interface(serial_number: str) -> str:
    """Return the SocketCAN interface for a USB-CAN adapter serial number."""
    for interface_path in _SYS_CLASS_NET.iterdir():
        try:
            if (interface_path / "type").read_text().strip() != "280":
                continue
        except OSError:
            continue

        try:
            result = subprocess.run(  # nosec B607
                ["udevadm", "info", "--query=property", f"--path={interface_path}"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("`udevadm` is required to find the PiPER USB-CAN adapter.") from exc

        if f"ID_SERIAL_SHORT={serial_number}" in result.stdout.splitlines():
            return interface_path.name

    raise RuntimeError(f"No PiPER USB-CAN adapter found with serial '{serial_number}'.")


def milli_to_unit(value: float | int) -> float:
    return float(value) * 1e-3


def unit_to_milli(value: float | int) -> int:
    return int(round(float(value) * 1e3))


@lru_cache(maxsize=1)
def get_piper_sdk() -> tuple[type[Any], Any]:
    try:
        from piper_sdk import C_PiperInterface_V2, LogLevel

        return C_PiperInterface_V2, LogLevel
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Could not import `piper_sdk`. Install Evo-RL dependencies first (for example: `pip install -e .`)."
        ) from exc


def parse_piper_log_level(level_name: str) -> Any:
    _, log_level_enum = get_piper_sdk()
    normalized = level_name.upper()
    try:
        return getattr(log_level_enum, normalized)
    except AttributeError as exc:
        raise ValueError(
            f"Invalid Piper log level '{level_name}'. "
            "Expected one of: DEBUG, INFO, WARNING, ERROR, CRITICAL, SILENT."
        ) from exc


def wait_enable_piper(arm: Any, timeout_s: float, retry_interval_s: float = 0.2) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_s)
    interval_s = max(0.01, retry_interval_s)
    while time.monotonic() < deadline:
        if bool(arm.EnablePiper()):
            return True
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            break
        time.sleep(min(interval_s, remaining_s))
    return False


def set_piper_role(arm: Any, role: int) -> None:
    arm.MasterSlaveConfig(role, 0x00, 0x00, 0x00)


def req_master_arm_home(arm: Any, mode: Literal[0, 1]) -> None:
    """Ask one arm on this CAN socket to home (mode=1) or restore teach mode (mode=0).

    ``mode=2`` (hardware master+slave on one bus) is intentionally rejected: this
    stack gives each Piper its own CAN, so the follower never sees 0x191 from the leader.
    """
    if mode not in (0, 1):
        raise ValueError(
            f"ReqMasterArmMoveToHome mode must be 0 or 1 for per-arm CAN wiring, got {mode}."
        )
    arm.ReqMasterArmMoveToHome(mode)


def go_zero_joints(arm: Any) -> None:
    """Command all six joints to 0 on a follower (already in position-control) arm."""
    arm.JointCtrl(0, 0, 0, 0, 0, 0)


def read_piper_joint_positions_deg(arm: Any) -> list[float] | None:
    try:
        joint_msg = arm.GetArmJointMsgs()
    except Exception:
        return None
    joint_state = getattr(joint_msg, "joint_state", None)
    if joint_state is None:
        return None
    positions: list[float] = []
    for joint_name in PIPER_JOINT_NAMES:
        raw_value = getattr(joint_state, joint_name, None)
        if raw_value is None:
            return None
        positions.append(milli_to_unit(raw_value))
    return positions


def piper_joints_near_zero(arm: Any, tolerance_deg: float = PIPER_HOME_JOINT_TOLERANCE_DEG) -> bool:
    positions = read_piper_joint_positions_deg(arm)
    if positions is None:
        return False
    return all(abs(position) <= tolerance_deg for position in positions)


def wait_piper_joints_near_zero(
    arm: Any,
    timeout_s: float,
    *,
    period_s: float = 0.05,
    tolerance_deg: float = PIPER_HOME_JOINT_TOLERANCE_DEG,
    on_tick: Callable[[], None] | None = None,
) -> bool:
    """Poll until joints are near zero, optionally commanding on each tick.

    Returns True if the arm reached the tolerance before ``timeout_s``.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    interval_s = max(0.01, period_s)
    while True:
        if on_tick is not None:
            on_tick()
        if piper_joints_near_zero(arm, tolerance_deg=tolerance_deg):
            return True
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            return False
        time.sleep(min(interval_s, remaining_s))


def run_piper_homes_parallel(*home_fns: Callable[[], None]) -> None:
    """Run per-arm ``go_home`` callables concurrently (one CAN each)."""
    if not home_fns:
        return
    if len(home_fns) == 1:
        home_fns[0]()
        return
    with ThreadPoolExecutor(max_workers=len(home_fns)) as executor:
        futures = [executor.submit(fn) for fn in home_fns]
        for future in futures:
            future.result()
