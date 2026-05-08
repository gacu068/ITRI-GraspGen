# Claude Code briefing — IP-Adapter integration (CT_adapter branch)

> If you're an AI assistant on the `CT_adapter` branch, **read this first**.
> Companion to `CLAUDE.md` (the original Pxter7777 project rules — still apply)
> and `LAB_DEPLOYMENT.md` (operator-facing deployment recipe).
> When in doubt, the user is a robotics researcher (not the original ITRI
> author) deploying their own fine-tuned model on top of someone else's
> production pipeline.

## Why this branch exists

The user trained a **Channel-Selective IP-Adapter** on top of NVlabs GraspGen,
conditioning grasp generation on **12D physical features = 9D PCA + 3D
gravity_local**, with Robotiq 2F-140 (retreat=0.0937 fix). The flagship
checkpoint is `v2_abs_r095/last.pth` (referred to as **v2_r095**).

This branch (CT_adapter) integrates that IP-Adapter into ITRI-GraspGen's
production grasp pipeline so it can run on a real TM5s arm. The base ITRI
project on `main` is unaware of IP-Adapter and stays untouched —
CT_adapter layers on top.

## Where things live

| Component | Path / Repo | Notes |
|---|---|---|
| ITRI integration glue | this repo (CT_adapter) | argparse + dispatch in `GraspGeneratorUI` |
| GraspGen fork (IP-Adapter source) | `~/GraspGen-IP` (clone of `gacu068/GraspGen`) | sibling, not replacing the submodule |
| Upstream NVlabs submodule | `Third_Party/GraspGen/` | unchanged; **do NOT modify** |
| v2_r095 weights | `~/graspgen_v2_r095/{config.yaml,last.pth}` | fetched from HF `ctshen068/graspgen-v2-r095` |
| Setup glue | `scripts/setup_ip_adapter.sh` | rewires editable-finder, idempotent |
| Lab calibration | `PointCloud_Generation/transform_config/sim2.json` | **machine-specific, not in git, do NOT overwrite** |

## How `import grasp_gen` resolves to the fork

`uv` installed grasp_gen as an editable package with a hardcoded path
mapping in `.venv/lib/python*/site-packages/__editable___grasp_gen_*_finder.py`.
`scripts/setup_ip_adapter.sh` rewrites `MAPPING` and `NAMESPACES` in that file
to point at `~/GraspGen-IP/grasp_gen` instead of `Third_Party/GraspGen/grasp_gen`.
**Any `uv sync` run (incl. implicit ones from `uv add`/sometimes `uv run`)
resets the file → re-run setup script.** Verify with `--check`.

## Integration entry point

`common_utils/graspgen_utils.py:GraspGeneratorUI` dispatches:
- **Vanilla path** (`ip_config=None`): unchanged, uses `GraspGenSampler.run_inference`
- **IP-Adapter path** (`ip_config + ip_ckpt set`): loads training config.yaml via
  OmegaConf, lazy-imports `GraspGenSamplerIP` from
  `~/GraspGen-IP/scripts/demo_object_mesh_ip.py` (sys.path hack), calls
  `sampler.sample(obj_pc, physical_features)`

`compute_physical_features(obj_pc, gravity_local)` in the same file produces
the 12D feature **using unit eigenvectors** (matches v2_r095 training on
`dataset_full`). Don't switch to eigval-scaled without checking which dataset
the active checkpoint was trained on.

CLI flags (`--ip_config`, `--ip_ckpt`, `--force_no_ip`, `--gravity`) are wired
through both `scripts/workflow_with_isaacsim.py` and `scripts/workflow_with_gui.py`.

## Critical rules (in addition to CLAUDE.md)

1. **Don't merge into main.** CT_adapter is the user's deployment branch;
   merging would force them through PR review with the upstream author.
2. **Don't `--no_ip_adapter` with v2_r095 ckpt to "compare baselines".**
   The prediction_head/ffn were unfrozen during IP-Adapter training, so the
   "base model" portion of v2_r095 ckpt is not the official base. Use
   `--base_model` flag in `local_graspgen_ip.py` (loads NVlabs official ckpt).
3. **Don't modify `Third_Party/GraspGen/`.** That's the upstream submodule.
   Modifications belong in the fork at `~/GraspGen-IP`.
4. **Don't overwrite `transform_config/sim2.json`** unless asked. It's the
   lab's camera-to-robot extrinsic, not regenerable from this checkout.
5. **Don't `uv sync` without warning the user.** It will reset the
   editable-finder redirect and break IP-Adapter imports until
   `scripts/setup_ip_adapter.sh` is re-run.
6. **Don't "consolidate" the duplicate `compute_physical_features` between
   `common_utils/graspgen_utils.py` and `scripts/local_graspgen_ip.py`.**
   The smoke script is intentionally standalone for debugging without the
   integration.
7. **Don't simplify or change the pyzed stub** at the top of
   `scripts/local_graspgen_*.py`. It's load-bearing — the local machine
   typically lacks ZED SDK 5.0, so the smoke scripts mock pyzed for
   `--use-png` mode only. Real ZED capture path will fail → that's intended.

## When something looks broken

In order of likelihood:

1. **`ImportError: No module named 'grasp_gen.models.ip_adapter'`**
   → editable-finder reset by `uv sync`. Run:
   ```bash
   bash scripts/setup_ip_adapter.sh --check
   ```
   If `Current grasp_gen root` doesn't end at `~/GraspGen-IP`, re-run
   `setup_ip_adapter.sh --graspgen-fork ~/GraspGen-IP`.

