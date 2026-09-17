"""
Merge a LoRA adapter trained on top of pi05 into the base weights,
exporting a self-contained policy checkpoint for direct deployment.
"""

import argparse
import logging
import os
import shutil
from pathlib import Path
import torch
from peft import PeftConfig, PeftModel
from lerobot.policies.factory import get_policy_class
from lerobot.policies.pi05.configuration_pi05 import PI05Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def merge_lora_to_base(
    base_path: str,
    lora_path: str,
    output_path: str,
    device: str = "cuda:0",
) -> None:
    base_dir = Path(base_path).resolve()
    lora_dir = Path(lora_path).resolve()
    out_dir = Path(output_path).resolve()

    if not base_dir.exists():
        raise FileNotFoundError(f"Base model path does not exist: {base_dir}")
    if not lora_dir.exists():
        raise FileNotFoundError(f"LoRA model path does not exist: {lora_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading training policy config from %s", lora_dir)
    lora_policy_cfg = PI05Config.from_pretrained(str(lora_dir))

    logger.info("Instantiating base model from %s on device %s", base_dir, device)
    policy_cls = get_policy_class("pi05")
    # Load base weights with the specialized features configuration from LoRA training
    base_policy = policy_cls.from_pretrained(
        str(base_dir),
        config=lora_policy_cfg,
    )
    base_policy.to(device)

    logger.info("Loading and attaching LoRA adapter from %s", lora_dir)
    peft_config = PeftConfig.from_pretrained(str(lora_dir))
    peft_model = PeftModel.from_pretrained(
        base_policy,
        str(lora_dir),
        config=peft_config,
    )

    logger.info("Merging LoRA weights into base weights (merge_and_unload)...")
    merged_policy = peft_model.merge_and_unload()

    # Clean up config to represent a standalone full model
    merged_policy.config.use_peft = False
    merged_policy.config.pretrained_path = None
    merged_policy.to("cpu")

    logger.info("Saving merged standalone policy to %s", out_dir)
    merged_policy.save_pretrained(str(out_dir))

    # Copy preprocessors, postprocessors, and tokenizer assets
    logger.info("Copying preprocessing, postprocessing and tokenizer assets...")
    for item in lora_dir.iterdir():
        if item.name in [
            "adapter_model.safetensors",
            "adapter_config.json",
            "config.json",  # already saved with updated fields
        ]:
            continue
        dest = out_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
            logger.info("Copied directory %s", item.name)
        elif item.is_file():
            shutil.copy2(item, dest)
            logger.info("Copied file %s", item.name)

    logger.info("Merge completed successfully! Output saved to: %s", out_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge pi05 LoRA weights into base model")
    parser.add_argument(
        "--base_path",
        type=str,
        default="/data1/hmcai/lerobot/checpoints/pi05_base",
        help="Path to base model directory",
    )
    parser.add_argument(
        "--lora_path",
        type=str,
        default="/data1/hmcai/lerobot/outputs/piper_pi05_lora_8gpu/checkpoints/030000/pretrained_model",
        help="Path to LoRA checkpoint directory",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="/data1/hmcai/lerobot/outputs/piper_pi05_merged_030000",
        help="Path to output merged model directory",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cuda:0" if torch.cuda.is_available() else "cpu",
        help="Device to perform the merge on",
    )
    args = parser.parse_args()

    merge_lora_to_base(
        base_path=args.base_path,
        lora_path=args.lora_path,
        output_path=args.output_path,
        device=args.device,
    )

