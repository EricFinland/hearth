# observability.nix: the audit daemon stub, the run store, and journald persistence.
{ config, lib, pkgs, ... }:
let
  # hearth-runs: quick query of the most recent agent runs. Installed on PATH.
  # The spec calls for /usr/local/bin/hearth-runs; on NixOS the idiomatic
  # equivalent is a wrapped script on PATH, which is what writeShellScriptBin
  # produces. The command name is the same: `hearth-runs`.
  hearthRuns = pkgs.writeShellScriptBin "hearth-runs" ''
    ${pkgs.sqlite}/bin/sqlite3 /var/lib/hearth/runs/audit.db \
      "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 20;" 2>/dev/null \
      || echo "No run data yet. Start an agent run first."
  '';
in
{
  config = {
    environment.systemPackages = [ pkgs.sqlite hearthRuns ];

    # Audit run store schema (created by the real audit daemon, documented here):
    #
    #   CREATE TABLE agent_runs (
    #     id          INTEGER PRIMARY KEY,
    #     agent_name  TEXT,
    #     run_id      TEXT,
    #     started_at  TEXT,
    #     finished_at TEXT,
    #     tokens_in   INTEGER,
    #     tokens_out  INTEGER,
    #     cost_usd    REAL,
    #     latency_ms  INTEGER,
    #     error       TEXT,
    #     model       TEXT
    #   );
    #
    # The database lives at /var/lib/hearth/runs/audit.db.

    systemd.tmpfiles.rules = [
      "d /var/lib/hearth/bin 0750 hearth hearth -"
    ];

    # Stub audit daemon script. Replace with the real aggregation daemon that
    # tails agent logs and writes rows into agent_runs.
    environment.etc."hearth/audit-daemon".source = pkgs.writeShellScript "hearth-audit-daemon" ''
      echo "hearth-audit daemon starting"
      # TODO: replace with the real audit aggregation loop that reads agent run
      # logs from /var/lib/hearth/logs and writes rows into the SQLite store at
      # /var/lib/hearth/runs/audit.db.
      while true; do
        sleep 3600
      done
    '';

    systemd.services.hearth-audit = {
      description = "hearth audit aggregation daemon (stub)";
      after = [ "multi-user.target" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "simple";
        # The daemon script is installed at /etc/hearth/audit-daemon and copied
        # to /var/lib/hearth/bin/audit-daemon, which is the path the spec uses.
        ExecStartPre = "${pkgs.coreutils}/bin/install -m0755 /etc/hearth/audit-daemon /var/lib/hearth/bin/audit-daemon";
        ExecStart = "/var/lib/hearth/bin/audit-daemon";
        Restart = "on-failure";
        User = "hearth";
        Group = "hearth";
      };
    };

    # Persist journald across reboots and cap disk usage at 2G.
    services.journald.extraConfig = ''
      Storage=persistent
      SystemMaxUse=2G
    '';
  };
}
