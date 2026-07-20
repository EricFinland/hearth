# schedule.nix: the standing-missions scheduler.
#
# A systemd timer periodically runs `hearth-schedule --tick`, which reads the
# mission registry, dispatches any that are due (by dropping a queue file for the
# normal spawn path), and records when each last ran. This is the "works while
# you sleep" layer. Gated behind hearth.schedule.enable (default off; the blade
# turns it on).
#
# v1.5 adds declarative missions: hearth.schedule.missions renders to
# /etc/hearth/missions.json, a pure-config JSON list the scheduler merges with
# the cockpit-managed registry (last-run state lives in a sidecar, never here).
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.schedule;
  agentSrc = ../../agent;

  # "every:N" -> {"every_minutes": N}; anything else is a daily "HH:MM" time.
  parseSchedule = s:
    if lib.strings.hasPrefix "every:" s
    then { every_minutes = lib.toInt (lib.removePrefix "every:" s); }
    else { at = s; };

  # attrsOf submodule -> JSON list; the attr name becomes the mission name.
  #
  # Example rendered missions.json for
  #   missions.digest = { schedule = "07:30"; prompt = "morning digest"; };
  #   missions.probe  = { schedule = "every:15"; kind = "swarm"; prompt = "probe"; };
  # (field order is alphabetical because Nix attrsets are sorted):
  #   [{"allowed_hosts":[],"creds":[],"enabled":true,"kind":"agent","model":"",
  #     "name":"digest","prompt":"morning digest","schedule":{"at":"07:30"},
  #     "tools":[]},
  #    {"allowed_hosts":[],"creds":[],"enabled":true,"kind":"swarm","model":"",
  #     "name":"probe","prompt":"probe","schedule":{"every_minutes":15},
  #     "tools":[]}]
  missionList = lib.mapAttrsToList (name: m: {
    inherit name;
    kind = m.kind;
    model = m.model;
    prompt = m.prompt;
    schedule = parseSchedule m.schedule;
    tools = m.tools;
    allowed_hosts = m.allowedHosts;
    creds = m.creds;
    enabled = m.enabled;
  }) cfg.missions;
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
    missions = lib.mkOption {
      type = lib.types.attrsOf (lib.types.submodule {
        options = {
          schedule = lib.mkOption {
            type = lib.types.str;
            description = ''"HH:MM" for a daily run at that time, or "every:N" for every N minutes.'';
          };
          kind = lib.mkOption {
            type = lib.types.enum [ "agent" "swarm" "marathon" ];
            default = "agent";
            description = "Run shape for the mission.";
          };
          model = lib.mkOption {
            type = lib.types.str;
            default = "";
            description = "Model to use; empty picks the scheduler default.";
          };
          prompt = lib.mkOption {
            type = lib.types.str;
            description = "Prompt handed to the run.";
          };
          tools = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Tool allowlist; empty means the default toolset.";
          };
          allowedHosts = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Egress host allowlist; empty means allow-all.";
          };
          creds = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Named credentials the run may read.";
          };
          enabled = lib.mkOption {
            type = lib.types.bool;
            default = true;
            description = "Whether the scheduler dispatches this mission.";
          };
        };
      });
      default = { };
      description = ''
        Declarative standing missions, rendered to /etc/hearth/missions.json and
        merged by the scheduler with the cockpit-managed registry. The attribute
        name becomes the mission name.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ schedBin ];

    # Pure config: the scheduler keeps last-run state in a sidecar, so this file
    # can be regenerated on every rebuild. Only rendered when missions are
    # actually declared, so an empty option leaves /etc clean.
    environment.etc."hearth/missions.json" = lib.mkIf (cfg.missions != { }) {
      text = builtins.toJSON missionList;
    };

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
