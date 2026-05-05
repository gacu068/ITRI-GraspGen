"""
Local smoke test for IP-Adapter v2_r095 (or any IP-Adapter variant).

PointCloud -> GraspGenSamplerIP -> meshcat visualization.

Same scaffold as scripts/local_graspgen_only.py but uses the IP-Adapter
sampler from /ssd1/CT_GraspGen/GraspGen (the user's fork). Reads the
training config.yaml directly so all ip_* hyperparams come from there.

Usage:
    uv run scripts/local_graspgen_ip.py --target "green cup"
    uv run scripts/local_graspgen_ip.py --target "green cup" --no_ip_adapter

Note: this script assumes the editable finder at
.venv/lib/python3.11/site-packages/__editable___grasp_gen_1_0_0_finder.py
has been redirected to /ssd1/CT_GraspGen/GraspGen/grasp_gen so the IP-Adapter
code is reachable. `uv sync` will overwrite that redirect; re-apply with:
    sed -i 's|/ssd1/CT_GraspGen/ITRI-GraspGen/Third_Party/GraspGen/grasp_gen|/ssd1/CT_GraspGen/GraspGen/grasp_gen|g' \\
        .venv/lib/python3.11/site-packages/__editable___grasp_gen_1_0_0_finder.py
"""

import argparse
import json
import logging
import sys
import types
from pathlib import Path

import numpy as np


def _install_pyzed_stub() -> None:
    """See local_graspgen_only.py for rationale."""
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
from omegaconf import OmegaConf  # noqa: E402

from common_utils import config  # noqa: E402
from common_utils.common_utils import create_obstacle_info, load_extra_obstacles  # noqa: E402
from common_utils.custom_logger import CustomFormatter  # noqa: E402
from PointCloud_Generation.PC_transform import (  # noqa: E402
    silent_transform_multiple_obj_with_name_dict,
)
from PointCloud_Generation.pointcloud_generation import PointCloudGenerator  # noqa: E402

# IP-Adapter sampler from user's fork. Imported AFTER the editable-finder
# redirect is in place; otherwise this import would still come from the
# upstream submodule which has no IP-Adapter.
from grasp_gen.utils.meshcat_utils import (  # noqa: E402
    create_visualizer,
    visualize_grasp,
    visualize_pointcloud,
)

# scripts/demo_object_mesh_ip.py is in the fork, not on sys.path. Pull
# GraspGenSamplerIP via direct import from its file.
sys.path.insert(0, "/ssd1/CT_GraspGen/GraspGen/scripts")
from demo_object_mesh_ip import GraspGenSamplerIP  # noqa: E402

handler = logging.StreamHandler()
handler.setFormatter(CustomFormatter())
logging.basicConfig(level=logging.DEBUG, handlers=[handler], force=True)
logger = logging.getLogger(__name__)

PROJECT_ROOT_DIR = Path(__file__).resolve().parents[1]
TRANSFORM_DIR = PROJECT_ROOT_DIR / "PointCloud_Generation" / "transform_config"

# v2_r095 default paths
V2_R095_LOG_DIR = Path(
    "/ssd1/CT_GraspGen/GraspGen_Results/logs/robotiq_2f_140_r095_ip_v2_abs_r095"
)
V2_R095_CONFIG = V2_R095_LOG_DIR / "config.yaml"
V2_R095_CKPT = V2_R095_LOG_DIR / "last.pth"

# Base model paths (for --base_model A/B comparison)
BASE_MODEL_CONFIG = (
    PROJECT_ROOT_DIR / "models" / "GraspGenModels" / "checkpoints" / "graspgen_robotiq_2f_140.yml"
)
BASE_MODEL_CKPT = (
    PROJECT_ROOT_DIR / "models" / "GraspGenModels" / "checkpoints" / "graspgen_robotiq_2f_140_gen.pth"
)


