"""
Verify that running v2_r095 IP-Adapter through GraspGeneratorUI (the integrated
path that workflow_with_isaacsim.py will use at the lab) gives equivalent
grasps to the standalone smoke script (scripts/local_graspgen_ip.py, which
calls GraspGenSamplerIP directly).

Run BOTH this and local_graspgen_ip.py with the same --target / --use-png and
visually compare meshcat output. Grasps should cluster on the same regions
of the object.

Usage:
    uv run scripts/local_test_integration.py --target "green cup"
"""

import argparse
import json
import logging
import sys
import types
from pathlib import Path

import numpy as np


def _install_pyzed_stub() -> None:
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
    for _name in ("MAT_TYPE", "MEM", "ERROR_CODE", "RESOLUTION", "DEPTH_MODE", "VIEW"):
        setattr(sl, _name, _Enum())
    for _name in ("Camera", "InitParameters", "Transform", "PositionalTrackingParameters"):
        setattr(sl, _name, type(_name, (), {}))

    pyzed = types.ModuleType("pyzed")
    pyzed.sl = sl
    sys.modules["pyzed"] = pyzed
    sys.modules["pyzed.sl"] = sl


_install_pyzed_stub()

import torch  # noqa: E402

from common_utils import config  # noqa: E402
from common_utils.actions_format_checker import MoveItem  # noqa: E402
from common_utils.common_utils import create_obstacle_info, load_extra_obstacles  # noqa: E402
from common_utils.custom_logger import CustomFormatter  # noqa: E402
from common_utils.graspgen_utils import GraspGeneratorUI  # noqa: E402
from PointCloud_Generation.PC_transform import (  # noqa: E402
    silent_transform_multiple_obj_with_name_dict,
)
from PointCloud_Generation.pointcloud_generation import PointCloudGenerator  # noqa: E402
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

V2_R095_LOG_DIR = Path(
    "/ssd1/CT_GraspGen/GraspGen_Results/logs/robotiq_2f_140_r095_ip_v2_abs_r095"
)


def parse_args():
    parser = argparse.ArgumentParser()
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
    parser.add_argument("--transform-config", type=str, default="sim2.json")
    parser.add_argument("--gripper_config", type=str, default=str(config.GRIPPER_CFG))
    parser.add_argument("--grasp_threshold", type=float, default=0.70)
    parser.add_argument("--num_grasps", type=int, default=200)
    parser.add_argument("--topk_num_grasps", type=int, default=5)
    parser.add_argument("--ip_config", type=str, default=str(V2_R095_LOG_DIR / "config.yaml"))
    parser.add_argument("--ip_ckpt", type=str, default=str(V2_R095_LOG_DIR / "last.pth"))
    parser.add_argument("--gravity", type=str, default="0,0,-1")
    parser.add_argument("--target", type=str, default="green cup")
    parser.add_argument("--qualifier", type=str, default="cup_qualifier")
    return parser.parse_args()


def ensure_transform_config(name: str) -> None:
    path = TRANSFORM_DIR / name
    if path.exists():
        return
    logger.warning(f"{path} not found; writing identity transform")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"tx": 0.0, "ty": 0.0, "tz": 0.0, "rr": 0.0, "rp": 0.0, "ry": 0.0}, f, indent=2)


def main():
    args = parse_args()
    torch.manual_seed(42)
    np.random.seed(42)
    ensure_transform_config(args.transform_config)
    gravity_local = tuple(float(x) for x in args.gravity.split(","))

    # 1. Same scene generation as local_graspgen_ip.py
    pc_generator = PointCloudGenerator(args)
    try:
        scene_data = pc_generator.generate_pointcloud(
            target_names=[args.target], blockages=[], valid_region=None,
        )
    finally:
        try:
            pc_generator.close()
        except AttributeError:
            pass

    scene_data = silent_transform_multiple_obj_with_name_dict(scene_data, args.transform_config)
    extra_obstacles = load_extra_obstacles()
    scene_data = create_obstacle_info(scene_data, extra_obstacles)

    # 2. Construct GraspGeneratorUI in IP-Adapter mode (THE INTEGRATION PATH)
    logger.warning(f"Building GraspGeneratorUI with ip_ckpt={args.ip_ckpt}")
    grasp_ui = GraspGeneratorUI(
        gripper_config=args.gripper_config,   # ignored when ip_config is set
        grasp_threshold=args.grasp_threshold,
        num_grasps=args.num_grasps,
        topk_num_grasps=args.topk_num_grasps,
        need_GUI=False,
        ip_config=args.ip_config,
        ip_ckpt=args.ip_ckpt,
        gravity_local=gravity_local,
    )
    assert grasp_ui.use_ip_adapter, "Integration not in IP-Adapter mode!"
    logger.warning(f"Sampler type: {type(grasp_ui.grasp_sampler).__name__}")
    logger.warning(f"Gripper:      {grasp_ui.gripper_name}")

    # 3. Drive _generate_grasps directly (bypass _generate_grasp_silent's
    # qualifier loop so we see raw model output, matching local_graspgen_ip.py)
    grasp_ui.scene_data = scene_data
    grasp_ui.move = MoveItem(
        target_name=args.target,
        qualifier=args.qualifier,
        move_type="grab_and_pour_and_place_back_curobo",
        args=[[0.0, 0.5, 0.5]],
    )
    all_grasps, custom_mask, collision_mask = grasp_ui._generate_grasps()

    logger.warning(
        f"Got {len(all_grasps)} grasps  | "
        f"custom_filter pass: {custom_mask.sum()}/{len(all_grasps)}  | "
        f"collision_free: {collision_mask.sum()}/{len(all_grasps)}"
    )

    # 4. Visualize ALL grasps (not just qualified) to match smoke-script output
    vis = create_visualizer()
    web_url = getattr(vis, "url", lambda: None)() or "http://127.0.0.1:7000/static/"
    print("\n" + "=" * 60)
    print(f"Open in browser:  {web_url}")
    print("Compare with the meshcat output of scripts/local_graspgen_ip.py.")
    print("Grasp distributions should cluster on the same object regions.")
    print("=" * 60 + "\n")

    scene_pc = np.array(scene_data["scene_info"]["pc_color"])[0]
    scene_color = np.array(scene_data["scene_info"]["img_color"]).reshape(1, -1, 3)[0]
    visualize_pointcloud(vis, "scene", scene_pc, scene_color, size=0.0025)
    obj_pc = np.asarray(scene_data["object_infos"][args.target]["points"])
    obj_color = np.array(scene_data["object_infos"][args.target]["colors"])
    visualize_pointcloud(vis, "object", obj_pc, obj_color, size=0.004)

    # Cyan to distinguish from the smoke script's purple
    for i, g in enumerate(all_grasps):
        visualize_grasp(vis, f"grasps/{i:03d}", g, color=[0, 200, 200],
                        gripper_name=grasp_ui.gripper_name, linewidth=1.5)

    print(f"Visualized {len(all_grasps)} grasps via GraspGeneratorUI integration path (cyan).")
    input("Press Enter to exit ...")


if __name__ == "__main__":
    main()
