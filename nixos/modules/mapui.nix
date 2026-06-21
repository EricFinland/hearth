# mapui.nix: the tycoon map backend (hearth-mapd) and its web port.
#
# hearth-mapd serves the map page and streams agent runtime state to the browser
# (see webui/). It reads the live state the agent runtime writes via
# agent/hearth_state.py. It never contacts an LLM, so the UI costs zero tokens.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.mapui;

  # Packaged as a directory so the server finds its static/ page next to itself
  # (hearth_mapd.py defaults its static dir to <script dir>/static).
  webuiSrc = ../../webui;

  hearthMapd = pkgs.writeShellApplication {
    name = "hearth-mapd";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${webuiSrc}/hearth_mapd.py "$@"
    '';
  };
in
{
  options.hearth.mapui = {
    enable = lib.mkEnableOption "the hearth tycoon map web UI" // { default = true; };
    port = lib.mkOption {
      type = lib.types.port;
      default = 8770;
      description = "TCP port hearth-mapd listens on.";
    };
    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Open the map port on the firewall so other devices on your network can
        view it. For a tighter setup, set this false and reach the map over
        Tailscale only (the tailscale0 interface is already trusted).
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ hearthMapd ];

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];

    systemd.services.hearth-mapd = {
      description = "hearth tycoon map backend";
      after = [ "network.target" "hearth-audit-init.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = "${hearthMapd}/bin/hearth-mapd --host 0.0.0.0 --port ${toString cfg.port} --db /var/lib/hearth/runs/audit.db";
        User = "hearth";
        Group = "hearth";
        Restart = "on-failure";
        # Light hardening. The service only reads the audit DB and serves files.
        NoNewPrivileges = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        # SQLite in WAL mode writes -wal/-shm sidecars even for readers, so the
        # runs dir must be writable (the hearth user owns it anyway).
        ReadWritePaths = [ "/var/lib/hearth/runs" ];
        PrivateTmp = true;
      };
    };
  };
}
