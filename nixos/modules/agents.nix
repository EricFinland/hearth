# agents.nix: the /var/lib/hearth layout, agent runtimes, the hearth-agent
# runner, sops-nix stub, and a sandboxed demo agent.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.agents;

  # The agent runner, packaged from agent/hearth_agent.py. It is standard
  # library only, so we just wrap the system python3 around the script. Keeping
  # the source as a real .py file (not an inline string) means it can be run and
  # tested directly: `python agent/hearth_agent.py --self-test`.
  hearthAgent = pkgs.writeShellApplication {
    name = "hearth-agent";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${../../agent/hearth_agent.py} "$@"
    '';
  };
in
{
  options.hearth.agents = {
    enable = lib.mkEnableOption "hearth agent runtime" // { default = true; };

    package = lib.mkOption {
      type = lib.types.package;
      internal = true;
      description = "The hearth-agent runner package, referenced by other modules.";
    };
  };

  config = lib.mkIf cfg.enable {
    hearth.agents.package = hearthAgent;

    # Directory layout under /var/lib/hearth, created on boot by tmpfiles.
    # Format: "d  <path>  <mode>  <user>  <group>  <age>"
    # agents and runs are 2770 (group writable, setgid) so a sandboxed agent
    # running as a DynamicUser in the hearth supplementary group can write its
    # working files and audit records there. See modules/sandbox.nix.
    systemd.tmpfiles.rules = [
      "d /var/lib/hearth         0750 hearth hearth -"
      "d /var/lib/hearth/agents  2770 hearth hearth -" # agent working directories
      "d /var/lib/hearth/models  0750 hearth hearth -" # model storage (see llm.nix)
      "d /var/lib/hearth/logs    0750 hearth hearth -" # agent run logs
      "d /var/lib/hearth/secrets 0700 hearth hearth -" # decrypted sops-nix secrets land here
      "d /var/lib/hearth/runs    2770 hearth hearth -" # per-run audit records (see observability.nix)
    ];

    # sops-nix integration stub. Real secret definitions go through the sops-nix
    # module (sops.secrets.<name>), with decrypted values targeted at
    # /var/lib/hearth/secrets (mode 0700). See docs/DECISIONS.md (ADR-003).
    environment.etc."hearth/sops.yaml".text = ''
      # Placeholder sops configuration for hearth.
      # Replace with a real .sops.yaml creation rule set. See docs/DECISIONS.md.
      # Example structure:
      #   creation_rules:
      #     - path_regex: secrets/.*\.yaml$
      #       age: <your-age-public-key>
    '';

    # Base agent runtimes plus the hearth-agent runner on PATH.
    environment.systemPackages = with pkgs; [
      python3
      uv
      nodejs_22
      hearthAgent
    ];

    # A demonstration agent that runs under the full sandbox profile and records
    # its run to the audit database. Manual start (no wantedBy), since it needs
    # Ollama up and a model pulled first:
    #   sudo systemctl start hearth-demo-agent
    #   journalctl -u hearth-demo-agent
    #   hearth-runs
    systemd.services.hearth-demo-agent = {
      description = "hearth demonstration agent (sandboxed, audited)";
      after = [ "ollama.service" ];
      serviceConfig = config.hearth.sandbox.profile // {
        Type = "oneshot";
        ExecStart =
          "${hearthAgent}/bin/hearth-agent --agent-name demo "
          + "--model llama3.2:3b 'Reply with a five word greeting.'";
      };
    };
  };
}