def parse_args():
    parser = argparse.ArgumentParser()
    # PointCloudGenerator args
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
    # IP-Adapter args
    parser.add_argument(
        "--ip_config", type=str, default=str(V2_R095_CONFIG),
        help="Path to training config.yaml (e.g. v2_r095/config.yaml).",
    )
    parser.add_argument(
        "--ip_ckpt", type=str, default=str(V2_R095_CKPT),
        help="Path to IP-Adapter checkpoint .pth (e.g. v2_r095/last.pth).",
    )
    parser.add_argument(
        "--ip_scale", type=float, default=None,
        help="Override IP-Adapter alpha scale at inference (None = use trained value).",
    )
    parser.add_argument(
        "--no_ip_adapter", action="store_true",
        help="Skip IP-Adapter patching and run vanilla base model (for ablation). "
             "WARNING: pairing this with v2_r095 ckpt gives garbage — the prediction_head "
             "was unfrozen during IP-Adapter training, so it expects IP signal. Use "
             "--base_model for a proper baseline comparison.",
    )
    parser.add_argument(
        "--base_model", action="store_true",
        help="Shortcut: load NVlabs official robotiq_2f_140 base checkpoint with the "
             "matching gripper yml, force --no_ip_adapter. Overrides --ip_config / --ip_ckpt.",
    )
    parser.add_argument(
        "--gravity", type=str, default="0,0,-1",
        help="3D gravity direction in object/world frame, comma-separated. "
             "Default (0,0,-1) assumes Z is up. With identity transform "
             "(camera frame) this is physically wrong but lets the model run.",
    )
    parser.add_argument(
        "--num_grasps", type=int, default=200,
        help="Number of grasps to sample.",
    )
    parser.add_argument(
        "--target", type=str, default="green cup",
        help="GroundingDINO prompt for the target object.",
    )
    return parser.parse_args()


