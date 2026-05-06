#!/usr/bin/env bash
# Idempotent setup for IP-Adapter integration on top of an existing
# ITRI-GraspGen checkout. Bridges 'uv sync done' -> 'workflow_with_isaacsim.py
# can run with --ip_config / --ip_ckpt'.
#
# What it does (forward mode, default):
#   1. Resolve where the user's GraspGen fork lives (--graspgen-fork PATH)
#   2. Optionally download v2_r095 weights from HuggingFace
#   3. Rewrite .venv/.../__editable___grasp_gen_*_finder.py so 'import grasp_gen'
#      resolves to the fork (which has the IP-Adapter implementation), instead
#      of Third_Party/GraspGen/grasp_gen (NVlabs upstream submodule, no IP).
#   4. Write scripts/setup_ip_adapter.env so workflow callers can source default
#      paths instead of typing them.
#   5. Sanity check: python imports patch_graspgen from the fork.
#
# What it does NOT do:
#   - uv sync, submodule init, model download, ZED SDK install, Isaac Sim setup
#   - Touch the upstream submodule contents (Third_Party/GraspGen/ stays untouched)
#   - Touch transform_config/sim2.json (lab calibration must be set separately)
#
# Revert mode (--revert): point the editable finder back at the submodule. Use
# this when checking out main / Pxter7777 code without IP-Adapter integration.
#
# Re-run anytime. uv sync resets the editable finder, so just run this again.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMODULE_GRASP_GEN="$PROJECT_ROOT/Third_Party/GraspGen/grasp_gen"
ENV_FILE="$PROJECT_ROOT/scripts/setup_ip_adapter.env"
DEFAULT_HF_REPO="ctshen068/graspgen-v2-r095"
DEFAULT_IP_WEIGHTS="$PROJECT_ROOT/graspgen_v2_r095"

GRASPGEN_FORK=""
IP_WEIGHTS_DIR=""
DOWNLOAD_WEIGHTS=false
HF_REPO="$DEFAULT_HF_REPO"
REVERT=false
CHECK_ONLY=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

OPTIONS:
  --graspgen-fork PATH     Absolute path to your GraspGen fork (the one with
                           grasp_gen/models/ip_adapter.py). Required unless --revert.
  --ip-weights PATH        Dir containing config.yaml + last.pth.
                           Default: $DEFAULT_IP_WEIGHTS
  --download-weights       Download HF_REPO into --ip-weights before setup.
  --hf-repo REPO           HF repo for weight download.
                           Default: $DEFAULT_HF_REPO
  --revert                 Restore editable finder to point at the submodule
                           (use when switching to a non-IP branch).
  --check                  Sanity check only; do not modify any files.
  -h, --help               Show this message.

Examples:
  # Initial lab setup, downloading weights from HF:
  $(basename "$0") --graspgen-fork ~/GraspGen-IP --download-weights

  # Re-apply after 'uv sync' resets the redirect:
  $(basename "$0") --graspgen-fork ~/GraspGen-IP

  # Switching back to upstream main:
  $(basename "$0") --revert
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --graspgen-fork) GRASPGEN_FORK="$2"; shift 2 ;;
        --ip-weights)    IP_WEIGHTS_DIR="$2"; shift 2 ;;
        --download-weights) DOWNLOAD_WEIGHTS=true; shift ;;
        --hf-repo)       HF_REPO="$2"; shift 2 ;;
        --revert)        REVERT=true; shift ;;
        --check)         CHECK_ONLY=true; shift ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
    esac
done

IP_WEIGHTS_DIR="${IP_WEIGHTS_DIR:-$DEFAULT_IP_WEIGHTS}"

# ── Helpers ──────────────────────────────────────────────────────────────────

