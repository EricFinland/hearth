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
