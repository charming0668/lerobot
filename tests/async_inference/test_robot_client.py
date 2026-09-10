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
"""Unit-tests for the `RobotClient` action-queue logic (pure Python, no gRPC).

We monkey-patch `lerobot.robots.utils.make_robot_from_config` so that
no real hardware is accessed. Only the queue-update mechanism is verified.
"""

from __future__ import annotations

import csv
import time
from queue import Queue

import pytest
import torch

# Skip entire module if required deps are not available
pytest.importorskip("grpc")
pytest.importorskip("serial", reason="pyserial is required (install lerobot[hardware])")
pytest.importorskip("datasets", reason="datasets is required (install lerobot[dataset])")

# -----------------------------------------------------------------------------
# Test fixtures
# -----------------------------------------------------------------------------


@pytest.fixture()
def robot_client():
    """Fresh `RobotClient` instance for each test case (no threads started).
    Uses DummyRobot."""
    # Import only when the test actually runs (after decorator check)
    from lerobot.async_inference.configs import RobotClientConfig
    from lerobot.async_inference.robot_client import RobotClient
    from tests.mocks.mock_robot import MockRobotConfig

    test_config = MockRobotConfig()

    # gRPC channel is not actually used in tests, so using a dummy address
    test_config = RobotClientConfig(
        robot=test_config,
        server_address="localhost:9999",
        policy_type="test",
        pretrained_name_or_path="test",
        actions_per_chunk=20,
    )

    client = RobotClient(test_config)

    # Initialize attributes that are normally set in start() method
    client.chunks_received = 0
    client.available_actions_size = []

    yield client

    if client.robot.is_connected:
        client.stop()


# -----------------------------------------------------------------------------
# Helper utilities for tests
# -----------------------------------------------------------------------------


def _make_actions(start_ts: float, start_t: int, count: int):
    """Generate `count` consecutive TimedAction objects starting at timestep `start_t`."""
    from lerobot.async_inference.helpers import TimedAction

    fps = 30  # emulates most common frame-rate
    actions = []
    for i in range(count):
        timestep = start_t + i
        timestamp = start_ts + i * (1 / fps)
        action_tensor = torch.full((6,), timestep, dtype=torch.float32)
        actions.append(TimedAction(action=action_tensor, timestep=timestep, timestamp=timestamp))
    return actions


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_update_action_queue_discards_stale(robot_client):
    """`_update_action_queue` must drop actions with `timestep` <= `latest_action`."""

    # Pretend we already executed up to action #4
    robot_client.latest_action = 4

    # Incoming chunk contains timesteps 3..7 -> expect 5,6,7 kept.
    incoming = _make_actions(start_ts=time.time(), start_t=3, count=5)  # 3,4,5,6,7

    robot_client._aggregate_action_queues(incoming)

    # Extract timesteps from queue
    resulting_timesteps = [a.get_timestep() for a in robot_client.action_queue.queue]

    assert resulting_timesteps == [5, 6, 7]


@pytest.mark.parametrize(
    "weight_old, weight_new",
    [
        (1.0, 0.0),
        (0.0, 1.0),
        (0.5, 0.5),
        (0.2, 0.8),
        (0.8, 0.2),
        (0.1, 0.9),
        (0.9, 0.1),
    ],
)
def test_aggregate_action_queues_combines_actions_in_overlap(
    robot_client, weight_old: float, weight_new: float
):
    """`_aggregate_action_queues` must combine actions on overlapping timesteps according
    to the provided aggregate_fn, here tested with multiple coefficients."""
    from lerobot.async_inference.helpers import TimedAction

    robot_client.chunks_received = 0

    # Pretend we already executed up to action #4, and queue contains actions for timesteps 5..6
    robot_client.latest_action = 4
    current_actions = _make_actions(
        start_ts=time.time(), start_t=5, count=2
    )  # actions are [torch.ones(6), torch.ones(6), ...]
    current_actions = [
        TimedAction(action=10 * a.get_action(), timestep=a.get_timestep(), timestamp=a.get_timestamp())
        for a in current_actions
    ]

    for a in current_actions:
        robot_client.action_queue.put(a)

    # Incoming chunk contains timesteps 3..7 -> expect 5,6,7 kept.
    incoming = _make_actions(start_ts=time.time(), start_t=3, count=5)  # 3,4,5,6,7

    overlap_timesteps = [5, 6]  # properly tested in test_aggregate_action_queues_discards_stale
    nonoverlap_timesteps = [7]

    robot_client._aggregate_action_queues(
        incoming, aggregate_fn=lambda x1, x2: weight_old * x1 + weight_new * x2
    )

    queue_overlap_actions = []
    queue_non_overlap_actions = []
    for a in robot_client.action_queue.queue:
        if a.get_timestep() in overlap_timesteps:
            queue_overlap_actions.append(a)
        elif a.get_timestep() in nonoverlap_timesteps:
            queue_non_overlap_actions.append(a)

    queue_overlap_actions = sorted(queue_overlap_actions, key=lambda x: x.get_timestep())
    queue_non_overlap_actions = sorted(queue_non_overlap_actions, key=lambda x: x.get_timestep())

    assert torch.allclose(
        queue_overlap_actions[0].get_action(),
        weight_old * current_actions[0].get_action() + weight_new * incoming[-3].get_action(),
    )
    assert torch.allclose(
        queue_overlap_actions[1].get_action(),
        weight_old * current_actions[1].get_action() + weight_new * incoming[-2].get_action(),
    )
    assert torch.allclose(queue_non_overlap_actions[0].get_action(), incoming[-1].get_action())


