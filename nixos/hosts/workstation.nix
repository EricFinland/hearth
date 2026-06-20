# workstation.nix: the concrete host profile deployed to a Proxmox VM.
# This host targets x86_64-linux for Proxmox VM deployment.
{ config, lib, ... }:
{
  imports = [
    ../configuration.nix
    ./hardware-vm.nix
  ];

  options.hearth.adminKeys = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    description = ''
      SSH public keys allowed to log in as the `operator` admin account.
      Add your key here BEFORE building an image. SSH password authentication is
      disabled (see modules/base.nix), so without a key you cannot ssh in.
      Example:
        hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
    '';
  };

  config = {
    networking.hostName = "hearth-workstation";

    # Human admin account, separate from the locked-down `hearth` service user.
    # `hearth` runs agents with reduced privilege; `operator` is who you log in
    # as to run `sudo nixos-rebuild switch`. Member of wheel for sudo.
    users.users.operator = {
      isNormalUser = true;
      extraGroups = [ "wheel" ];
      openssh.authorizedKeys.keys = config.hearth.adminKeys;
      # Console fallback for the Proxmox noVNC console only. Change it on first
      # boot with `passwd`. SSH itself still requires a key.
      initialPassword = "hearth";
    };

    # GPU stack for Ollama CUDA acceleration on the passed-through GTX 1660 Ti.
    # Note: `hardware.opengl` was renamed to `hardware.graphics` in NixOS 24.11,
    # so the original scaffold value would have failed to evaluate on unstable.
    hardware.graphics.enable = true;

    # Use podman instead of docker if container tooling is needed. Docker is off
    # by default to keep the attack surface small; the daemon runs as root and is
    # not required by the core hearth services.
    virtualisation.docker.enable = false;
  };
}
