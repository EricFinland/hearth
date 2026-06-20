#!/usr/bin/env bash
# bootstrap.sh: apply this flake to an existing NixOS host.
#
# Usage:
#   bash scripts/bootstrap.sh [FLAKE_URL]
#
# FLAKE_URL defaults to the current directory ("."). It can also be a remote
# flake reference, for example github:YOUR_USERNAME/hearth.
set -euo pipefail

FLAKE_URL="${1:-.}"

if ! command -v nixos-rebuild >/dev/null 2>&1; then
  echo "error: nixos-rebuild not found. This script must run on a NixOS host." >&2
  exit 1
fi

echo "Applying ${FLAKE_URL}#workstation with nixos-rebuild switch ..."
sudo nixos-rebuild switch --flake "${FLAKE_URL}#workstation"

echo
echo "Switch complete."
echo
echo "First-time sops-nix key setup (only needed once per host):"
echo "  1. age-keygen -o ~/.config/sops/age/keys.txt"
echo "  2. Copy the printed public key into .sops.yaml creation rules."
echo "  3. Create secrets: sops secrets/example.yaml"
echo "  4. Reference them with sops.secrets.<name> and re-run this script."
echo "See docs/DECISIONS.md (ADR-003) for the full walkthrough."
