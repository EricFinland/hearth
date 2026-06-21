# admin.nix: the human admin account, shared by every host.
#
# This is separate from the `hearth` service user (modules/base.nix), which runs
# agents with reduced privilege. `operator` is who you log in as to run
# `sudo nixos-rebuild switch`. SSH keys come from hearth.adminKeys, which each
# host sets to the operator's real public key(s).
{ config, lib, ... }:
{
  options.hearth.adminKeys = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    description = ''
      SSH public keys allowed to log in as the `operator` admin account.
      SSH password authentication is disabled (see modules/base.nix), so without
      a key here you can only reach the box through the local console.
      Example:
        hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
    '';
  };

  config = {
    # This is a single-operator homelab box managed remotely over SSH. Let wheel
    # sudo without a password so remote `nixos-rebuild` works without a TTY.
    # Tighten this (set true) if the box ever has multiple users or faces less
    # trusted access.
    security.sudo.wheelNeedsPassword = false;

    users.users.operator = {
      isNormalUser = true;
      extraGroups = [ "wheel" "networkmanager" ];
      openssh.authorizedKeys.keys = config.hearth.adminKeys;
      # Console fallback for a local login. Change it on first boot with
      # `passwd`. SSH itself still requires a key.
      initialPassword = "hearth";
    };
  };
}