def ensure_transform_config(name: str) -> None:
    path = TRANSFORM_DIR / name
    if path.exists():
        return
    logger.warning(
        f"{path} not found; writing identity transform for local visualization."
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = {"tx": 0.0, "ty": 0.0, "tz": 0.0, "rr": 0.0, "rp": 0.0, "ry": 0.0}
    with open(path, "w") as f:
        json.dump(identity, f, indent=2)


def compute_physical_features(obj_pc: np.ndarray, gravity_local: np.ndarray) -> np.ndarray:
    """
    Compute 12D physical features matching v2_r095 training convention
    (dataset_full = unit eigenvectors).

    Layout: [pca_features (9), gravity_local (3)] where pca.reshape(3,3) has
    rows = unit eigenvectors sorted by eigenvalue descending.
    """
    centered = obj_pc - obj_pc.mean(axis=0)
    cov = np.cov(centered, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)  # eigvecs columns
    order = np.argsort(eigvals)[::-1]
    eigvecs_sorted = eigvecs[:, order]            # 3x3, columns are unit eigvecs
    pca_features = eigvecs_sorted.T.flatten()    # rows = eigvecs, then flatten
    physical_features = np.concatenate([pca_features, gravity_local]).astype(np.float32)
    assert physical_features.shape == (12,), physical_features.shape
    return physical_features


def main():
    args = parse_args()
    torch.manual_seed(42)
    np.random.seed(42)

    if args.base_model:
        args.ip_config = str(BASE_MODEL_CONFIG)
        args.ip_ckpt = str(BASE_MODEL_CKPT)
        args.no_ip_adapter = True
        logger.warning("[--base_model] Using NVlabs official robotiq_2f_140 base ckpt + vanilla model")

    ensure_transform_config(args.transform_config)
    gravity_local = np.array([float(x) for x in args.gravity.split(",")], dtype=np.float32)
    assert gravity_local.shape == (3,), f"--gravity must be 3 numbers, got {gravity_local}"

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
        try:
            pc_generator.close()
        except AttributeError as e:
            logger.debug(f"Skipping pc_generator.close() (use-png mode): {e}")

    # 2. Transform (identity for local)
    scene_data = silent_transform_multiple_obj_with_name_dict(
        scene_data, args.transform_config
    )
    extra_obstacles = load_extra_obstacles()
    scene_data = create_obstacle_info(scene_data, extra_obstacles)

    obj_pc = np.asarray(scene_data["object_infos"][args.target]["points"])
    logger.warning(f"Object {args.target!r}: {obj_pc.shape[0]} points")

    # 3. Build GraspGenSamplerIP (loads v2_r095 config + ckpt)
    logger.info(f"Loading IP-Adapter config: {args.ip_config}")
    grasp_cfg = OmegaConf.load(args.ip_config)
    OmegaConf.set_struct(grasp_cfg, False)
    grasp_cfg.eval.checkpoint = args.ip_ckpt
    if args.ip_scale is not None:
        grasp_cfg.diffusion.ip_scale = args.ip_scale
    gripper_name = grasp_cfg.data.gripper_name
    logger.warning(f"Gripper: {gripper_name}, ckpt: {args.ip_ckpt}")

    sampler = GraspGenSamplerIP(grasp_cfg, force_no_ip=args.no_ip_adapter)

    # 4. Physical features
    if args.no_ip_adapter:
        physical_features = np.zeros(12, dtype=np.float32)  # ignored anyway
        logger.warning("--no_ip_adapter: physical_features will be ignored")
    else:
        physical_features = compute_physical_features(obj_pc, gravity_local)
        pca_axes = physical_features[:9].reshape(3, 3)
        logger.warning(
            f"physical_features[:9] (PCA axes, rows = eigvecs):\n{np.array_str(pca_axes, precision=3)}"
        )
        logger.warning(f"physical_features[9:] (gravity): {physical_features[9:]}")

    # 5. Inference
    logger.info(f"Running GraspGen IP inference ({args.num_grasps} grasps) ...")
    grasps_t, conf_t = sampler.sample(
        obj_pc,
        physical_features,
        threshold=-1.0,
        num_grasps=args.num_grasps,
        remove_outliers=False,
    )
    if len(grasps_t) == 0:
        logger.error("No grasps returned!")
        return
    grasps = grasps_t.cpu().numpy() if hasattr(grasps_t, "cpu") else np.asarray(grasps_t)
    grasps[:, 3, 3] = 1.0
    logger.warning(f"Got {len(grasps)} grasps")

    # 6. Headless meshcat visualization
    vis = create_visualizer()
    web_url = getattr(vis, "url", lambda: None)() or "http://127.0.0.1:7000/static/"
    print("\n" + "=" * 60)
    print(f"Open this URL in your browser:\n  {web_url}")
    print("If running over SSH:  ssh -L 7000:127.0.0.1:7000 <host>")
    print("=" * 60 + "\n")

    scene_pc = np.array(scene_data["scene_info"]["pc_color"])[0]
    scene_color = np.array(scene_data["scene_info"]["img_color"]).reshape(1, -1, 3)[0]
    visualize_pointcloud(vis, "scene", scene_pc, scene_color, size=0.0025)

    obj_color = np.array(scene_data["object_infos"][args.target]["colors"])
    visualize_pointcloud(vis, "object", obj_pc, obj_color, size=0.004)

    color = [200, 0, 200] if not args.no_ip_adapter else [0, 200, 0]
    for i, g in enumerate(grasps):
        visualize_grasp(vis, f"grasps/{i:03d}", g, color=color,
                        gripper_name=gripper_name, linewidth=1.5)

    print(f"Visualized {len(grasps)} grasps in meshcat (gripper={gripper_name}).")
    if args.base_model:
        mode_str = "BASE MODEL (vanilla, official ckpt)"
    elif args.no_ip_adapter:
        mode_str = f"NO_IP_ADAPTER on ckpt={Path(args.ip_ckpt).name} (likely garbage if ckpt was IP-trained)"
    else:
        mode_str = f"IP-Adapter ON, ckpt={Path(args.ip_ckpt).name}"
    print(f"Mode: {mode_str}")
    input("Press Enter to exit ...")


if __name__ == "__main__":
    main()
