# egress.nix: OS-level per-run egress enforcement (v1.4 "Wall").
#
# Design:
#   - Each agent run gets its own nft chain inside a dedicated `table inet
#     hearth`, keyed on the run's cgroupv2 path
#     (system.slice/hearth-agent@<id>.service), so rules apply to exactly one
#     run and to every process it spawns, with no proxy to bypass.
#   - The table coexists with the default NixOS firewall: we do NOT enable
#     networking.nftables (that would switch the firewall backend). The
#     hearth-egress tool loads its own table at runtime with the nft binary,
#     which lives happily alongside the iptables-nft backend.
#   - hearth-egress apply/remove are called from the spawn path (spawn.nix):
#     apply right before the run's loop starts (only when the request carries a
#     non-empty allowed_hosts list; an empty list means allow-all, no rules),
#     remove via ExecStopPost when the run's unit stops.
#   - `hearth-egress watch` is a small bridge daemon: it follows the kernel
#     journal for the drop-log prefix and writes each blocked connection to the
#     egress_log table in /var/lib/hearth/runs/audit.db, so the cockpit can show
#     what the wall actually stopped.
#
# Default off. The blade does not auto-enable this; hosts opt in with
# hearth.egress.enable = true.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.egress;
  agentSrc = ../../agent;

  # nft and journalctl must be on PATH for the python tool's subprocess calls.
  egressBin = pkgs.writeShellApplication {
    name = "hearth-egress";
    runtimeInputs = [ pkgs.python3 pkgs.nftables pkgs.systemd ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_egress.py "$@"
    '';
  };
in
{
  options.hearth.egress = {
    enable = lib.mkEnableOption "OS-level per-run egress enforcement";

    package = lib.mkOption {
      type = lib.types.package;
      internal = true;
      description = "The hearth-egress wrapper, referenced by spawn.nix.";
    };
  };

  config = lib.mkIf cfg.enable {
    hearth.egress.package = egressBin;

    environment.systemPackages = [ egressBin ];

    # The journal-to-audit bridge. Runs as root because following the kernel
    # journal (journalctl -k) requires it; kept minimal beyond that.
    systemd.services.hearth-egress-watch = {
      description = "hearth egress drop-log bridge (kernel journal to egress_log)";
      after = [ "hearth-audit-init.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = "${egressBin}/bin/hearth-egress watch";
        Restart = "on-failure";
        RestartSec = 5;
        Environment = [ "HEARTH_DB=/var/lib/hearth/runs/audit.db" ];
        ProtectSystem = "strict";
        ReadWritePaths = [ "/var/lib/hearth/runs" ];
        NoNewPrivileges = true;
      };
    };

    # Let the operator user (which runs the spawned agents, see spawn.nix) load
    # and tear down per-run rules without a password. Operator already has
    # blanket passwordless sudo via wheel (admin.nix), but this rule is scoped
    # to the exact wrapper binary so egress management keeps working even if
    # the wheel grant is ever tightened.
    security.sudo.extraRules = [
      {
        users = [ "operator" ];
        commands = [
          {
            command = "${egressBin}/bin/hearth-egress";
            options = [ "NOPASSWD" ];
          }
        ];
      }
    ];
  };
}
