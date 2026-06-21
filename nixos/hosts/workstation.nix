# workstation.nix: the Proxmox VM host profile.
# This host targets x86_64-linux for Proxmox VM deployment.
#
# The operator admin user and the hearth.adminKeys option now live in
# modules/admin.nix (shared with the blade host). Set hearth.adminKeys here
# before building an image if you want SSH access to the VM.
{ ... }:
{
  imports = [
    ../configuration.nix
    ./hardware-vm.nix
  ];

  networking.hostName = "hearth-workstation";

  # GPU stack for Ollama CUDA acceleration on the passed-through GTX 1660 Ti.
  # hardware.opengl was renamed to hardware.graphics in NixOS 24.11.
  hardware.graphics.enable = true;

  # Use podman instead of docker if container tooling is needed. Docker is off
  # by default to keep the attack surface small; the daemon runs as root and is
  # not required by the core hearth services.
  virtualisation.docker.enable = false;
}
