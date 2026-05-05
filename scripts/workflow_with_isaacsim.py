import argparse
import logging
import numpy as np
import torch
from pathlib import Path
from common_utils import network_config
from common_utils.custom_logger import CustomFormatter
from common_utils.workflow_control import BaseWorkflowController
from common_utils.socket_communication import (
    NonBlockingJSONSender,
    NonBlockingJSONReceiver,
)
from common_utils import config

# root logger setup
handler = logging.StreamHandler()
handler.setFormatter(CustomFormatter())
logging.basicConfig(level=logging.DEBUG, handlers=[handler], force=True)
logger = logging.getLogger(__name__)

# Project root dir
PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1]


def set_seed(seed):
    torch.manual_seed(seed)
    np.random.seed(seed)


def parse_args():
    parser = argparse.ArgumentParser(description="Manually transform a point cloud.")
    parser.add_argument(
        "--ckpt_dir",
        default=str(config.FOUNDATIONSTEREO_CHECKPOINT),
        type=str,
        help="pretrained model path",
    )

    parser.add_argument(
        "--scale",
        default=1,
        type=float,
        help="downsize the image by scale, must be <=1",
    )
    parser.add_argument("--hiera", default=0, type=int, help="hierarchical inference")
    parser.add_argument(
        "--valid_iters",
        type=int,
        default=32,
        help="number of flow-field updates during forward pass",
    )
    parser.add_argument(
        "--out_dir", default="./output/", type=str, help="the directory to save results"
    )
    parser.add_argument(
        "--output-tag",
        default="",
        type=str,
        help="pretrained model path",
    )
    parser.add_argument(
        "--erosion_iterations",
        type=int,
        default=1,  # can be 6
        help="Number of erosion iterations for the SAM mask.",
    )
    parser.add_argument(
        "--max-depth",
        type=float,
        default=3.0,
        help="max depth for generating pointcloud",
    )
    parser.add_argument(
        "--transform-config",
        type=str,
        default="sim2.json",
        help="transform-config",
    )
    parser.add_argument(
        "--gripper_config",
        type=str,
        default=str(config.GRIPPER_CFG),
        help="Path to gripper configuration YAML file",
    )
    parser.add_argument(
        "--grasp_threshold",
        type=float,
        default=0.70,
        help="Threshold for valid grasps. If -1.0, then the top 100 grasps will be ranked and returned",
    )
    parser.add_argument(
        "--num_grasps",
        type=int,
        default=200,
        help="Number of grasps to generate",
    )
    parser.add_argument(
        "--return_topk",
        action="store_true",
        help="Whether to return only the top k grasps",
    )
    parser.add_argument(
        "--topk_num_grasps",
        type=int,
        default=5,
        help="Number of top grasps to return when return_topk is True",
    )
    parser.add_argument(
        "--need-confirm",
        action="store_true",
        help="decide if we need confirm for groundingDINO detect and grasp Generation",
    )
    parser.add_argument(
        "--save-fullact",
        action="store_true",
        help="save the fullact",
    )
    parser.add_argument(
        "--use-png",
        type=str,
        default="",
        help="Use exisiting images at sample_data/zed_images instead of the real zed camera",
    )
    parser.add_argument(
        "--ip_config",
        type=str,
        default=None,
        help="Path to IP-Adapter training config.yaml (e.g. v2_abs_r095/config.yaml). "
             "Triggers IP-Adapter mode in GraspGeneratorUI.",
    )
    parser.add_argument(
        "--ip_ckpt",
        type=str,
        default=None,
        help="Path to IP-Adapter checkpoint .pth. Required when --ip_config is set.",
    )
    parser.add_argument(
        "--force_no_ip",
        action="store_true",
        help="Load IP-Adapter ckpt but skip patching layers (ablation).",
    )
    parser.add_argument(
        "--gravity",
        type=str,
        default="0,0,-1",
        help="Comma-separated 3D gravity direction in robot-world frame for IP-Adapter "
             "conditioning. Default (0,0,-1) assumes lab-calibrated Z-up world.",
    )
    return parser.parse_args()


class CLIWorkflowController(BaseWorkflowController):
    def __init__(self, args) -> None:
        sender = NonBlockingJSONSender(port=network_config.GRASPGEN_TO_ISAACSIM_PORT)
        receiver = NonBlockingJSONReceiver(
            port=network_config.ISAACSIM_TO_GRASPGEN_PORT
        )
        super().__init__(args, sender, receiver)

    def _send_EOF(self):
        # end of move
        self.sender.send_data(["EOF"])
        response = self.receiver.capture_data()
        while response is None:
            response = self.receiver.capture_data()
        if response["message"] == "EOF and ROS2 Complete":
            logger.warning("Success")
        elif response["message"] == "Abort":
            logger.warning("Abort")
            raise InterruptedError("aborted by isaacsim, stop current action")
        else:
            raise ValueError(f"Unknown message {response['message']}")

    def _handle_keyboard_interrupt(self):
        logger.info("Manual stopping current action.")
        self.sender.send_data(["Reset_to_default"])

    def _grab_command(self):
        print("Please provide the command, or type 'end' to end.")
        return input("Command: "), False


def main():
    args = parse_args()
    set_seed(42)
    with CLIWorkflowController(args) as controller:
        while True:
            controller.handle_task_command()


if __name__ == "__main__":
    main()
