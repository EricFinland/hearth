# agents.nix: the /var/lib/hearth layout, agent runtimes, the hearth-agent
# runner, sops-nix stub, and a sandboxed demo agent.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.agents;

  # The agent sources, packaged as a directory so hearth_agent.py can import
  # hearth_state.py (they live side by side). Standard library only, so we just
  # wrap the system python3 around the scripts. They can also be run and tested
  # directly: `python agent/hearth_agent.py --self-test`.
  agentSrc = ../../agent;

  hearthAgent = pkgs.writeShellApplication {
    name = "hearth-agent";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_agent.py "$@"
    '';
  };

  # hearth-loop: the tool-using agent loop (Ollama tool-calling, sandbox-aware).
  hearthLoop = pkgs.writeShellApplication {
    name = "hearth-loop";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_loop.py "$@"
    '';
  };

  # hearth-state: inspect or drive agent runtime state (used by the tycoon map).
  hearthState = pkgs.writeShellApplication {
    name = "hearth-state";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_state.py "$@"
    '';
  };

  # hearth-doctor: one-command health check of the install.
  hearthDoctor = pkgs.writeShellApplication {
    name = "hearth-doctor";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_doctor.py "$@"
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

    loopPackage = lib.mkOption {
      type = lib.types.package;
      internal = true;
      description = "the hearth-loop runner";
    };
  };

  config = lib.mkIf cfg.enable {
    hearth.agents.package = hearthAgent;
    hearth.agents.loopPackage = hearthLoop;

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

    # Base agent runtimes plus the hearth-agent runner and hearth-state CLI.
    # The dev toolchain (git, gcc, gnumake) lets sandboxed agents build code.
    environment.systemPackages = with pkgs; [
      python3
      uv
      nodejs_22
      git
      gcc
      gnumake
      hearthAgent
      hearthLoop
      hearthState
      hearthDoctor
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
