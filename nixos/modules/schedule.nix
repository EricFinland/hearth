# schedule.nix: the standing-missions scheduler.
#
# A systemd timer periodically runs `hearth-schedule --tick`, which reads the
# mission registry, dispatches any that are due (by dropping a queue file for the
# normal spawn path), and records when each last ran. This is the "works while
# you sleep" layer. Gated behind hearth.schedule.enable (default off; the blade
# turns it on).
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.schedule;
  agentSrc = ../../agent;
  schedBin = pkgs.writeShellApplication {
    name = "hearth-schedule";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_schedule.py "$@"
    '';
  };
in
{
  options.hearth.schedule = {
    enable = lib.mkEnableOption "the standing-missions scheduler";
    interval = lib.mkOption {
      type = lib.types.str;
      default = "*:0/10";
      description = "systemd OnCalendar spec for how often to check for due missions.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ schedBin ];

    # Registry dir writable by operator (who runs both the scheduler and mapd).
    systemd.tmpfiles.rules = [
      "d /var/lib/hearth/scheduler 0770 operator users -"
    ];

    systemd.services.hearth-schedule = {
      description = "hearth standing-missions scheduler tick";
      after = [ "network.target" "hearth-audit-init.service" ];
      # Runs as operator so it can write the queue + registry and kick the spawn
      # service via sudo, like the other agent paths.
      serviceConfig = {
        Type = "oneshot";
        User = "operator";
        Group = "users";
        NoNewPrivileges = false;
        ExecStart = "${schedBin}/bin/hearth-schedule --tick";
      };
    };

    systemd.timers.hearth-schedule = {
      description = "run the hearth scheduler tick periodically";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.interval;
        Persistent = true;  # catch up a missed tick after downtime
      };
    };
  };
}
