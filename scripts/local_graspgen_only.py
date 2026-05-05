"""
Local smoke test: PointCloud -> GraspGen -> meshcat visualization.

No Isaac Sim, no ROS2, no real arm. Lets you verify the GraspGen part of the
ITRI-GraspGen workflow runs end-to-end against a saved PNG pair, before doing
anything else (e.g. swapping in IP-Adapter checkpoints).

Usage:
    uv run scripts/local_graspgen_only.py --target "green cup"
    uv run scripts/local_graspgen_only.py --use-png demo3 --target "banana"

Open the printed meshcat URL. In the tkinter panel you can untick
"Apply Custom Filter" if the cup_qualifier filters everything out (it assumes
world-up; without lab calibration we run in camera frame).
"""

import argparse
import json
import logging
import sys
import types
from pathlib import Path

import numpy as np


def _install_pyzed_stub() -> None:
    """Stub pyzed.sl so PointCloudGenerator imports under --use-png mode without
    a matching ZED SDK installed locally. Only the API surface used by
    ZedCamera.initialize_zed_using_existing_png / capture_images_from_exsisting_png
    is provided. Real-camera paths will silently break — that's intentional for
    local-only smoke testing.
    """
    if "pyzed" in sys.modules:
        return

    class _Mat:
        def __init__(self, w=None, h=None, mat_type=None, mem=None):
            self._data = np.zeros((1, 1, 3), dtype=np.uint8) if w is None else np.zeros((h, w, 3), dtype=np.uint8)

        def get_data(self):
            return self._data

    class _Enum:
        def __getattr__(self, name):
            return name

    sl = types.ModuleType("pyzed.sl")
    sl.Mat = _Mat
    sl.MAT_TYPE = _Enum()
    sl.MEM = _Enum()
    sl.ERROR_CODE = _Enum()
    sl.RESOLUTION = _Enum()
    sl.DEPTH_MODE = _Enum()
    sl.VIEW = _Enum()
    sl.Camera = type("Camera", (), {})
    sl.InitParameters = type("InitParameters", (), {})
    sl.Transform = type("Transform", (), {})
    sl.PositionalTrackingParameters = type("PositionalTrackingParameters", (), {})

    pyzed = types.ModuleType("pyzed")
    pyzed.sl = sl
    sys.modules["pyzed"] = pyzed
    sys.modules["pyzed.sl"] = sl


_install_pyzed_stub()

import torch  # noqa: E402

from common_utils import config  # noqa: E402
from common_utils.common_utils import create_obstacle_info, load_extra_obstacles  # noqa: E402
from common_utils.custom_logger import CustomFormatter  # noqa: E402
from PointCloud_Generation.PC_transform import (  # noqa: E402
    silent_transform_multiple_obj_with_name_dict,
)
from PointCloud_Generation.pointcloud_generation import PointCloudGenerator  # noqa: E402

from grasp_gen.grasp_server import GraspGenSampler, load_grasp_cfg  # noqa: E402
from grasp_gen.utils.meshcat_utils import (  # noqa: E402
    create_visualizer,
    visualize_grasp,
    visualize_pointcloud,
)

handler = logging.StreamHandler()
handler.setFormatter(CustomFormatter())
logging.basicConfig(level=logging.DEBUG, handlers=[handler], force=True)
logger = logging.getLogger(__name__)

PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1]
TRANSFORM_DIR = PROJECT_ROOT_DIR / "PointCloud_Generation" / "transform_config"


def parse_args():
    parser = argparse.ArgumentParser()
    # PointCloudGenerator args (mirror workflow_with_isaacsim.py)
    parser.add_argument("--ckpt_dir", default=str(config.FOUNDATIONSTEREO_CHECKPOINT))
    parser.add_argument("--scale", default=1.0, type=float)
    parser.add_argument("--hiera", default=0, type=int)
    parser.add_argument("--valid_iters", type=int, default=32)
    parser.add_argument("--out_dir", default="./output/", type=str)
    parser.add_argument("--output-tag", default="", type=str)
    parser.add_argument("--erosion_iterations", type=int, default=1)
    parser.add_argument("--max-depth", type=float, default=3.0)
    parser.add_argument("--need-confirm", action="store_true")
    parser.add_argument("--use-png", type=str, default="demo6")
    # Transform
    parser.add_argument(
        "--transform-config",
        type=str,
        default="sim2.json",
        help="JSON in PointCloud_Generation/transform_config/. Auto-created as identity if missing.",
    )
    # GraspGen args
    parser.add_argument("--gripper_config", type=str, default=str(config.GRIPPER_CFG))
    parser.add_argument("--grasp_threshold", type=float, default=0.70)
    parser.add_argument("--num_grasps", type=int, default=200)
    parser.add_argument("--topk_num_grasps", type=int, default=5)
    # Smoke-test specifics
    parser.add_argument(
        "--target",
        type=str,
        default="green cup",
        help="GroundingDINO prompt for the object to grasp.",
    )
    parser.add_argument(
        "--qualifier",
        type=str,
        default="cup_qualifier",
        help="Custom-filter qualifier name (toggleable in the GUI).",
    )
    return parser.parse_args()