@pytest.mark.parametrize(
    "chunk_size, queue_len, expected",
    [
        (20, 12, False),  # 12 / 20 = 0.6  > g=0.5 threshold, not ready to send
        (20, 8, True),  # 8  / 20 = 0.4 <= g=0.5, ready to send
        (10, 5, True),
        (10, 6, False),
    ],
)
def test_ready_to_send_observation(robot_client, chunk_size: int, queue_len: int, expected: bool):
    """Validate `_ready_to_send_observation` ratio logic for various sizes."""

    robot_client.action_chunk_size = chunk_size

    # Clear any existing actions then fill with `queue_len` dummy entries ----
    robot_client.action_queue = Queue()

    dummy_actions = _make_actions(start_ts=time.time(), start_t=0, count=queue_len)
    for act in dummy_actions:
        robot_client.action_queue.put(act)

    assert robot_client._ready_to_send_observation() is expected


@pytest.mark.parametrize(
    "g_threshold, expected",
    [
        # The condition is `queue_size / chunk_size <= g`.
        # Here, ratio = 6 / 10 = 0.6.
        (0.0, False),  # 0.6 <= 0.0 is False
        (0.1, False),
        (0.2, False),
        (0.3, False),
        (0.4, False),
        (0.5, False),
        (0.6, True),  # 0.6 <= 0.6 is True
        (0.7, True),
        (0.8, True),
        (0.9, True),
        (1.0, True),
    ],
)
def test_ready_to_send_observation_with_varying_threshold(robot_client, g_threshold: float, expected: bool):
    """Validate `_ready_to_send_observation` with fixed sizes and varying `g`."""
    # Fixed sizes for this test: ratio = 6 / 10 = 0.6
    chunk_size = 10
    queue_len = 6

    robot_client.action_chunk_size = chunk_size
    # This is the parameter we are testing
    robot_client._chunk_size_threshold = g_threshold

    # Fill queue with dummy actions
    robot_client.action_queue = Queue()
    dummy_actions = _make_actions(start_ts=time.time(), start_t=0, count=queue_len)
    for act in dummy_actions:
        robot_client.action_queue.put(act)

    assert robot_client._ready_to_send_observation() is expected


def test_rtc_merge_skips_nothing_when_no_actions_consumed(robot_client):
    """First RTC chunk must not drop prefix actions (startup jerk fix)."""
    from lerobot.async_inference.helpers import TimedAction
    from lerobot.policies.rtc.action_queue import ActionQueue
    from lerobot.policies.rtc.configuration_rtc import RTCConfig
    from lerobot.policies.rtc.latency_tracker import LatencyTracker

    robot_client.config.rtc = RTCConfig(enabled=True, execution_horizon=10)
    robot_client.rtc_queue = ActionQueue(robot_client.config.rtc)
    robot_client.latency_tracker = LatencyTracker()
    robot_client._rtc_index_before = 0
    robot_client._rtc_request_start = time.perf_counter() - 0.5  # would be ~15 steps at 30fps

    incoming = [
        TimedAction(
            timestamp=time.time(),
            timestep=i,
            action=torch.full((6,), float(i)),
            original_action=torch.full((6,), float(i) + 100),
        )
        for i in range(10)
    ]
    robot_client._merge_rtc_actions(incoming)

    assert robot_client.rtc_queue.qsize() == 10
    leftover = robot_client.rtc_queue.get_left_over()
    assert leftover is not None
    torch.testing.assert_close(leftover[0], torch.full((6,), 100.0))


