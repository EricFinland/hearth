#!/usr/bin/env bash
# deploy.sh: copy a built image to a Proxmox host and import it as a VM disk.
#
# Required environment variables:
#   PROXMOX_HOST   IP or hostname of the Proxmox node
#   PROXMOX_USER   SSH user on the Proxmox node (default: root)
#   PROXMOX_VMID   the VM ID whose disk should be replaced/imported
#
# Optional:
#   PROXMOX_STORAGE   target storage for the imported disk (default: local-lvm)
#   IMAGE_PATH        path to the qcow2 (default: result-image/nixos.qcow2)
set -euo pipefail

PROXMOX_USER="${PROXMOX_USER:-root}"
PROXMOX_STORAGE="${PROXMOX_STORAGE:-local-lvm}"
IMAGE_PATH="${IMAGE_PATH:-result-image/nixos.qcow2}"

# Validate required vars.
missing=0
for var in PROXMOX_HOST PROXMOX_VMID; do
  if [ -z "${!var:-}" ]; then
    echo "error: ${var} is not set." >&2
    missing=1
  fi
done
if [ "${missing}" -ne 0 ]; then
  echo "Set the required environment variables and re-run. See the header of this script." >&2
  exit 1
fi

if [ ! -f "${IMAGE_PATH}" ]; then
  echo "error: image not found at ${IMAGE_PATH}. Run scripts/build-image.sh first." >&2
  exit 1
fi

REMOTE_TMP="/tmp/hearth-nixos.qcow2"

# Dry-run summary.
echo "=== deploy plan ==="
echo "  source image : ${IMAGE_PATH}"
echo "  proxmox host : ${PROXMOX_USER}@${PROXMOX_HOST}"
echo "  remote tmp   : ${REMOTE_TMP}"
echo "  target VM ID : ${PROXMOX_VMID}"
echo "  storage      : ${PROXMOX_STORAGE}"
echo "==================="

# Copy the image up.
echo "Copying image to ${PROXMOX_USER}@${PROXMOX_HOST}:${REMOTE_TMP} ..."
scp "${IMAGE_PATH}" "${PROXMOX_USER}@${PROXMOX_HOST}:${REMOTE_TMP}"

# Import the disk into the target VM.
echo "Importing disk into VM ${PROXMOX_VMID} on ${PROXMOX_STORAGE} ..."
ssh "${PROXMOX_USER}@${PROXMOX_HOST}" \
  "qm importdisk ${PROXMOX_VMID} ${REMOTE_TMP} ${PROXMOX_STORAGE}"

echo "Done. Attach the imported disk to VM ${PROXMOX_VMID} in the Proxmox UI,"
echo "set it as the boot disk, and start the VM."
