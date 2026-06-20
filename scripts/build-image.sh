#!/usr/bin/env bash
# build-image.sh: build a Proxmox-ready qcow2 image from the flake.
set -euo pipefail

# Require nix.
if ! command -v nix >/dev/null 2>&1; then
  echo "error: nix is not installed or not on PATH." >&2
  echo "Install Nix (https://nixos.org/download) and enable flakes." >&2
  exit 1
fi

# TARGET selects which image to build:
#   image-minimal  LLM stack disabled. Build this first; it skips compiling CUDA.
#   image          full system (Ollama + CUDA). Large and slow on first build.
TARGET="${1:-image-minimal}"

echo "Building .#${TARGET} ..."
nix build ".#${TARGET}" -o result-image

echo "Done. Contents of result-image:"
ls -lh result-image/

# Boot the resulting image in Proxmox by importing the qcow2 as a new disk.
echo
echo "Next: import the .qcow2 in result-image/ into Proxmox as a new VM disk."
echo "Create the VM with BIOS = OVMF (UEFI), since the image uses systemd-boot."
