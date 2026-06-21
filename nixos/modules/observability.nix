# observability.nix: the audit database, its schema initializer, the hearth-runs
# query, and persistent journald.
{ config, lib, pkgs, ... }:
let
  dbPath = "/var/lib/hearth/runs/audit.db";

  # hearth-runs: print the most recent agent runs in a readable table. Installed
  # on PATH. The spec calls for /usr/local/bin/hearth-runs; on NixOS the
  # idiomatic equivalent is a wrapped script on PATH, with the same command name.
  hearthRuns = pkgs.writeShellScriptBin "hearth-runs" ''
    if [ ! -f "${dbPath}" ]; then
      echo "No run data yet. Start an agent run first (try: sudo systemctl start hearth-demo-agent)."
      exit 0
    fi
    ${pkgs.sqlite}/bin/sqlite3 -header -column "${dbPath}" \
      "SELECT started_at, agent_name, model, tokens_in, tokens_out, latency_ms, cost_usd, error
       FROM agent_runs ORDER BY started_at DESC LIMIT 20;" \
      || echo "No run data yet. Start an agent run first."
  '';
in
{
  config = {
    environment.systemPackages = [ pkgs.sqlite hearthRuns ];

    # The audit schema lives in agent/hearth_agent.py (the single source of
    # truth). This oneshot creates it on boot so the database and table exist
    # before the first agent run. The columns are:
    #   id, agent_name, run_id, started_at, finished_at, tokens_in, tokens_out,
    #   cost_usd, latency_ms, error, model
    systemd.services.hearth-audit-init = lib.mkIf config.hearth.agents.enable {
      description = "Initialize the hearth audit database schema";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "hearth";
        Group = "hearth";
        # Create the database group-writable so sandboxed agents (hearth group)
        # can record their runs and states into it.
        UMask = "0007";
        ExecStart = "${config.hearth.agents.package}/bin/hearth-agent --init-db --db ${dbPath}";
      };
    };

    # Persist journald across reboots and cap disk usage at 2G.
    services.journald.extraConfig = ''
      Storage=persistent
      SystemMaxUse=2G
    '';
  };
}