log() { printf '\033[1;36m[setup-ip]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup-ip]\033[0m \033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[setup-ip]\033[0m \033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

find_finder_file() {
    local matches
    matches=("$PROJECT_ROOT"/.venv/lib/python*/site-packages/__editable___grasp_gen_*_finder.py)
    if [[ ! -f "${matches[0]}" ]]; then
        die "uv editable finder not found under .venv/. Did you run 'uv sync'?"
    fi
    if [[ ${#matches[@]} -gt 1 ]]; then
        warn "Multiple finder files detected; using ${matches[0]}"
    fi
    printf '%s\n' "${matches[0]}"
}

# Print the directory the finder currently maps grasp_gen → to.
# Returns the path WITHOUT the trailing /grasp_gen.
get_current_root() {
    local finder="$1"
    "$PROJECT_ROOT/.venv/bin/python" - "$finder" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
m = re.search(r"'grasp_gen':\s*'([^']+?)/grasp_gen'", text)
print(m.group(1) if m else "")
PY
}

set_root() {
    local finder="$1" new_root="$2"
    "$PROJECT_ROOT/.venv/bin/python" - "$finder" "$new_root" <<'PY'
import re, sys
from pathlib import Path
finder, new_root = sys.argv[1], sys.argv[2]
text = Path(finder).read_text()
m = re.search(r"'grasp_gen':\s*'([^']+?)/grasp_gen'", text)
if not m:
    sys.exit("Couldn't parse current grasp_gen root from finder")
old_root = m.group(1)
if old_root == new_root:
    sys.exit(0)  # already redirected
new_text = text.replace(old_root, new_root)
Path(finder).write_text(new_text)
print(f"Redirected: {old_root} -> {new_root}")
PY
}

sanity_check_imports() {
    "$PROJECT_ROOT/.venv/bin/python" - <<'PY'
import sys
try:
    import grasp_gen
    from grasp_gen.models.ip_adapter import patch_graspgen, GraspConditionEncoder
except ImportError as e:
    sys.exit(f"FAIL: {e}")
print(f"OK: grasp_gen at {grasp_gen.__file__}")
print("OK: patch_graspgen importable from grasp_gen.models.ip_adapter")
PY
}

download_weights() {
    local target="$1" repo="$2"
    log "Downloading weights from HF '$repo' -> '$target'"
    mkdir -p "$target"
    "$PROJECT_ROOT/.venv/bin/python" - "$repo" "$target" <<'PY'
import sys
from huggingface_hub import snapshot_download
repo, target = sys.argv[1], sys.argv[2]
snapshot_download(repo_id=repo, repo_type="model", local_dir=target,
                  allow_patterns=["config.yaml", "last.pth"])
print(f"Downloaded to {target}")
PY
}

write_env_file() {
    local fork="$1" weights_dir="$2"
    cat > "$ENV_FILE" <<EOF
# Auto-generated by scripts/setup_ip_adapter.sh — re-source after re-running setup.
# Suggested usage:
#   source scripts/setup_ip_adapter.env
#   uv run scripts/workflow_with_isaacsim.py --no-confirm \\
#       --ip_config "\$IP_ADAPTER_CONFIG" --ip_ckpt "\$IP_ADAPTER_CKPT"
export GRASPGEN_FORK_DIR="$fork"
export IP_ADAPTER_WEIGHTS_DIR="$weights_dir"
export IP_ADAPTER_CONFIG="$weights_dir/config.yaml"
export IP_ADAPTER_CKPT="$weights_dir/last.pth"
EOF
    log "Wrote $ENV_FILE"
}

# ── Main ─────────────────────────────────────────────────────────────────────

FINDER_FILE="$(find_finder_file)"
log "Finder file: $FINDER_FILE"

if $REVERT; then
    log "Reverting editable-finder redirect to submodule path"
    [[ -d "$SUBMODULE_GRASP_GEN" ]] || die "Submodule grasp_gen not found at $SUBMODULE_GRASP_GEN"
    set_root "$FINDER_FILE" "$(dirname "$SUBMODULE_GRASP_GEN")"
    log "Reverted. 'import grasp_gen' now resolves to the upstream submodule."
    log "(IP-Adapter imports will fail in this state — that's expected for non-IP branches.)"
    exit 0
fi

if $CHECK_ONLY; then
    current="$(get_current_root "$FINDER_FILE")"
    log "Current grasp_gen root: $current"
    sanity_check_imports || die "Import check failed."
    log "Check passed."
    exit 0
fi

# Forward mode requires --graspgen-fork
[[ -n "$GRASPGEN_FORK" ]] || die "--graspgen-fork is required (or pass --revert / --check)."
GRASPGEN_FORK="$(cd "$GRASPGEN_FORK" && pwd)"  # absolutize

[[ -f "$GRASPGEN_FORK/grasp_gen/models/ip_adapter.py" ]] || die \
    "$GRASPGEN_FORK/grasp_gen/models/ip_adapter.py not found. Is --graspgen-fork pointing at the fork (with IP-Adapter)?"

# Optional weight download
if $DOWNLOAD_WEIGHTS; then
    download_weights "$IP_WEIGHTS_DIR" "$HF_REPO"
fi

# Verify weights present (whether we downloaded or expected pre-existing)
[[ -f "$IP_WEIGHTS_DIR/config.yaml" ]] || die \
    "$IP_WEIGHTS_DIR/config.yaml not found. Run with --download-weights or place files manually."
[[ -f "$IP_WEIGHTS_DIR/last.pth" ]] || die \
    "$IP_WEIGHTS_DIR/last.pth not found. Run with --download-weights or place files manually."

# Editable finder redirect
log "Redirecting grasp_gen import to $GRASPGEN_FORK"
set_root "$FINDER_FILE" "$GRASPGEN_FORK"

# Env file for workflow callers
write_env_file "$GRASPGEN_FORK" "$IP_WEIGHTS_DIR"

# Sanity check
log "Sanity check: importing IP-Adapter"
sanity_check_imports || die "Import failed after redirect."

log "Done."
echo ""
echo "Next steps:"
echo "  source scripts/setup_ip_adapter.env"
echo "  uv run scripts/workflow_with_isaacsim.py --no-confirm \\"
echo "      --ip_config \"\$IP_ADAPTER_CONFIG\" --ip_ckpt \"\$IP_ADAPTER_CKPT\""
echo ""
echo "If transform_config/sim2.json is missing or stale, set up your lab calibration"
echo "extrinsics there before running on real arm."
