# tripwire.nix: system-level honeyfile decoys.
#
# Per-run decoys are planted into each agent workspace by the agent loop
# (agent/hearth_tools.py plant_decoys). This module plants a few decoys OUTSIDE
# any workspace so a full-machine agent that goes looking for secrets on the box
# finds convincing bait first. Reading one surfaces a canary token; the agent
# loop's output scan (layer 2) trips on it, or, for a raw shell open that never
# passes through a tool, the auditd layer (v2.0) catches it.
#
# The decoys are generated once by a oneshot with random canary tokens, so the
# tokens are not baked into the world-readable Nix store.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.tripwire;
  plant = pkgs.writeShellScript "hearth-plant-decoys" ''
    set -eu
    umask 077
    gen() { echo "HEARTH-CANARY-$(${pkgs.coreutils}/bin/head -c8 /dev/urandom | ${pkgs.coreutils}/bin/od -An -tx1 | ${pkgs.gnused}/bin/sed 's/ //g')"; }
    # world-readable bait: what a shell `cat` or `grep -r` would stumble onto.
    d=/var/lib/hearth/decoys
    ${pkgs.coreutils}/bin/mkdir -p "$d"
    if [ ! -e "$d/billing_api_token.txt" ]; then
      { echo "# internal billing API"; echo "token=$(gen)"; } > "$d/billing_api_token.txt"
      ${pkgs.coreutils}/bin/chmod 0644 "$d/billing_api_token.txt"
    fi
    if [ ! -e "$d/ssh_backup_key" ]; then
      { echo "-----BEGIN OPENSSH PRIVATE KEY-----"; gen; echo "-----END OPENSSH PRIVATE KEY-----"; } > "$d/ssh_backup_key"
      ${pkgs.coreutils}/bin/chmod 0644 "$d/ssh_backup_key"
    fi
    # sudo bait: inside the 0700 secrets dir, only reachable via sudo/root, so a
    # read of this specifically flags privilege abuse (auditd-detected in v2.0).
    s=/var/lib/hearth/secrets
    if [ -d "$s" ] && [ ! -e "$s/decoy-master-key" ]; then
      { echo "master_key=$(gen)"; } > "$s/decoy-master-key"
      ${pkgs.coreutils}/bin/chmod 0600 "$s/decoy-master-key"
    fi
  '';
in
{
  options.hearth.tripwire = {
    enable = lib.mkEnableOption "system-level honeyfile decoys" // { default = true; };
  };

  config = lib.mkIf cfg.enable {
    systemd.tmpfiles.rules = [
      "d /var/lib/hearth/decoys 0755 hearth hearth -"
    ];

    systemd.services.hearth-decoys = {
      description = "plant hearth honeyfile decoys";
      wantedBy = [ "multi-user.target" ];
      after = [ "hearth-audit-init.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "hearth";
        Group = "hearth";
        ExecStart = "${plant}";
      };
    };
  };
}