2. **`pyzed.sl: undefined symbol`** (real workflow, not smoke)
   → Lab ZED SDK isn't 5.0. Cannot fix from code. Either upgrade SDK or
   pin a different `pyzed` wheel in `pyproject.toml` (last resort, breaks
   `uv.lock`).

3. **`UnicodeDecodeError` while loading gripper YAML** (during
   `GraspGenSamplerIP` init / first IP-Adapter call; observed at lab on
   2026-05-07)
   → Shell locale isn't UTF-8 (`LANG=C`/`POSIX`) AND the fork's
   `grasp_gen/robot.py:load_gripper_yaml_file` opens YAML without
   `encoding=`. v2_r095's `robotiq_2f_140_r095.yaml` contains `→` and
   other non-ASCII chars. Two-layer fix already shipped:
     - `scripts/setup_ip_adapter.sh` exports `PYTHONUTF8=1` in the generated
       `setup_ip_adapter.env`
     - `common_utils/graspgen_utils.py:_patch_grasp_gen_yaml_utf8()`
       monkey-patches the loader before `GraspGenSamplerIP` is imported
   If it still triggers, the user skipped `source scripts/setup_ip_adapter.env`,
   or invoked `GraspGenSamplerIP` outside `GraspGeneratorUI` (which is
   what calls the monkey-patch). Quick workaround: prefix command with
   `PYTHONUTF8=1`. Do NOT "fix" by editing the fork's `robot.py` — the
   monkey-patch is intentional so the fork can stay in sync with upstream.

4. **IP grasps cluster nonsensically / collide constantly**
   → Likely gravity/PCA OOD. Check:
     - `transform_config/sim2.json` produces Z-up world frame
     - Default `--gravity 0,0,-1` matches lab's world convention
     - Active checkpoint's training dataset (config.yaml `grasp_root_dir`):
       `dataset_full` → unit PCA, `dataset_full_length` → eigval-scaled.
       `compute_physical_features` currently outputs unit.

5. **`workflow_with_isaacsim.py` retries forever, "Failed" from cuRobo**
   → IP-Adapter grasps survive `cup_qualifier` filter but fail cuRobo
   collision check. Three suspects:
     - `cup_qualifier` is hard-coded for Z-up upright cups; if the lab
       uses non-cup objects, swap qualifier in the action JSON.
       (Lab branch as of 2026-05-07 already short-circuits this with
       `return True` — that's intentional, not a bug to revert.)
     - `flip_upside_down_grasps` (graspgen_utils.py) flips grasps with
       `up_z<0`; for objects IP-Adapter intentionally grasps from below
       this fights the model
     - cuRobo collision against scene PC + table; IP grasps may approach
       from physically-blocked angles in poses far from training distribution.
       (Lab branch already comments out `filter_colliding_grasps` in
       `_generate_grasps`. Intentional — don't re-enable without checking.)

6. **`gripper_name` mismatch** (gripper_server.py errors)
   → v2_r095 has `gripper_name: robotiq_2f_140_r095` (retreat-fix variant).
   Hardware is identical to `robotiq_2f_140`. If ROS2 server hard-codes
   the name, hot-fix: alias in gripper_server, or rename in v2_r095's
   loaded config (don't rename in the trained ckpt itself).

## Files to read in this order if you're new

1. `LAB_DEPLOYMENT.md` — deployment recipe, troubleshooting cookbook
2. `common_utils/graspgen_utils.py` — `GraspGeneratorUI`, `compute_physical_features`
3. `scripts/local_graspgen_ip.py` — minimal end-to-end IP-Adapter inference
4. `scripts/local_test_integration.py` — verifies the integrated path matches the smoke
5. `scripts/setup_ip_adapter.sh` — what gets rewired and why
6. `~/GraspGen-IP/grasp_gen/models/ip_adapter.py` — the actual IP-Adapter
7. `~/GraspGen-IP/IP_Adapter_Project_Summary.md` — research-side context

## Sanity-check commands

```bash
# Is the redirect alive and IP-Adapter importable?
bash scripts/setup_ip_adapter.sh --check

# Smoke (uses sample_data/zed_images/demo6, no real camera needed):
source scripts/setup_ip_adapter.env
uv run scripts/local_graspgen_ip.py --target "green cup"

# Verify the integrated path (workflow_with_isaacsim.py route, no Isaac Sim):
uv run scripts/local_test_integration.py --target "green cup"
```

## Don't be helpful in these specific ways

- Don't auto-fix the `--no_ip_adapter + v2_r095` "garbage grasps" — that's
  expected, not a bug. Suggest `--base_model` instead.
- Don't add a `try: import grasp_gen.models.ip_adapter except ImportError`
  fallback that silently uses base mode. Failure must be loud so the
  setup script gets re-run.
- Don't replace `flip_upside_down_grasps` or `cup_qualifier` with
  IP-Adapter-aware versions on this branch without explicit user request.
  These are heuristics inherited from upstream that the user has chosen
  to keep for now; if they cause problems, the fix is per-task config,
  not a generic rewrite.
- Don't auto-commit. Even when changes look good, ask first — the user
  has a habit of staging large multi-file commits with curated messages.