def test_remote_policy_config_forwards_rename_map():
    import pickle

    from lerobot.async_inference.configs import RobotClientConfig
    from lerobot.async_inference.robot_client import RobotClient
    from tests.mocks.mock_robot import MockRobotConfig

    rename_map = {
        "observation.images.left_wrist": "observation.images.left_wrist_0_rgb",
        "observation.images.right_wrist": "observation.images.right_wrist_0_rgb",
        "observation.images.right_front": "observation.images.base_0_rgb",
    }
    cfg = RobotClientConfig(
        robot=MockRobotConfig(),
        server_address="localhost:9999",
        policy_type="test",
        pretrained_name_or_path="test",
        actions_per_chunk=20,
        rename_map=rename_map,
        confirm_chunk=True,
    )
    client = RobotClient(cfg)
    try:
        assert client.policy_config.rename_map == rename_map
        assert pickle.loads(pickle.dumps(client.policy_config)).rename_map == rename_map
    finally:
        if client.robot.is_connected:
            client.stop()


def test_confirm_chunk_merge_ignores_wall_clock_rtt(robot_client):
    """First RTC chunk (consumed==0) must not drop prefix even if wall clock is large."""
    from lerobot.async_inference.helpers import TimedAction
    from lerobot.policies.rtc.action_queue import ActionQueue
    from lerobot.policies.rtc.configuration_rtc import RTCConfig
    from lerobot.policies.rtc.latency_tracker import LatencyTracker

    robot_client.config.confirm_chunk = True
    robot_client.config.rtc = RTCConfig(enabled=True, mode="trained", execution_horizon=10)
    robot_client.rtc_queue = ActionQueue(robot_client.config.rtc)
    robot_client.latency_tracker = LatencyTracker()
    robot_client._rtc_index_before = 0
    # 5s * 30fps = 150 steps; if wall clock leaked into merge_delay, the chunk would be skipped.
    robot_client._rtc_request_start = time.perf_counter() - 5.0

    incoming = [
        TimedAction(
            timestamp=time.time(),
            timestep=i,
            action=torch.full((6,), float(i)),
            original_action=torch.full((6,), float(i)),
        )
        for i in range(50)
    ]
    robot_client._merge_rtc_actions(incoming)

    assert robot_client.rtc_queue.qsize() == 50


def test_rtc_ready_to_send_waits_while_inflight(robot_client):
    from lerobot.policies.rtc.action_queue import ActionQueue
    from lerobot.policies.rtc.configuration_rtc import RTCConfig

    robot_client.config.rtc = RTCConfig(enabled=True)
    robot_client.rtc_queue = ActionQueue(robot_client.config.rtc)
    robot_client.action_chunk_size = 20
    robot_client._rtc_inflight = True
    assert robot_client._ready_to_send_observation() is False

    robot_client._rtc_inflight = False
    assert robot_client._ready_to_send_observation() is True


def test_confirm_keys_enter_space_q(robot_client):
    robot_client.config.confirm_chunk = True
    robot_client._on_confirm_key("enter")
    assert robot_client._enter_go.is_set()
    assert not robot_client._home_go.is_set()
    assert not robot_client.shutdown_event.is_set()

    robot_client._enter_go.clear()
    robot_client._on_confirm_key("n")
    assert not robot_client._enter_go.is_set()

    robot_client._on_confirm_key("space")
    assert robot_client._home_go.is_set()
    assert not robot_client.shutdown_event.is_set()

    robot_client._home_go.clear()
    robot_client._on_confirm_key("q")
    assert robot_client.shutdown_event.is_set()
    assert not robot_client._home_go.is_set()


def test_enter_toggles_policy_and_pause_keeps_hold(robot_client):
    robot_client.config.confirm_chunk = True
    robot_client._held_action = {"joint_1.pos": 1.0}
    robot_client.control_loop_observation = lambda *args, **kwargs: None
    robot_client.control_loop_action = lambda *args, **kwargs: {}

    robot_client._enter_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._policy_active is True

    robot_client._enter_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._policy_active is False
    assert robot_client._held_action == {"joint_1.pos": 1.0}


def test_home_without_go_home_clears_held_action(robot_client):
    robot_client._held_action = {"joint_1.pos": 1.0}
    robot_client._policy_active = True
    robot_client._enter_go.set()
    robot_client._home_robot("test")
    assert robot_client._held_action is None
    assert robot_client._policy_active is False
    assert robot_client._chunk_phase == "idle"
    assert not robot_client._enter_go.is_set()
    assert not robot_client._home_go.is_set()