def ensure_transform_config(name: str) -> None:
    """If the requested transform config doesn't exist, write an identity one."""
    path = TRANSFORM_DIR / name
    if path.exists():
        return
    logger.warning(
        f"{path} not found; writing identity transform for local visualization. "
        "Replace with lab-calibrated extrinsics before deploying to real arm."
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = {"tx": 0.0, "ty": 0.0, "tz": 0.0, "rr": 0.0, "rp": 0.0, "ry": 0.0}
    with open(path, "w") as f:
        json.dump(identity, f, indent=2)


def main():
    args = parse_args()
    torch.manual_seed(42)
    np.random.seed(42)

    ensure_transform_config(args.transform_config)

    # 1. PNG -> point clouds + per-object masks
    logger.info(f"Initializing PointCloudGenerator (use-png={args.use_png}) ...")
    pc_generator = PointCloudGenerator(args)
    try:
        scene_data = pc_generator.generate_pointcloud(
            target_names=[args.target],
            blockages=[],
            valid_region=None,
        )
    finally:
        # use-png mode never opens the real ZED camera; close() touches
        # self.camera which doesn't exist. Swallow that specific failure.
        try:
            pc_generator.close()
        except AttributeError as e:
            logger.debug(f"Skipping pc_generator.close() (use-png mode): {e}")

    # 2. Transform to world frame (identity if no lab config)
    scene_data = silent_transform_multiple_obj_with_name_dict(
        scene_data, args.transform_config
    )
    extra_obstacles = load_extra_obstacles()
    scene_data = create_obstacle_info(scene_data, extra_obstacles)

    # 3. GraspGen — headless (no tkinter, no DISPLAY required)
    logger.info(f"Loading GraspGen ({args.gripper_config}) ...")
    grasp_cfg = load_grasp_cfg(args.gripper_config)
    gripper_name = grasp_cfg.data.gripper_name
    sampler = GraspGenSampler(grasp_cfg)

    obj_pc = scene_data["object_infos"][args.target]["points"]
    logger.info(f"Running inference on {len(obj_pc)} object points ...")
    grasps_t, conf = GraspGenSampler.run_inference(
        obj_pc,
        sampler,
        grasp_threshold=args.grasp_threshold,
        num_grasps=args.num_grasps,
    )
    grasps = grasps_t.cpu().numpy()
    grasps[:, 3, 3] = 1.0
    logger.warning(f"Got {len(grasps)} grasps after threshold {args.grasp_threshold}")

    # 4. Headless meshcat visualization
    vis = create_visualizer()
    web_url = getattr(vis, "url", lambda: None)() or "http://127.0.0.1:7000/static/"
    print("\n" + "=" * 60)
    print(f"Open this URL in your browser:\n  {web_url}")
    print("If running over SSH:  ssh -L 7000:127.0.0.1:7000 <host>")
    print("=" * 60 + "\n")

    # full scene
    scene_pc = np.array(scene_data["scene_info"]["pc_color"])[0]
    scene_color = np.array(scene_data["scene_info"]["img_color"]).reshape(1, -1, 3)[0]
    visualize_pointcloud(vis, "scene", scene_pc, scene_color, size=0.0025)

    # target object highlighted
    obj_color = np.array(scene_data["object_infos"][args.target]["colors"])
    visualize_pointcloud(vis, "object", obj_pc, obj_color, size=0.004)

    # all grasps (no qualifier — show raw GraspGen output)
    for i, g in enumerate(grasps):
        visualize_grasp(vis, f"grasps/{i:03d}", g, color=[0, 200, 0],
                        gripper_name=gripper_name, linewidth=1.5)

    print(f"Visualized {len(grasps)} grasps in meshcat.")
    input("Press Enter to exit (closes meshcat) ...")


if __name__ == "__main__":
    main()
