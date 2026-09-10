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

"""
Example command:
```shell
python src/lerobot/async_inference/robot_client.py \
    --robot.type=so100_follower \
    --robot.port=/dev/tty.usbmodem58760431541 \
    --robot.cameras="{ front: {type: opencv, index_or_path: 0, width: 1920, height: 1080, fps: 30}}" \
    --robot.id=black \
    --task="dummy" \
    --server_address=127.0.0.1:8080 \
    --policy_type=act \
    --pretrained_name_or_path=user/model \
    --policy_device=mps \
    --client_device=cpu \
    --actions_per_chunk=50 \
    --chunk_size_threshold=0.5 \
    --aggregate_fn_name=weighted_average \
    --debug_visualize_queue_size=True

# Remote RTC (leftover over the wire). Requires the same patched server.
python src/lerobot/async_inference/robot_client.py \
    --robot.type=so100_follower \
    --server_address=127.0.0.1:8080 \
    --policy_type=pi05 \
    --pretrained_name_or_path=user/model \
    --policy_device=cuda \
    --actions_per_chunk=50 \
    --rtc.enabled=true \
    --rtc.mode=trained \
    --rtc.execution_horizon=20 \
    --confirm_chunk=true
```
"""

import contextlib
import logging
import math
import pickle  # nosec
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pprint import pformat
from queue import Queue
from typing import Any

import draccus
import grpc
import torch

from lerobot.cameras.opencv import OpenCVCameraConfig  # noqa: F401
from lerobot.cameras.zmq.configuration_zmq import ZMQCameraConfig  # noqa: F401

# `lerobot.cameras.realsense` imports pyrealsense2 eagerly whenever the package is merely
# installed, and on some platforms it is installed but not loadable (e.g. a Jetson wheel built
# against a newer glibc). Probe the dependency itself rather than the camera module, so a missing
# realsense only costs us that camera type, while any other import error still surfaces.
try:
    import pyrealsense2  # noqa: F401
except ImportError as e:
    logging.warning("realsense camera type unavailable: pyrealsense2 failed to import (%s)", e)
else:
    from lerobot.cameras.realsense import RealSenseCameraConfig  # noqa: F401

from lerobot.policies.rtc.action_queue import ActionQueue
from lerobot.policies.rtc.latency_tracker import LatencyTracker
from lerobot.robots import (  # noqa: F401
    Robot,
    RobotConfig,
    bi_piper_follower,
    bi_so_follower,
    koch_follower,
    make_robot_from_config,
    omx_follower,
    piper_follower,
    so_follower,
    unitree_g1,
)
from lerobot.transport import (
    services_pb2,  # type: ignore
    services_pb2_grpc,  # type: ignore
)
from lerobot.transport.utils import grpc_channel_options, send_bytes_in_chunks
from lerobot.utils.import_utils import register_third_party_plugins
from lerobot.utils.keyboard_input import TerminalKeyListener, create_key_listener

from .configs import RobotClientConfig
from .helpers import (
    Action,
    FPSTracker,
    Observation,
    RawObservation,
    RemotePolicyConfig,
    TimedAction,
    TimedObservation,
    get_logger,
    map_robot_keys_to_lerobot_features,
    visualize_action_queue_size,
)
from .trajectory_recorder import TrajectoryRecorder