def test_home_fails_closed_when_go_home_returns_false(robot_client):
    robot_client.robot.go_home = lambda: False
    robot_client._policy_active = True
    robot_client._enter_go.set()
    assert robot_client._home_robot("test") is False
    assert robot_client.shutdown_event.is_set()
    assert robot_client._policy_active is False
    assert not robot_client._enter_go.is_set()


def _enable_traj(robot_client, tmp_path):
    from lerobot.async_inference.trajectory_recorder import TrajectoryRecorder

    robot_client.config.confirm_chunk = True
    robot_client._traj = TrajectoryRecorder(tmp_path, enabled=True)
    robot_client.control_loop_observation = lambda *args, **kwargs: None
    return robot_client._traj


def _queue_action(robot_client, values=(0.1, 0.2, 0.3)):
    from lerobot.async_inference.helpers import TimedAction

    robot_client.action_queue.put(
        TimedAction(timestamp=time.time(), timestep=0, action=torch.tensor(values, dtype=torch.float32))
    )


def test_enter_enter_saves_executed_trajectory(robot_client, tmp_path):
    _enable_traj(robot_client, tmp_path)
    robot_client._enter_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._traj.active is True

    _queue_action(robot_client)
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._traj.n_steps == 1

    robot_client._enter_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._policy_active is False
    assert robot_client._traj.active is False
    csvs = list(tmp_path.rglob("run_001.csv"))
    assert len(csvs) == 1
    with csvs[0].open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert "cmd.motor_1.pos" in rows[0]

    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert [p.name for p in tmp_path.rglob("run_*.csv")] == ["run_001.csv"]


def test_space_before_second_enter_discards_trajectory(robot_client, tmp_path):
    _enable_traj(robot_client, tmp_path)
    robot_client._enter_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    _queue_action(robot_client)
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._traj.n_steps == 1

    robot_client._home_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    assert robot_client._traj.active is False
    assert list(tmp_path.rglob("*.csv")) == []


def test_stop_before_second_enter_discards_trajectory(robot_client, tmp_path):
    _enable_traj(robot_client, tmp_path)
    robot_client._enter_go.set()
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    _queue_action(robot_client)
    robot_client._control_loop_confirm_chunk(task="t", verbose=False)
    robot_client.stop()
    assert robot_client._traj.active is False
    assert list(tmp_path.rglob("*.csv")) == []


def test_confirm_chunk_drops_aborted_request(robot_client):
    from lerobot.async_inference.helpers import TimedAction
    from lerobot.policies.rtc.action_queue import ActionQueue
    from lerobot.policies.rtc.configuration_rtc import RTCConfig

    robot_client.config.confirm_chunk = True
    robot_client.config.rtc = RTCConfig(enabled=True, mode="trained", execution_horizon=10)
    robot_client.rtc_queue = ActionQueue(robot_client.config.rtc)
    robot_client._cmd_gen = 2
    robot_client._obs_gen = 1
    robot_client._rtc_index_before = 0
    robot_client._rtc_request_start = time.perf_counter()
    incoming = [
        TimedAction(
            timestamp=time.time(),
            timestep=i,
            action=torch.full((6,), float(i)),
            original_action=torch.full((6,), float(i)),
        )
        for i in range(50)
    ]
    robot_client._merge_rtc_actions(incoming)
    assert robot_client.rtc_queue.qsize() == 0


# -----------------------------------------------------------------------------
# Regression test: robot type registry populated by robot_client imports
# -----------------------------------------------------------------------------


def test_robot_client_registers_builtin_robot_types():
    """Importing robot_client must populate RobotConfig's ChoiceRegistry.

    This is a regression test for a bug introduced in #2425, where removing
    robot module imports from robot_client.py caused RobotConfig's registry to
    be empty, breaking CLI argument parsing with:
      error: argument --robot.type: invalid choice: 'so101_follower' (choose from )

    Robot types are registered via @RobotConfig.register_subclass() decorators
    at import time, so all supported modules must be explicitly imported.
    """
    import lerobot.async_inference.robot_client  # noqa: F401
    from lerobot.robots.config import RobotConfig

    known_choices = RobotConfig.get_known_choices()

    expected_robot_types = [
        "so100_follower",
        "so101_follower",
        "koch_follower",
        "omx_follower",
        "bi_so_follower",
    ]
    for robot_type in expected_robot_types:
        assert robot_type in known_choices, (
            f"Robot type '{robot_type}' is not registered in RobotConfig's ChoiceRegistry. "
            f"Ensure the corresponding module is imported in robot_client.py. "
            f"Known choices: {sorted(known_choices)}"
        )
