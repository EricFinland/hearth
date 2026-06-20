#!/usr/bin/env bash
# build-image.sh: build a Proxmox-compatible qcow2 image from the flake.
set -euo pipefail

# Require nix.
if ! command -v nix >/dev/null 2>&1; then
  echo "error: nix is not installed or not on PATH." >&2
  echo "Install Nix (https://nixos.org/download) and enable flakes." >&2
  exit 1
fi

OUT="result-image"

echo "Building qcow2 image for .#workstation ..."
nix run github:nix-community/nixos-generators -- \
  --format qcow \
  --flake .#workstation \
  -o "${OUT}"

echo "Done."
echo "Image output: ${OUT}"
# Boot the resulting image in Proxmox by importing result-image/nixos.qcow2 as a new disk.
echo "Next: import ${OUT}/nixos.qcow2 into Proxmox as a new VM disk."
