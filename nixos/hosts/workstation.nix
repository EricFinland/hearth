# workstation.nix: the concrete host profile deployed to a Proxmox VM.
# This host targets x86_64-linux for Proxmox VM deployment.
{ ... }:
{
  imports = [
    ../configuration.nix
  ];

  networking.hostName = "hearth-workstation";

  # GPU stack for Ollama CUDA acceleration on the passed-through GTX 1660 Ti.
  hardware.opengl.enable = true;

  # Use podman instead of docker if container tooling is needed. Docker is off
  # by default to keep the attack surface small; the daemon runs as root and is
  # not required by the core hearth services.
  virtualisation.docker.enable = false;
}
