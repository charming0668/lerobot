# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- `scripts/merge_pi05_lora.py`: Standalone utility to merge trained Pi0.5 LoRA weights into the base checkpoint for zero-overhead inference deployment.
- `scripts/eval/start_piper_pi05_policy_server.sh`: One-click launch script for Pi0.5 PolicyServer with preloaded merged weights.
- `docs/eval/rtc_inference_lifecycle_and_execution_horizon.md`: In-depth documentation covering the full RTC action lifecycle and execution horizon dynamics.

### Fixed
- `src/lerobot/policies/rtc/configuration_rtc.py`: Added fail-fast mutual exclusivity validation in `RTCConfig.__post_init__` to prevent setting conflicting `execution_horizon` when `mode='trained'`.
- `scripts/eval/start_robot_client.sh`: Cleaned up conflicting `--rtc.execution_horizon` argument and updated target pretrained path to the self-contained merged model.