class RobotClient:
    prefix = "robot_client"
    logger = get_logger(prefix)

    def __init__(self, config: RobotClientConfig):
        """Initialize RobotClient with unified configuration.

        Args:
            config: RobotClientConfig containing all configuration parameters
        """
        # Store configuration
        self.config = config
        self.robot = make_robot_from_config(config.robot)
        self.robot.connect()

        lerobot_features = map_robot_keys_to_lerobot_features(self.robot)

        # Use environment variable if server_address is not provided in config
        self.server_address = config.server_address

        self.policy_config = RemotePolicyConfig(
            config.policy_type,
            config.pretrained_name_or_path,
            lerobot_features,
            config.actions_per_chunk,
            config.policy_device,
            rename_map=config.rename_map,
            rtc_config=config.rtc if config.rtc.enabled else None,
        )
        self.channel = grpc.insecure_channel(
            self.server_address, grpc_channel_options(initial_backoff=f"{config.environment_dt:.4f}s")
        )
        self.stub = services_pb2_grpc.AsyncInferenceStub(self.channel)
        self.logger.info(f"Initializing client to connect to server at {self.server_address}")

        self.shutdown_event = threading.Event()

        # Initialize client side variables
        self.latest_action_lock = threading.Lock()
        self.latest_action = -1
        self.action_chunk_size = -1

        self._chunk_size_threshold = config.chunk_size_threshold

        self.action_queue = Queue()
        self.action_queue_lock = threading.Lock()  # Protect queue operations
        self.action_queue_size = []
        self.start_barrier = threading.Barrier(2)  # 2 threads: action receiver, control loop

        self.rtc_queue: ActionQueue | None = ActionQueue(config.rtc) if config.rtc.enabled else None
        self.latency_tracker = LatencyTracker() if config.rtc.enabled else None
        self._rtc_lock = threading.Lock()
        self._rtc_inflight = False
        self._rtc_index_before = 0
        self._rtc_request_start = 0.0

        # FPS measurement
        self.fps_tracker = FPSTracker(target_fps=self.config.fps)

        self.logger.info("Robot connected and ready")
        if config.rtc.enabled:
            self.logger.info(
                "Remote RTC enabled | mode=%s | execution_horizon=%s",
                config.rtc.mode,
                config.rtc.execution_horizon,
            )

        # Use an event for thread-safe coordination
        self.must_go = threading.Event()
        self.must_go.set()  # Initially set - observations qualify for direct processing

        self._confirm_listener = None
        self._enter_go = threading.Event()
        self._home_go = threading.Event()
        self._policy_active = False
        self._chunk_phase = "idle"
        self._held_action: dict[str, Any] | None = None
        # Generation counters so a late GetActions after Space/home cannot be executed.
        self._cmd_gen = 0
        self._obs_gen = 0
        self._rx_gen = -1
        self._traj = TrajectoryRecorder(
            config.trajectory_dir,
            enabled=bool(config.record_trajectory and config.confirm_chunk),
        )
        self._traj_obs_warned = False

    @property
    def running(self):
        return not self.shutdown_event.is_set()

    @property
    def rtc_enabled(self) -> bool:
        return self.config.rtc.enabled and self.rtc_queue is not None

    def start(self):
        """Start the robot client and connect to the policy server"""
        try:
            self.shutdown_event.clear()
            if self.config.confirm_chunk:
                self._start_confirm_chunk_keyboard()
                if not self._home_robot("startup"):
                    return False

            # client-server handshake
            start_time = time.perf_counter()
            self.stub.Ready(services_pb2.Empty())
            end_time = time.perf_counter()
            self.logger.debug(f"Connected to policy server in {end_time - start_time:.4f}s")

            # send policy instructions
            policy_config_bytes = pickle.dumps(self.policy_config)
            policy_setup = services_pb2.PolicySetup(data=policy_config_bytes)

            self.logger.info("Sending policy instructions to policy server")
            self.logger.info(
                "Policy handshake | type=%s | path=%s | device=%s | rename_map=%s",
                self.policy_config.policy_type,
                self.policy_config.pretrained_name_or_path,
                self.policy_config.device,
                self.policy_config.rename_map,
            )
            if not self.policy_config.rename_map:
                self.logger.warning(
                    "rename_map is empty; PolicyServer will not remap camera keys to the checkpoint"
                )

            self.stub.SendPolicyInstructions(policy_setup)
            return self.running

        except grpc.RpcError as e:
            self.logger.error(f"Failed to connect to policy server: {e}")
            return False

    def stop(self):
        """Stop the robot client"""
        self._traj.discard_run()
        self.shutdown_event.set()
        self._enter_go.set()
        self._home_go.set()

        if self._confirm_listener is not None:
            with contextlib.suppress(Exception):
                self._confirm_listener.stop()
            self._confirm_listener = None

        self.robot.disconnect()
        self.logger.debug("Robot disconnected")

        self.channel.close()
        self.logger.debug("Client stopped, channel closed")

    def send_observation(
        self,
        obs: TimedObservation,
    ) -> bool:
        """Send observation to the policy server.
        Returns True if the observation was sent successfully, False otherwise."""
        if not self.running:
            raise RuntimeError("Client not running. Run RobotClient.start() before sending observations.")

        if not isinstance(obs, TimedObservation):
            raise ValueError("Input observation needs to be a TimedObservation!")

        start_time = time.perf_counter()
        observation_bytes = pickle.dumps(obs)
        serialize_time = time.perf_counter() - start_time
        self.logger.debug(f"Observation serialization time: {serialize_time:.6f}s")

        try:
            observation_iterator = send_bytes_in_chunks(
                observation_bytes,
                services_pb2.Observation,
                log_prefix="[CLIENT] Observation",
                silent=True,
            )
            _ = self.stub.SendObservations(observation_iterator)
            obs_timestep = obs.get_timestep()
            self.logger.debug(f"Sent observation #{obs_timestep} | ")

            return True

        except grpc.RpcError as e:
            self.logger.error(f"Error sending observation #{obs.get_timestep()}: {e}")
            return False

    def _inspect_action_queue(self):
        if self.rtc_enabled:
            queue_size = self.rtc_queue.qsize()
            return queue_size, list(range(queue_size))
        with self.action_queue_lock:
            queue_size = self.action_queue.qsize()
            timestamps = sorted([action.get_timestep() for action in self.action_queue.queue])
        self.logger.debug(f"Queue size: {queue_size}, Queue contents: {timestamps}")
        return queue_size, timestamps

    def _aggregate_action_queues(
        self,
        incoming_actions: list[TimedAction],
        aggregate_fn: Callable[[torch.Tensor, torch.Tensor], torch.Tensor] | None = None,
    ):
        """Finds the same timestep actions in the queue and aggregates them using the aggregate_fn"""
        if aggregate_fn is None:
            # default aggregate function: take the latest action
            def aggregate_fn(x1, x2):
                return x2

        future_action_queue = Queue()
        with self.action_queue_lock:
            internal_queue = self.action_queue.queue

        current_action_queue = {action.get_timestep(): action.get_action() for action in internal_queue}

        for new_action in incoming_actions:
            with self.latest_action_lock:
                latest_action = self.latest_action

            # New action is older than the latest action in the queue, skip it
            if new_action.get_timestep() <= latest_action:
                continue

            # If the new action's timestep is not in the current action queue, add it directly
            elif new_action.get_timestep() not in current_action_queue:
                future_action_queue.put(new_action)
                continue

            # If the new action's timestep is in the current action queue, aggregate it
            # TODO: There is probably a way to do this with broadcasting of the two action tensors
            future_action_queue.put(
                TimedAction(
                    timestamp=new_action.get_timestamp(),
                    timestep=new_action.get_timestep(),
                    action=aggregate_fn(
                        current_action_queue[new_action.get_timestep()], new_action.get_action()
                    ),
                )
            )

        with self.action_queue_lock:
            self.action_queue = future_action_queue

    def _merge_rtc_actions(self, timed_actions: list[TimedAction]) -> None:
        """Replace the RTC queue with the incoming chunk, skipping steps already executed."""
        original = torch.stack([ta.get_original_action().detach().cpu() for ta in timed_actions])
        processed = torch.stack([ta.get_action().detach().cpu() for ta in timed_actions])

        with self._rtc_lock:
            index_before = self._rtc_index_before
            request_start = self._rtc_request_start
            self._rtc_inflight = False

        elapsed = time.perf_counter() - request_start
        measured_delay = math.ceil(elapsed / self.config.environment_dt)
        consumed = max(0, self.rtc_queue.get_action_index() - index_before)
        if self.config.confirm_chunk:
            with self._rtc_lock:
                if self._obs_gen != self._cmd_gen:
                    self.logger.info("Dropping action chunk from an aborted request")
                    return
                self._rx_gen = self._obs_gen
        # First chunk (nothing consumed yet) must not skip the prefix — that caused a startup jerk.
        merge_delay = 0 if consumed == 0 else measured_delay
        if self.latency_tracker is not None:
            self.latency_tracker.add(elapsed)

        self.rtc_queue.merge(original, processed, merge_delay, index_before)
        self.logger.debug(
            "RTC merge | delay=%d (consumed=%d, measured=%d) | queue=%d",
            merge_delay,
            consumed,
            measured_delay,
            self.rtc_queue.qsize(),
        )

    def receive_actions(self, verbose: bool = False):
        """Receive actions from the policy server"""
        # Wait at barrier for synchronized start
        self.start_barrier.wait()
        self.logger.info("Action receiving thread starting")

        while self.running:
            try:
                # Use StreamActions to get a stream of actions from the server
                actions_chunk = self.stub.GetActions(services_pb2.Empty())
                if len(actions_chunk.data) == 0:
                    if self.rtc_enabled:
                        with self._rtc_lock:
                            self._rtc_inflight = False
                    continue  # received `Empty` from server, wait for next call

                receive_time = time.time()

                # Deserialize bytes back into list[TimedAction]
                deserialize_start = time.perf_counter()
                timed_actions = pickle.loads(actions_chunk.data)  # nosec
                deserialize_time = time.perf_counter() - deserialize_start

                # Log device type of received actions
                if len(timed_actions) > 0:
                    received_device = timed_actions[0].get_action().device.type
                    self.logger.debug(f"Received actions on device: {received_device}")

                # Move actions to client_device (e.g., for downstream planners that need GPU)
                client_device = self.config.client_device
                if client_device != "cpu":
                    for timed_action in timed_actions:
                        if timed_action.get_action().device.type != client_device:
                            timed_action.action = timed_action.get_action().to(client_device)
                    self.logger.debug(f"Converted actions to device: {client_device}")
                else:
                    self.logger.debug(f"Actions kept on device: {client_device}")

                self.action_chunk_size = max(self.action_chunk_size, len(timed_actions))

                # Calculate network latency if we have matching observations
                if len(timed_actions) > 0 and verbose:
                    with self.latest_action_lock:
                        latest_action = self.latest_action

                    self.logger.debug(f"Current latest action: {latest_action}")

                    # Get queue state before changes
                    old_size, old_timesteps = self._inspect_action_queue()
                    if not old_timesteps:
                        old_timesteps = [latest_action]  # queue was empty

                    # Log incoming actions
                    incoming_timesteps = [a.get_timestep() for a in timed_actions]

                    first_action_timestep = timed_actions[0].get_timestep()
                    server_to_client_latency = (receive_time - timed_actions[0].get_timestamp()) * 1000

                    self.logger.info(
                        f"Received action chunk for step #{first_action_timestep} | "
                        f"Latest action: #{latest_action} | "
                        f"Incoming actions: {incoming_timesteps[0]}:{incoming_timesteps[-1]} | "
                        f"Network latency (server->client): {server_to_client_latency:.2f}ms | "
                        f"Deserialization time: {deserialize_time * 1000:.2f}ms"
                    )

                # Update action queue
                start_time = time.perf_counter()
                if self.rtc_enabled:
                    self._merge_rtc_actions(timed_actions)
                else:
                    self._aggregate_action_queues(timed_actions, self.config.aggregate_fn)
                queue_update_time = time.perf_counter() - start_time

                self.must_go.set()  # after receiving actions, next empty queue triggers must-go processing!

                if verbose:
                    # Get queue state after changes
                    new_size, new_timesteps = self._inspect_action_queue()

                    with self.latest_action_lock:
                        latest_action = self.latest_action

                    self.logger.info(
                        f"Latest action: {latest_action} | "
                        f"Old action steps: {old_timesteps[0]}:{old_timesteps[-1]} | "
                        f"Incoming action steps: {incoming_timesteps[0]}:{incoming_timesteps[-1]} | "
                        f"Updated action steps: {new_timesteps[0]}:{new_timesteps[-1]}"
                    )
                    self.logger.debug(
                        f"Queue update complete ({queue_update_time:.6f}s) | "
                        f"Before: {old_size} items | "
                        f"After: {new_size} items | "
                    )

            except grpc.RpcError as e:
                self.logger.error(f"Error receiving actions: {e}")

    def actions_available(self):
        """Check if there are actions available in the queue"""
        if self.rtc_enabled:
            return not self.rtc_queue.empty()
        with self.action_queue_lock:
            return not self.action_queue.empty()

    def _chunk_ready_for_current_request(self) -> bool:
        """True only for the chunk that matches the latest Enter, not a leftover after home."""
        if not self.actions_available():
            return False
        if not self.config.confirm_chunk:
            return True
        return self._rx_gen == self._obs_gen == self._cmd_gen

    def _action_tensor_to_action_dict(self, action_tensor: torch.Tensor) -> dict[str, float]:
        action = {key: action_tensor[i].item() for i, key in enumerate(self.robot.action_features)}
        return action

    def control_loop_action(self, verbose: bool = False) -> dict[str, Any]:
        """Reading and performing actions in local queue"""

        # Lock only for queue operations
        get_start = time.perf_counter()
        if self.rtc_enabled:
            action_tensor = self.rtc_queue.get()
            self.action_queue_size.append(self.rtc_queue.qsize())
            get_end = time.perf_counter() - get_start
            if action_tensor is None:
                return {}
            timed_timestep = None
        else:
            with self.action_queue_lock:
                self.action_queue_size.append(self.action_queue.qsize())
                # Get action from queue
                timed_action = self.action_queue.get_nowait()
            get_end = time.perf_counter() - get_start
            action_tensor = timed_action.get_action()
            timed_timestep = timed_action.get_timestep()

        _performed_action = self.robot.send_action(self._action_tensor_to_action_dict(action_tensor))
        if self._traj.active:
            self._traj.append(_performed_action, self._read_measured_state())
        with self.latest_action_lock:
            if timed_timestep is None:
                self.latest_action += 1
            else:
                self.latest_action = timed_timestep

        if verbose:
            if self.rtc_enabled:
                current_queue_size = self.rtc_queue.qsize()
            else:
                with self.action_queue_lock:
                    current_queue_size = self.action_queue.qsize()
            self.logger.debug(
                f"Action #{self.latest_action} performed | Queue size: {current_queue_size} | "
                f"Pop took {get_end:.6f}s"
            )

        return _performed_action

    def _ready_to_send_observation(self):
        """Flags when the client is ready to send an observation"""
        if self.rtc_enabled:
            with self._rtc_lock:
                if self._rtc_inflight:
                    return False
            if self.action_chunk_size <= 0:
                return True
            return self.rtc_queue.qsize() / self.action_chunk_size <= self._chunk_size_threshold
        with self.action_queue_lock:
            return self.action_queue.qsize() / self.action_chunk_size <= self._chunk_size_threshold

    def control_loop_observation(self, task: str, verbose: bool = False) -> RawObservation:
        try:
            # Get serialized observation bytes from the function
            start_time = time.perf_counter()

            raw_observation: RawObservation = self.robot.get_observation()
            raw_observation["task"] = task

            with self.latest_action_lock:
                latest_action = self.latest_action

            leftover = None
            inference_delay = 0
            execution_horizon = None
            if self.rtc_enabled:
                leftover = self.rtc_queue.get_left_over()
                if leftover is not None and leftover.numel() == 0:
                    leftover = None
                max_latency = 0.0 if self.latency_tracker is None else (self.latency_tracker.max() or 0.0)
                if leftover is None:
                    inference_delay = 0
                else:
                    inference_delay = math.ceil(max_latency / self.config.environment_dt)
                execution_horizon = self.config.rtc.execution_horizon
                with self._rtc_lock:
                    if self.config.confirm_chunk:
                        self._obs_gen = self._cmd_gen
                    self._rtc_index_before = self.rtc_queue.get_action_index()
                    self._rtc_request_start = time.perf_counter()
                    self._rtc_inflight = True

            observation = TimedObservation(
                timestamp=time.time(),  # need time.time() to compare timestamps across client and server
                observation=raw_observation,
                timestep=max(latest_action, 0),
                inference_delay=inference_delay,
                prev_chunk_left_over=leftover,
                execution_horizon=execution_horizon,
            )

            obs_capture_time = time.perf_counter() - start_time

            # If there are no actions left in the queue, the observation must go through processing!
            if self.rtc_enabled:
                observation.must_go = self.must_go.is_set() and self.rtc_queue.empty()
                current_queue_size = self.rtc_queue.qsize()
            else:
                with self.action_queue_lock:
                    observation.must_go = self.must_go.is_set() and self.action_queue.empty()
                    current_queue_size = self.action_queue.qsize()

            sent = self.send_observation(observation)
            if not sent and self.rtc_enabled:
                with self._rtc_lock:
                    self._rtc_inflight = False

            self.logger.debug(f"QUEUE SIZE: {current_queue_size} (Must go: {observation.must_go})")
            if observation.must_go:
                # must-go event will be set again after receiving actions
                self.must_go.clear()

            if verbose:
                # Calculate comprehensive FPS metrics
                fps_metrics = self.fps_tracker.calculate_fps_metrics(observation.get_timestamp())

                self.logger.info(
                    f"Obs #{observation.get_timestep()} | "
                    f"Avg FPS: {fps_metrics['avg_fps']:.2f} | "
                    f"Target: {fps_metrics['target_fps']:.2f}"
                )

                self.logger.debug(
                    f"Ts={observation.get_timestamp():.6f} | Capturing observation took {obs_capture_time:.6f}s"
                )

            return raw_observation

        except Exception as e:
            if self.rtc_enabled:
                with self._rtc_lock:
                    self._rtc_inflight = False
            self.logger.error(f"Error in observation sender: {e}")

    def _on_confirm_key(self, name: str) -> None:
        key = name.lower()
        if key == "enter":
            self._enter_go.set()
            self.logger.info("Enter: toggle run/pause")
        elif key == "space":
            self._home_go.set()
            self.logger.info("Space: will go home, then wait for Enter")
        elif key in {"esc", "q"}:
            self.logger.info("Stop key pressed")
            self.shutdown_event.set()
            self._enter_go.set()

    def _start_confirm_chunk_keyboard(self) -> None:
        # Terminal-only: pynput is global on X11 and would treat Space in any window as home.
        if sys.stdin.isatty():
            listener = TerminalKeyListener(self._on_confirm_key)
            listener.start()
            self._confirm_listener = listener
            self.logger.info(
                "Using terminal keyboard — keep this terminal focused "
                "(Enter=run/pause, Space=home, q/Esc=stop)."
            )
        else:
            self._confirm_listener = create_key_listener(
                self._on_confirm_key,
                controls_help="Enter=run/pause, Space=home, q/Esc=stop",
            )
            if self._confirm_listener is None:
                self.logger.warning(
                    "confirm_chunk is on but no keyboard is available; the robot will hold and wait"
                )
        self.logger.info("Safety gate: homing, then Enter=start/pause, Space=home, q/Esc=stop")
        if self._traj.enabled:
            self.logger.info(
                "Trajectory recording on: each Enter→Enter run saves CSV+PNG under %s",
                self.config.trajectory_dir,
            )

    def _read_measured_state(self) -> dict[str, Any]:
        """Joint/gripper readings only. Missing method or a failed read returns {}."""
        getter = getattr(self.robot, "get_proprioception", None)
        if getter is None:
            return {}
        try:
            return getter()
        except Exception:
            if not self._traj_obs_warned:
                self.logger.warning("get_proprioception failed; trajectory will record commands only")
                self._traj_obs_warned = True
            return {}

    def _hold_last_action(self) -> None:
        if self._held_action is not None:
            self.robot.send_action(self._held_action)

    def _clear_action_state(self, *, keep_hold: bool = False) -> None:
        """Drop queued/in-flight actions so a later Enter starts a fresh request."""
        with self._rtc_lock:
            self._cmd_gen += 1
            self._rtc_inflight = False
            self._rtc_index_before = 0
        if self.rtc_enabled and self.rtc_queue is not None:
            self.rtc_queue.clear()
        with self.action_queue_lock:
            self.action_queue = Queue()
        if not keep_hold:
            self._held_action = None
        self.must_go.set()

    def _pause_in_place(self) -> None:
        """Stop policy requests and hold the last commanded pose. No home, no EmergencyStop."""
        self._policy_active = False
        held = self._held_action
        self._clear_action_state(keep_hold=True)
        self._held_action = held
        self._chunk_phase = "idle"
        self.logger.info("Paused at current pose. Enter=resume, Space=home, q=stop")

    def _home_robot(self, reason: str) -> bool:
        """Go to joint zeros, then idle. Does not send a policy observation.

        Piper followers wait the full ``go_home`` settle (default 6s) at reduced
        MOVE_J speed, same as ``lerobot-record`` Space-home. Success is fail-closed.
        """
        self.logger.info("Homing (%s); waiting for joints to settle...", reason)
        self._policy_active = False
        self._clear_action_state()
        go_home = getattr(self.robot, "go_home", None)
        settled = True
        if go_home is None:
            self.logger.warning("robot has no go_home(); holding in place")
        else:
            result = go_home()
            if result is False:
                settled = False
        # Drop any chunk that arrived on the receiver thread while homing.
        self._clear_action_state()
        self._chunk_phase = "idle"
        self._enter_go.clear()
        self._home_go.clear()
        if not settled:
            self.logger.error(
                "Homing failed: joints not near zero after settle. "
                "CAN may be DOWN or the arm did not reach zero in time. Not starting motion."
            )
            self.shutdown_event.set()
            return False
        self.logger.info("At home. Press Enter to start, Space to home again, q to stop")
        return True

    def _control_loop_confirm_chunk(self, task: str, verbose: bool) -> None:
        """Enter toggles continuous policy. Space homes. Pause holds the last pose."""
        if not self.running:
            return
        if self._home_go.is_set():
            self._home_go.clear()
            self._enter_go.clear()
            self._traj.discard_run()
            self._home_robot("space")
            return
        if self._enter_go.is_set():
            self._enter_go.clear()
            if self._policy_active:
                self._pause_in_place()
                self._traj.finish_run()
            else:
                self._policy_active = True
                self._traj.start_run()
                self.must_go.set()
                self.logger.info("Running policy continuously. Enter=pause, Space=home, q=stop")

        if self._policy_active:
            if self.actions_available():
                performed = self.control_loop_action(verbose)
                if performed:
                    self._held_action = performed
            if self._ready_to_send_observation():
                self.control_loop_observation(task, verbose)
            return

        self._hold_last_action()

    def control_loop(self, task: str, verbose: bool = False) -> tuple[Observation, Action]:
        """Combined function for executing actions and streaming observations"""
        # Wait at barrier for synchronized start
        self.start_barrier.wait()
        self.logger.info("Control loop thread starting")

        _performed_action = None
        _captured_observation = None

        while self.running:
            control_loop_start = time.perf_counter()
            if self.config.confirm_chunk:
                self._control_loop_confirm_chunk(task, verbose)
            else:
                """Control loop: (1) Performing actions, when available"""
                if self.actions_available():
                    _performed_action = self.control_loop_action(verbose)

                """Control loop: (2) Streaming observations to the remote policy server"""
                if self._ready_to_send_observation():
                    _captured_observation = self.control_loop_observation(task, verbose)

            self.logger.debug(f"Control loop (ms): {(time.perf_counter() - control_loop_start) * 1000:.2f}")
            # Dynamically adjust sleep time to maintain the desired control frequency
            time.sleep(max(0, self.config.environment_dt - (time.perf_counter() - control_loop_start)))

        return _captured_observation, _performed_action


@draccus.wrap()
def async_client(cfg: RobotClientConfig):
    logging.info(pformat(asdict(cfg)))

    # TODO: Assert if checking robot support is still needed with the plugin system
    # if cfg.robot.type not in SUPPORTED_ROBOTS:
    #     raise ValueError(f"Robot {cfg.robot.type} not yet supported!")

    client = RobotClient(cfg)

    try:
        if not client.start():
            return

        client.logger.info("Starting action receiver thread...")
        action_receiver_thread = threading.Thread(target=client.receive_actions, daemon=True)
        action_receiver_thread.start()

        try:
            client.control_loop(task=cfg.task)
        finally:
            client.stop()
            action_receiver_thread.join(timeout=5.0)
            if cfg.debug_visualize_queue_size:
                visualize_action_queue_size(client.action_queue_size)
            client.logger.info("Client stopped")
    finally:
        if client.robot.is_connected:
            client.stop()


if __name__ == "__main__":
    register_third_party_plugins()
    async_client()  # run the client
