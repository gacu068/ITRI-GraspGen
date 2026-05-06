# Lab Deployment Checklist — IP-Adapter Integration

End-to-end recipe for deploying the IP-Adapter (v2_r095) integration on a lab
machine that already has a working `Pxter7777/ITRI-GraspGen` checkout. Layered
on top, **does not disrupt** the existing main-branch setup.

> **Audience**: future-you, or a lab collaborator with shell access to the lab
> workstation (`lab@workstation:~/ITRI-GraspGen`).

---

## 0. Prerequisites (assumed already done)

The lab machine should already have:

- [ ] `~/ITRI-GraspGen` cloned with `origin = Pxter7777/ITRI-GraspGen` (their
      working checkout, on `main`)
- [ ] `git submodule update --init --recursive` done
- [ ] `uv sync` done (`.venv/` populated)
- [ ] `bash download_models.sh` done (`models/` populated)
- [ ] **ZED SDK 5.0** installed at `/usr/local/zed` (must match the
      `pyzed-5.0` wheel pinned in `pyproject.toml`; if older SDK, the workflow
      stack won't import). Check: `cat /usr/local/zed/include/sl/Camera.hpp | grep ZED_SDK_MAJOR`
- [ ] **Isaac Sim 4.5** installed and `omni_python` alias set
      (see `isaac-sim2real/README.md`)
- [ ] **cuRobo** installed against Isaac Sim Python
      (see `isaac-sim2real/README.md`)
- [ ] **ROS2 humble** + `tm_driver` (or willing to use
      `ROS2_server/dummy_gripper_server.py` first)
- [ ] **TM5s URDF / cuRobo configs** symlinked into `~/curobo/` (see
      `isaac-sim2real/README.md` step "Copy TM5S configs")
- [ ] **`transform_config/sim2.json`** — the camera→robot extrinsic for THIS
      lab setup. **Do NOT overwrite this file**; it's machine-specific. The
      IP-Adapter integration leaves it untouched.

If any of the above is missing, finish those before continuing.

---

## 1. Pre-flight check

Confirm the existing `main` workflow runs (sanity baseline):

```bash
cd ~/ITRI-GraspGen
git status                                  # should be clean, on main
git log --oneline -3                        # latest Pxter7777 commits

# Quick import smoke (no GPU work):
uv run --no-sync python -c "import grasp_gen; print(grasp_gen.__file__)"
# → should print Third_Party/GraspGen/grasp_gen/__init__.py
```

If imports break here, fix the existing setup before adding IP-Adapter.

---

## 2. Pull the IP-Adapter integration

Add the fork as a new remote, branch off it. `main` stays untouched.

```bash
cd ~/ITRI-GraspGen
git remote add gacu068 git@github.com:gacu068/ITRI-GraspGen.git
git fetch gacu068 CT_adapter
git checkout -b CT_adapter gacu068/CT_adapter
```

Verify:

```bash
git log --oneline | head -5
# → should include:
# ae69b8c chore: add setup_ip_adapter.sh for one-command lab deployment
# c5b6dde test: add local smoke scripts for GraspGen baseline + IP-Adapter
# 132d565 feat: integrate IP-Adapter sampler into GraspGeneratorUI
# 4a524f5 add claude.md (#62)               ← Pxter7777's last commit
```

Switching back to upstream is just `git checkout main`.

---

## 3. Clone the GraspGen fork

The GraspGen fork (with `models/ip_adapter.py`) lives outside ITRI-GraspGen,
so the upstream submodule at `Third_Party/GraspGen` stays unchanged.

```bash
git clone git@github.com:gacu068/GraspGen.git ~/GraspGen-IP
```

Quick verify it has IP-Adapter:

```bash
ls ~/GraspGen-IP/grasp_gen/models/ip_adapter.py
# → should exist
```

> Both `gacu068/GraspGen` and `gacu068/ITRI-GraspGen` are private; you'll
> need read access to `gacu068`'s repos. SSH key on the lab machine must be
> registered to a GitHub account with that access.

---

## 4. Get the v2_r095 weights from HuggingFace

The trained weights are on a private HF repo `ctshen068/graspgen-v2-r095`.

### 4a. HuggingFace auth (one-time)

```bash
uv run --no-sync huggingface-cli login
# Paste a Read or Write token from https://huggingface.co/settings/tokens
# Choose 'n' for git credential
```

Verify:

```bash
uv run --no-sync huggingface-cli whoami
# → should print your HF username (must have access to ctshen068/graspgen-v2-r095)
```

### 4b. Download (handled by setup script in §5)

You can do this in §5 with `--download-weights`, OR pre-download manually:

```bash
uv run --no-sync python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='ctshen068/graspgen-v2-r095', repo_type='model',
                  local_dir='~/graspgen_v2_r095',
                  allow_patterns=['config.yaml', 'last.pth'])
"
```

After this you should have:

```
~/graspgen_v2_r095/
  ├── config.yaml          (~5 KB)
  └── last.pth             (~268 MB)
```

---

## 5. One-command setup

The setup script wires everything together — editable-finder redirect, env
file, and (optionally) weight download.

```bash
cd ~/ITRI-GraspGen
bash scripts/setup_ip_adapter.sh \
    --graspgen-fork ~/GraspGen-IP \
    --ip-weights ~/graspgen_v2_r095 \
    --download-weights        # OMIT if you already pre-downloaded in §4b
```

Expected output ends with:

```
[setup-ip] Sanity check: importing IP-Adapter
OK: grasp_gen at /home/.../GraspGen-IP/grasp_gen/__init__.py
OK: patch_graspgen importable from grasp_gen.models.ip_adapter
[setup-ip] Done.
```

### What it actually changed

- `~/ITRI-GraspGen/.venv/lib/python*/site-packages/__editable___grasp_gen_*_finder.py`
  — `MAPPING` and `NAMESPACES` paths now point at `~/GraspGen-IP/grasp_gen`
- `~/ITRI-GraspGen/scripts/setup_ip_adapter.env` — auto-generated, exports
  `GRASPGEN_FORK_DIR`, `IP_ADAPTER_CONFIG`, `IP_ADAPTER_CKPT`

### What it did NOT touch

- `Third_Party/GraspGen/` (upstream submodule, unchanged)
- `transform_config/sim2.json` (lab calibration, unchanged)
- `models/` (existing model weights, unchanged)
- Any other `.venv/` content (only one editable-finder file)

### Re-running

`uv sync` resets the editable-finder file. After any future `uv sync`, just
re-run the setup script with the same args. Idempotent.

---

## 6. Verify the integration end-to-end

### 6a. Local-only smoke (no Isaac Sim, no arm, no socket)

```bash
source scripts/setup_ip_adapter.env
uv run scripts/local_graspgen_ip.py --target "green cup" --use-png demo6
```

Should print `Got N grasps`, open a meshcat URL, and show purple grasps
clustered on the cup. Press Enter to exit.

If this fails, **stop**. The integration isn't sound; debug before continuing.

### 6b. Three-process workflow (with dummy gripper, no real arm)

Three separate terminals:

```bash
# Terminal 1: dummy ROS2 server (no real arm needed)
cd ~/ITRI-GraspGen/ROS2_server && uv run dummy_gripper_server.py

# Terminal 2: Isaac Sim + cuRobo
cd ~/ITRI-GraspGen/isaac-sim2real && omni_python sync_with_ROS2.py

# Terminal 3: GraspGen workflow with IP-Adapter
cd ~/ITRI-GraspGen
source scripts/setup_ip_adapter.env
uv run scripts/workflow_with_isaacsim.py --no-confirm --use-png demo6 \
    --ip_config "$IP_ADAPTER_CONFIG" --ip_ckpt "$IP_ADAPTER_CKPT"
# At the prompt, type: Grasp_and_Dump
```

Expected: three processes coordinate via socket, Isaac Sim shows TM5s arm
moving through the planned trajectory.

### 6c. Real arm (only after 6b passes)

Same as 6b but replace dummy with the real driver:

```bash
# Terminal 0 (additional): TM driver
ros2 run tm_driver tm_driver robot_ip:=192.168.1.10

# Terminal 1: real gripper server
cd ~/ITRI-GraspGen/ROS2_server && /usr/bin/python3 gripper_server.py

# Terminals 2 & 3 same as above
```

⚠️ **Before pressing enter on the real arm**: confirm the workspace is
clear, e-stop is reachable, and you've reviewed the trajectory in the
Isaac Sim window first.

---

## 7. IP-Adapter-specific gotchas

### Gripper YAML — must match the trained model

v2_r095 expects `gripper_name = robotiq_2f_140_r095` (with retreat=0.0937).
The training `config.yaml` already encodes this. Just don't override
`--gripper_config` to the original `robotiq_2f_140.yml` (retreat=0.0237) —
the grasp depth would mismatch and the gripper would over-shoot by ~7cm.

### Gravity in lab frame

`compute_physical_features` (`common_utils/graspgen_utils.py`) uses
`gravity_local = (0, 0, -1)` by default, which is correct **only if** the
point cloud has been transformed to a Z-up world frame. ITRI's
`transform_config/sim2.json` should do exactly this (camera → robot world
where +Z is up). If your lab's calibration uses a different convention,
override on the workflow command:

```bash
uv run scripts/workflow_with_isaacsim.py ... --gravity "0,0,-1"
```

### PCA convention

v2_r095 was trained on `dataset_full` (unit eigenvectors). The
`compute_physical_features` helper computes unit eigvecs (no eigval scaling)
to match. If you ever swap to a model trained on `dataset_full_length`
(eigval-scaled), you must change the helper accordingly — feeding the wrong
PCA convention silently gives OOD conditioning and IP-Adapter behaves like
the base model.

### Custom filter and gravity

`common_utils/qualification.py:cup_qualifier` assumes Z-up world (`up[2] >
0.85`). If you skipped/changed `transform_config/sim2.json`, the qualifier
will reject most grasps. For local debugging without lab calibration, the
GUI control panel lets you toggle Custom Filter off.

---

## 8. Switching back to non-IP main

```bash
cd ~/ITRI-GraspGen
git checkout main                                   # back to Pxter7777 main
bash scripts/setup_ip_adapter.sh --revert           # editable-finder back to submodule
```

`--revert` won't delete `~/GraspGen-IP` or `~/graspgen_v2_r095`; it only
reverses the redirect so `import grasp_gen` resolves to the upstream
submodule. You can switch back to IP mode anytime with the same command
from §5.

---

## 9. Troubleshooting

### `ImportError: No module named 'grasp_gen.models.ip_adapter'`

Editable-finder isn't redirected. Check:

```bash
bash scripts/setup_ip_adapter.sh --check
```

If `Current grasp_gen root` doesn't end at `~/GraspGen-IP`, re-run the
forward setup (§5).

### `pyzed.sl: undefined symbol: ...`

ZED SDK version mismatch. The pinned wheel is `pyzed 5.0`. Lab must have
ZED SDK 5.0 installed. Newer or older SDK → fail. Either upgrade the SDK
or pin a different wheel in `pyproject.toml` (last resort).

### `[INFO] All checkpoint keys matched perfectly.` not shown / many warnings

Architecture mismatch between checkpoint and `cfg.diffusion`. Probably
loaded the wrong config — make sure `--ip_config` points at the v2_r095
training config (which encodes `obs_backbone: pointn4et`,
`gripper_name: robotiq_2f_140_r095`, `ip_*` hyperparams).

### Isaac Sim hangs at "Waiting for command"

Three-process socket order matters. Start ROS2 server first → Isaac Sim →
GraspGen workflow. Restart all three if a previous run left orphan
processes (`nvidia-smi` to spot zombies eating GPU).

### `gh: command not found` / no SSH access to private repos

The lab machine needs an SSH key registered to a GitHub account with read
access to `gacu068/*` repos, OR a Personal Access Token configured for
HTTPS. See `gh auth login` (selects SSH automatically if a key is present).

---

## 10. Reference paths summary

| Resource | Lab location | Source |
|---|---|---|
| ITRI-GraspGen integration | `~/ITRI-GraspGen` (CT_adapter branch) | `gacu068/ITRI-GraspGen` |
| GraspGen fork (with IP-Adapter) | `~/GraspGen-IP` | `gacu068/GraspGen` |
| v2_r095 weights | `~/graspgen_v2_r095/{config.yaml,last.pth}` | HF `ctshen068/graspgen-v2-r095` |
| Lab calibration | `~/ITRI-GraspGen/PointCloud_Generation/transform_config/sim2.json` | Lab-specific, NOT in git |
| Isaac Sim | `~/isaac-sim-4.5.0/` | NVIDIA Omniverse download |
| cuRobo | `~/curobo/` | `NVlabs/curobo` |
| TM5s configs | `~/curobo/src/curobo/content/.../{tm5s.yml,tm5s.urdf}` (symlinks) | `isaac-sim2real/tm5s.{yml,urdf}` |
